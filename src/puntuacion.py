"""R5 · Puntuación explicable: regresión logística. Dueño: Ranking.

    python -m src.puntuacion          # entrena (o lee lo ya descargado de data/), enseña coeficientes y validación

En vez de sumar puntos "a ojo", un modelo pequeño aprende cuánto vale cada señal para que un producto ENSEÑADO
en el carrusel se compre. Es una sola neurona con sigmoide:  P(compra) = 1 / (1 + e^-(b + w1·x1 + w2·x2 + ...)).

- Datos: visitas al carrusel antiguo (shop.history_sessions) entre INICIO y CORTE. Cada visita da 10 filas, una
  por producto enseñado; etiqueta = 1 si compró ese producto.
- Las señales (features) existen también para productos y compradores NUEVOS: nunca se usa el product_id.
- Las piezas (co-visitas, comprados, populares) se calculan solo con datos ANTERIORES a INICIO: así el modelo
  aprende con la misma información que tendría en vivo y no mira el futuro.
- Sesgo del carrusel antiguo: enseñaba sobre todo lo popular. Cada fila pesa 1/propensity (recortado a
  RECORTE_PESO) para que lo que casi nunca se enseñaba cuente lo suyo (IPS, docs/investigacion.md punto 3).
- Sesgo de posición: lo de arriba se compra más solo por estar arriba. El modelo lleva una señal por puesto
  (2º...10º) durante el entrenamiento; al recomendar se quita, porque queremos saber cuánto vale el PRODUCTO.
- Validación (chequeo de Ranking, no la evaluación oficial): las 100.000 visitas de test de Evaluación
  (posteriores a CORTE), con el Estado de data/estado_<CORTE>.pkl.

La regresión se resuelve con numpy (método de Newton), sin dependencias nuevas. El resultado (coeficientes) se
guarda en data/puntuacion.json (data/ no se sube al repo).
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import numpy as np

from src import catalogo, covisitas, usuarios
from src.estado import Estado, por_categoria
from src.rawtree import query

DATA = Path(__file__).resolve().parent.parent / "data"
INICIO, CORTE = "2026-10-24", "2026-11-07"
"""Se entrena con las dos semanas anteriores al corte de evaluación."""
UNA_DE_CADA_CON_COMPRA, UNA_DE_CADA_SIN_COMPRA = 1, 10
"""Las compras son raras (6.545 de 466.000 visitas en esas dos semanas, ~1,4 %): se cogen TODAS las visitas con
compra y 1 de cada 10 sin compra. Para compensar, cada visita sin compra pesa 10 veces más."""
RECORTE_PESO = 50.0
L2 = 1.0
"""Freno para que ningún coeficiente se dispare (regularización)."""
DECAIMIENTO = 0.7

SENALES = [
    ("log_precio", "logaritmo del precio"),
    ("misma_categoria", "misma categoría que lo último visto"),
    ("misma_subcategoria", "misma subcategoría que lo último visto"),
    ("misma_marca", "misma marca que algo de lo visto"),
    ("revisita", "ya lo ha visto en esta sesión"),
    ("covisita", "log(1 + puntos de co-visita con lo visto)"),
    ("popularidad", "-log(1 + puesto en los más vendidos)"),
    ("popularidad_categoria", "-log(1 + puesto en los más vendidos de su categoría)"),
    ("distancia_precio", "|log precio - log precio medio de lo visto|"),
    ("frio_x_popularidad", "popularidad, solo si no hay vistas (comprador frío)"),
    ("movil_x_log_precio", "log precio, solo si entra desde el móvil (ios/android)"),
]
NOMBRES = [n for n, _ in SENALES]
PUESTOS = [f"puesto_{i}" for i in range(2, 11)]


# ---------------------------------------------------------------- señales de cada candidato
class _Indices:
    """Puestos de cada producto en las listas de populares (se calcula una vez por Estado)."""

    def __init__(self, estado: Estado):
        self.global_ = {pid: i for i, pid in enumerate(estado.populares)}
        self.categoria = {pid: i for lista in estado.populares_categoria.values() for i, pid in enumerate(lista)}
        self.n = len(estado.populares) + 1


_cache: dict[int, _Indices] = {}


def _indices(estado: Estado) -> _Indices:
    if id(estado) not in _cache:
        _cache.clear()
        _cache[id(estado)] = _Indices(estado)
    return _cache[id(estado)]


def _log_precio(datos: dict) -> float:
    try:
        return math.log(max(float(datos.get("price_eur") or 0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def senales(peticion: dict, candidatos: list[str], estado: Estado) -> np.ndarray:
    """Una fila por candidato con las señales de SENALES (sin puestos). Solo lee `estado`, como recommend()."""
    cat = estado.catalogo
    idx = _indices(estado)
    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    ultimo = cat.get(vistos[-1], {}) if vistos else {}
    marcas = {cat.get(v, {}).get("brand") for v in vistos} - {None, ""}
    precios = [_log_precio(cat.get(v, {})) for v in vistos]
    precio_medio = sum(precios) / len(precios) if precios else 0.0
    ctx = peticion.get("context") or {}
    movil = float(str(ctx.get("device", "")) in ("ios", "android"))
    frio = float(not vistos)

    covis: dict[str, float] = {}
    for k, pid in enumerate(reversed(vistos)):
        for pos, (otro, peso) in enumerate(estado.covisitas.get(pid, [])):
            covis[otro] = covis.get(otro, 0.0) + (DECAIMIENTO ** k) * peso / (pos + 1)

    vistos_set = set(vistos)
    filas = []
    for c in candidatos:
        d = cat.get(c, {})
        lp = _log_precio(d)
        pop = -math.log1p(idx.global_.get(c, idx.n))
        filas.append([
            lp,
            float(bool(vistos) and d.get("category") == ultimo.get("category")),
            float(bool(vistos) and d.get("subcategory") == ultimo.get("subcategory")),
            float(d.get("brand") in marcas),
            float(c in vistos_set),
            math.log1p(covis.get(c, 0.0)),
            pop,
            -math.log1p(idx.categoria.get(c, idx.n)),
            abs(lp - precio_medio) if precios else 0.0,
            frio * pop,
            movil * lp,
        ])
    return np.asarray(filas, dtype=np.float64)


def _puestos(n_filas: int) -> np.ndarray:
    """Señal de puesto (2º..10º) para las 10 filas de una visita; el 1º es la referencia (todo ceros)."""
    m = np.zeros((n_filas, len(PUESTOS)))
    for i in range(1, min(n_filas, 10)):
        m[i, i - 1] = 1.0
    return m


# ---------------------------------------------------------------- usar el modelo (lo llama ranking.recommend)
_modelo: dict | None = None
_modelo_leido = False


def modelo_guardado() -> dict | None:
    """Coeficientes de data/puntuacion.json, leídos UNA vez (la primera llamada). None si no existe el archivo:
    entonces recommend() sigue sin modelo. Para crearlo: python -m src.puntuacion."""
    global _modelo, _modelo_leido
    if not _modelo_leido:
        _modelo_leido = True
        try:
            m = json.loads((DATA / "puntuacion.json").read_text())
            m["_media"], m["_desv"] = np.asarray(m["media"]), np.asarray(m["desviacion"])
            m["_coef"] = np.asarray(m["coeficientes"])
            _modelo = m if m.get("senales") == NOMBRES else None  # señales distintas = archivo viejo: no usar
        except (OSError, ValueError, KeyError):
            _modelo = None
        if _modelo is None:  # se ve en la consola del servicio: sin esto, usar_modelo se ignoraría en silencio
            print("AVISO puntuacion: no hay data/puntuacion.json válido -> usar_modelo NO hace nada. "
                  "Ejecuta: python -m src.puntuacion", flush=True)
    return _modelo


def puntuar(peticion: dict, candidatos: list[str], estado: Estado, modelo: dict) -> np.ndarray:
    """Puntuación de cada candidato (más alta = más probable que lo compre). Sin puestos: solo el producto.
    Es la parte de dentro de la sigmoide; para ordenar no hace falta aplicarla."""
    return (senales(peticion, candidatos, estado) - modelo["_media"]) / modelo["_desv"] @ modelo["_coef"]


# ---------------------------------------------------------------- datos
_SQL = """
SELECT user_id::String AS user_id, `context.device`::String AS device, recent_views, shown, propensity,
       purchased::String AS purchased
FROM shop.history_sessions
WHERE ts::DateTime64(3) >= '{inicio}' AND ts::DateTime64(3) < '{fin}'
  AND if(purchased::String != '', cityHash64(request_id::String) % {con} = 0,
                                  cityHash64(request_id::String) % {sin} = 0)"""


def _visitas_entreno() -> list[dict]:
    ruta = DATA / f"entreno_{INICIO}_{CORTE}.pkl"
    if ruta.exists():
        return pickle.loads(ruta.read_bytes())
    print("Descargando visitas de entrenamiento (solo la primera vez)...")
    filas = query(_SQL.format(inicio=INICIO, fin=CORTE, con=UNA_DE_CADA_CON_COMPRA, sin=UNA_DE_CADA_SIN_COMPRA),
                  timeout=600)
    visitas = [{"user_id": f["user_id"], "context": {"device": f["device"]}, "recent_views": f["recent_views"] or [],
                "shown": f["shown"], "propensity": f["propensity"], "purchased": f["purchased"] or None}
               for f in filas]
    DATA.mkdir(exist_ok=True)
    ruta.write_bytes(pickle.dumps(visitas))
    return visitas


def _estado_entreno() -> Estado:
    """Piezas calculadas solo con datos anteriores a INICIO."""
    cat = catalogo.cargar_catalogo(INICIO)
    pop = catalogo.cargar_populares(INICIO)
    return Estado(catalogo=cat, disponibles=catalogo.disponibles(cat), populares=pop,
                  populares_categoria=por_categoria(cat, pop), covisitas=covisitas.cargar_covisitas(INICIO),
                  comprados=usuarios.cargar_comprados(INICIO))


def _matriz(visitas: list[dict], estado: Estado, ponderar: bool):
    """X (señales + puestos), y (¿compró?), w (peso de cada fila) y a qué visita pertenece cada fila.

    Recompras FUERA (los organizadores: en vivo no hay recompras; en el historial son ~62 % de las compras y el
    modelo aprendería ese artefacto): se descartan las visitas cuya compra es algo que el comprador ya tenía, y en
    las demás, las filas de productos que ya tenía (en vivo no se le enseñan). Devuelve también cuántas visitas
    se descartaron."""
    bloques, ys, ws, grupo = [], [], [], []
    descartadas = 0
    for g, v in enumerate(visitas):
        tenia = estado.comprados.get(str(v.get("user_id", "")), set())
        if v["purchased"] and v["purchased"] in tenia:
            descartadas += 1
            continue
        shown = list(v["shown"])
        quedan = [i for i, p in enumerate(shown) if p not in tenia]
        if not quedan:
            continue
        x = np.hstack([senales(v, shown, estado), _puestos(len(shown))])[quedan]  # el puesto original se conserva
        bloques.append(x)
        ys.extend(float(shown[i] == v["purchased"]) for i in quedan)
        base = 1.0 if (not ponderar or v["purchased"]) else UNA_DE_CADA_SIN_COMPRA / UNA_DE_CADA_CON_COMPRA
        ws.extend(base * (min(1.0 / max(float(v["propensity"][i]), 1e-9), RECORTE_PESO) if ponderar else 1.0)
                  for i in quedan)
        grupo.extend([g] * len(quedan))
    return np.vstack(bloques), np.asarray(ys), np.asarray(ws), np.asarray(grupo), descartadas


# ---------------------------------------------------------------- la regresión (Newton con numpy)
def entrenar(X: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = L2, iteraciones: int = 30) -> np.ndarray:
    """Regresión logística ponderada. Devuelve [b, w1, w2, ...]. Newton: en cada paso se aproxima la pérdida por
    una parábola y se salta a su mínimo; con ~20 señales converge en pocas vueltas."""
    X1 = np.hstack([np.ones((len(X), 1)), X])
    beta = np.zeros(X1.shape[1])
    freno = np.full(X1.shape[1], l2)
    freno[0] = 0.0  # al término independiente no se le pone freno
    for _ in range(iteraciones):
        p = 1.0 / (1.0 + np.exp(-np.clip(X1 @ beta, -30, 30)))
        gradiente = X1.T @ (w * (p - y)) + freno * beta
        hessiana = np.zeros((X1.shape[1], X1.shape[1]))
        for i in range(0, len(X1), 500_000):  # por trozos para no llenar la memoria
            t = X1[i:i + 500_000]
            hessiana += (t * (w[i:i + 500_000] * p[i:i + 500_000] * (1 - p[i:i + 500_000]))[:, None]).T @ t
        paso = np.linalg.solve(hessiana + np.diag(freno), gradiente)
        beta -= paso
        if np.max(np.abs(paso)) < 1e-6:
            break
    return beta


def auc(puntos: np.ndarray, y: np.ndarray) -> float:
    """Probabilidad de que una fila comprada puntúe más que una no comprada (0,5 = azar, 1 = perfecto)."""
    orden = np.argsort(puntos, kind="mergesort")
    rangos = np.empty(len(puntos))
    rangos[orden] = np.arange(1, len(puntos) + 1)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((rangos[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ---------------------------------------------------------------- principal
def main() -> None:
    visitas = _visitas_entreno()
    compras = sum(bool(v["purchased"]) for v in visitas)
    print(f"Entrenamiento {INICIO} -> {CORTE}: {len(visitas)} visitas ({compras} con compra)")
    estado = _estado_entreno()
    X, y, w, _, fuera = _matriz(visitas, estado, ponderar=True)
    print(f"Descartadas {fuera} visitas cuya compra era una recompra; quedan {int(y.sum())} compras para aprender")
    media = X[:, :len(NOMBRES)].mean(axis=0)
    desv = X[:, :len(NOMBRES)].std(axis=0) + 1e-9
    Xn = X.copy()
    Xn[:, :len(NOMBRES)] = (X[:, :len(NOMBRES)] - media) / desv  # misma escala: coeficientes comparables
    beta = entrenar(Xn, y, w)

    print(f"\n{'señal':<24}{'coef.':>8}   ×odds   qué es")
    for (nombre, texto), b in zip(SENALES + [(p, "") for p in PUESTOS], beta[1:]):
        print(f"{nombre:<24}{b:>8.3f}  {math.exp(b):>6.2f}   {texto}")

    modelo = {"inicio": INICIO, "corte": CORTE, "senales": NOMBRES, "media": media.tolist(),
              "desviacion": desv.tolist(), "termino": float(beta[0]),
              "coeficientes": beta[1:1 + len(NOMBRES)].tolist(), "puestos": beta[1 + len(NOMBRES):].tolist()}
    (DATA / "puntuacion.json").write_text(json.dumps(modelo, indent=1))

    # Diagnóstico del propio modelo (la comparación con otras versiones la hace Evaluación con src/evaluar.py):
    # AUC en visitas posteriores al corte, con el Estado de ese corte y el mismo filtro de recompras.
    test = pickle.loads((DATA / f"test_{CORTE}.pkl").read_bytes())
    est = pickle.loads((DATA / f"estado_{CORTE}.pkl").read_bytes())
    Xt, yt, _, _, fuera_t = _matriz(test, est, ponderar=False)
    s = (Xt[:, :len(NOMBRES)] - media) / desv @ beta[1:1 + len(NOMBRES)]  # sin puestos: solo el producto
    print(f"\nDiagnóstico {CORTE}+: {len(test)} visitas, {fuera_t} descartadas por recompra, {int(yt.sum())} compras")
    print(f"AUC del modelo (sin puestos): {auc(s, yt):.3f}  (0,5 = azar, 1 = perfecto)")


if __name__ == "__main__":
    main()
