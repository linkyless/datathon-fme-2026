"""e-samen-v2 · Modelo de ELECCIÓN: "de los 10 que le enseñamos, ¿cuál compra?". Dueño: Operador (Lester).

    python -m src.eleccion --descargar   # baja de RawTree las 28.053 compras del carrusel e impresiones (~30 s)
    python -m src.eleccion               # entrena, valida contra la v0 y guarda data/eleccion.json

La idea, para un product manager:
- En el historial del carrusel tenemos 28.053 compras. En cada una sabemos los 10 productos enseñados, su orden y
  cuál se compró. Un "logit condicional" (la misma neurona de R5, pero comparando SOLO entre los 10 de cada visita)
  aprende qué hace que un producto gane a sus 9 vecinos de carrusel.
- Comparar dentro de lo enseñado evita el sesgo del carrusel antiguo (enseñaba lo popular): lo popular solo gana si
  de verdad se elige más que lo que tenía al lado. La posición entra como señal al entrenar y se quita al recomendar.
- Señal nueva clave: la CALIDAD de cada producto = compras que tuvo / compras que "le tocaban" por las posiciones en
  que salió (si salió mucho en el puesto 1, se esperaba que se comprara más). Generaliza mejor que la popularidad.
- Señales de precio relativo a lo visto (medido: se compra a ~0,55× el precio de lo último visto), subcategoría,
  marca y re-visita. Nunca el product_id directamente (salvo a través de la calidad suavizada).
- Las compras de productos que el comprador YA tenía (recompras) se quitan de entrenamiento y de validación.

Validación honesta (no mira el futuro): calidad y popularidad con datos ANTERIORES a CORTE; se valida con las compras
posteriores. Métrica: acierto del 1º (¿el comprado es el que más puntúa de los 10?) y log-verosimilitud media.
"""
from __future__ import annotations

import json
import math
import pickle
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
CORTE = "2026-11-07"
INICIO_ENTRENO = "2026-10-01"
SUAVIZADO = 5.0
"""Calidad = (compras + 5·μ) / (esperadas + 5), con μ = calidad TÍPICA de un producto bien medido (mediana de los que
tienen ≥ 500 impresiones). Un producto con pocas impresiones se queda cerca de lo típico, no de 1: suavizar hacia 1
hacía que lo casi nunca enseñado (1.845 productos) pareciera mejor que lo probado (medido: típico ≈ e^-0,8)."""
_MU: dict = {}
L2 = 1.0

SENALES = [
    "log_calidad", "popularidad", "misma_categoria", "misma_subcategoria", "misma_marca", "sub_en_vistas",
    "log_ratio_precio", "log_ratio_precio2", "log_precio", "revisita", "frio", "frio_x_calidad",
    "frio_x_popularidad", "frio_x_log_precio",
]
PUESTOS = [f"puesto_{i}" for i in range(2, 11)]


# ---------------------------------------------------------------- piezas (calidad y popularidad)
def sesgo_posicion(imp: list[dict], ses: list[dict], antes: bool | None) -> np.ndarray:
    """Compras por impresión en cada puesto 1..10 (lo que vale estar en ese puesto)."""
    n_imp = np.zeros(11)
    for f in imp:
        if antes is None or bool(f["antes"]) == antes:
            n_imp[int(f["pos"])] += float(f["n"])
    n_com = np.zeros(11)
    for s in ses:
        if antes is None or (s["ts"] < CORTE) == antes:
            if s["p"] in s["shown"]:
                n_com[s["shown"].index(s["p"]) + 1] += 1
    return n_com / np.maximum(n_imp, 1)


def calidades(imp: list[dict], ses: list[dict], antes: bool | None) -> tuple[dict, dict]:
    """product_id -> (compras, compras esperadas por sus posiciones). antes=True: solo datos < CORTE; None: todo."""
    sp = sesgo_posicion(imp, ses, antes)
    esperadas: dict[str, float] = defaultdict(float)
    for f in imp:
        if antes is None or bool(f["antes"]) == antes:
            esperadas[f["pid"]] += float(f["n"]) * sp[int(f["pos"])]
    compras: Counter = Counter()
    for s in ses:
        if antes is None or (s["ts"] < CORTE) == antes:
            compras[s["p"]] += 1
    return dict(compras), dict(esperadas)


def calidad_tipica(compras: dict, esperadas: dict) -> float:
    clave = (id(compras), id(esperadas))
    if clave not in _MU:
        r = [(compras.get(p, 0) + 0.5) / (e + 0.5) for p, e in esperadas.items() if e >= 5.0]
        _MU.clear()
        _MU[clave] = float(np.median(r)) if r else 1.0
    return _MU[clave]


def log_calidad(pid: str, compras: dict, esperadas: dict, quitar: int = 0) -> float:
    mu = calidad_tipica(compras, esperadas)
    return math.log((compras.get(pid, 0) - quitar + SUAVIZADO * mu) / (esperadas.get(pid, 0.0) + SUAVIZADO))


def rangos_popularidad(primeras: list[dict], hasta: str | None) -> dict[str, int]:
    """Puesto de cada producto por nº de compradores distintos (0 = el más vendido)."""
    c = Counter(f["p"] for f in primeras if hasta is None or f["t"] < hasta)
    return {pid: i for i, (pid, _) in enumerate(c.most_common())}


# ---------------------------------------------------------------- señales de un candidato
def senales(cand: list[str], vistos: list[str], cat: dict, lc: dict[str, float], rango: dict[str, int]) -> np.ndarray:
    """Matriz (len(cand), len(SENALES)). `lc` = log_calidad por producto (ya calculada)."""
    n_pop = len(rango) + 1
    ultimo = cat.get(vistos[-1], {}) if vistos else {}
    frio = 0.0 if vistos else 1.0
    p_ult = _precio(ultimo)
    subs_vistas = {cat.get(v, {}).get("subcategory") for v in vistos} - {None, ""}
    vistos_set = set(vistos)
    X = np.zeros((len(cand), len(SENALES)))
    for k, pid in enumerate(cand):
        d = cat.get(pid, {})
        q = lc.get(pid, 0.0)
        pop = -math.log1p(rango.get(pid, n_pop))
        lp = math.log(max(_precio(d), 1.0))
        lr = math.log(max(_precio(d), 1.0) / max(p_ult, 1.0)) if vistos else 0.0
        X[k] = (q, pop,
                float(bool(ultimo) and d.get("category") == ultimo.get("category")),
                float(bool(ultimo) and d.get("subcategory") == ultimo.get("subcategory")),
                float(bool(ultimo) and d.get("brand") == ultimo.get("brand")),
                float(d.get("subcategory") in subs_vistas),
                lr, lr * lr, lp, float(pid in vistos_set), frio, frio * q, frio * pop, frio * lp)
    return X


def _precio(d: dict) -> float:
    try:
        return float(d.get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- entrenamiento (logit condicional)
def _vistos(s: dict) -> list[str]:
    return [v["product_id"] for v in (s["rv"] or []) if v.get("product_id")]


def _es_recompra(s: dict, primera: dict) -> bool:
    t = primera.get((s["u"], s["p"]))
    return t is not None and t < s["ts"]


def preparar(sesiones, cat, compras, esperadas, rango, con_puesto, dejar_uno_fuera):
    """Lista de (X 10×k, índice del comprado)."""
    filas = []
    for s in sesiones:
        if s["p"] not in s["shown"]:
            continue
        lc = {pid: log_calidad(pid, compras, esperadas, quitar=int(dejar_uno_fuera and pid == s["p"]))
              for pid in s["shown"]}
        X = senales(s["shown"], _vistos(s), cat, lc, rango)
        if con_puesto:
            P = np.zeros((len(s["shown"]), len(PUESTOS)))
            for j in range(1, len(s["shown"])):
                P[j, j - 1] = 1.0
            X = np.hstack([X, P])
        filas.append((X, s["shown"].index(s["p"])))
    return filas


def entrenar(filas, columnas: list[int] | None = None, iters: int = 30) -> np.ndarray:
    """Newton sobre la log-verosimilitud del softmax dentro de cada visita, con freno L2."""
    k = filas[0][0].shape[1] if columnas is None else len(columnas)
    w = np.zeros(k)
    for _ in range(iters):
        g = -L2 * w
        H = -L2 * np.eye(k)
        for X, y in filas:
            Xc = X if columnas is None else X[:, columnas]
            z = Xc @ w
            p = np.exp(z - z.max())
            p /= p.sum()
            media = p @ Xc
            g += Xc[y] - media
            H -= (Xc * p[:, None]).T @ Xc - np.outer(media, media)
        paso = np.linalg.solve(H, g)
        w -= paso
        if np.abs(paso).max() < 1e-6:
            break
    return w


def validar(filas, w, columnas=None) -> dict:
    acierto = ll = top3 = 0.0
    for X, y in filas:
        Xc = X if columnas is None else X[:, columnas]
        z = Xc @ w
        p = np.exp(z - z.max())
        p /= p.sum()
        orden = np.argsort(-z, kind="stable")
        acierto += orden[0] == y
        top3 += y in orden[:3]
        ll += math.log(max(p[y], 1e-12))
    n = max(len(filas), 1)
    return {"n": len(filas), "acierto_1": acierto / n, "acierto_top3": top3 / n, "logver": ll / n}


# ---------------------------------------------------------------- recomendar en vivo
class Modelo:
    def __init__(self, w: np.ndarray, lc: dict[str, float], rango: dict[str, int]):
        self.w, self.lc, self.rango = w, lc, rango

    @classmethod
    def cargar(cls, ruta: Path = DATA / "eleccion.json") -> "Modelo | None":
        if not ruta.exists():
            return None
        d = json.loads(ruta.read_text(encoding="utf-8"))
        return cls(np.array(d["w"]), d["lc"], d["rango"])


def recomendar(peticion: dict, estado, modelo: Modelo, filtrar_comprados: bool = True, n_frio: int = 300) -> list[str]:
    """Candidatos: todos los disponibles de la categoría de lo último visto (+ lo visto de esa categoría);
    a un comprador sin vistas, los n_frio mejores por calidad y popularidad. Se puntúan y se devuelven los 10 mejores."""
    cat = estado.catalogo
    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    ya = estado.comprados.get(str(peticion.get("user_id", "")), set()) if filtrar_comprados else set()
    disp = estado.disponibles
    if vistos:
        categoria = cat.get(vistos[-1], {}).get("category")
        # SOLO su categoría (+ lo visto que sea de ella): el carrusel antiguo siempre enseñaba 10 de la categoría
        # de lo último visto, así que el modelo no sabe comparar entre categorías (y el 97,6 % compra en ella).
        cand = [pid for pid in _por_categoria(estado).get(categoria, []) if pid in disp]
        cand += [pid for pid in reversed(vistos) if pid in disp and cat.get(pid, {}).get("category") == categoria]
    else:
        cand = [pid for pid in _mejores_frio(estado, modelo, n_frio) if pid in disp]
    cand = [pid for pid in dict.fromkeys(cand) if pid not in ya]
    s = senales(cand, vistos, cat, modelo.lc, modelo.rango) @ modelo.w
    elegidos = [cand[i] for i in np.argsort(-s, kind="stable")[:10]]
    if len(elegidos) < 10:  # relleno: más vendidos de siempre (no se puntúan contra los de la categoría)
        elegidos += [pid for pid in estado.populares if pid in disp and pid not in ya and pid not in elegidos][:10 - len(elegidos)]
    return elegidos


_cache: dict = {}


def _por_categoria(estado) -> dict[str, list[str]]:
    clave = ("cat", id(estado.catalogo))
    if clave not in _cache:
        por: dict[str, list[str]] = defaultdict(list)
        for pid, d in estado.catalogo.items():
            por[d.get("category")].append(pid)
        _cache.clear()
        _cache[clave] = dict(por)
    return _cache[clave]


def _mejores_frio(estado, modelo: Modelo, n: int) -> list[str]:
    clave = ("frio", id(estado.catalogo), id(modelo))
    if clave not in _cache:
        todos = list(estado.catalogo)
        s = senales(todos, [], estado.catalogo, modelo.lc, modelo.rango) @ modelo.w
        _cache[clave] = [todos[i] for i in np.argsort(-s, kind="stable")[:n]]
    return _cache[clave]


# ---------------------------------------------------------------- main
def descargar() -> None:
    from src.rawtree import query
    ses = query("""SELECT ts::String ts, user_id::String u, shown, purchased::String p, recent_views rv,
      `context.device`::String dev, `context.page`::String page, revenue_eur::Float64 rev
      FROM shop.history_sessions WHERE purchased::String != ''""", timeout=900)
    imp = query(f"""SELECT pid, pos, (t < '{CORTE}') antes, count() n FROM (SELECT arrayJoin(arrayEnumerate(shown)) pos,
      shown[pos]::String pid, ts::DateTime64(3) t FROM shop.history_sessions) GROUP BY pid, pos, antes""", timeout=900)
    compras = query("""SELECT user_id::String u, product_id::String p, min(ts::DateTime64(3))::String t
      FROM shop.history_events WHERE event_type='purchase' GROUP BY u, p""", timeout=900)
    DATA.mkdir(exist_ok=True)
    (DATA / "eleccion.pkl").write_bytes(pickle.dumps({"ses": ses, "imp": imp, "compras": compras}))
    print(f"descargado: {len(ses)} compras del carrusel, {len(imp)} filas de impresiones, {len(compras)} primeras compras")


def main() -> None:
    import sys
    from src.catalogo import cargar_catalogo

    if "--descargar" in sys.argv or not (DATA / "eleccion.pkl").exists():
        descargar()

    d = pickle.loads((DATA / "eleccion.pkl").read_bytes())  # lo baja el script de descarga (ver docs/EXPERIMENTOS)
    ses, imp, primeras = d["ses"], d["imp"], d["compras"]
    primera = {(f["u"], f["p"]): f["t"] for f in primeras}
    cat = cargar_catalogo()
    ses = [s for s in ses if s["p"] in s["shown"]]
    nuevas = [s for s in ses if not _es_recompra(s, primera)]
    print(f"compras en el carrusel: {len(ses)} · sin recompra: {len(nuevas)} ({len(nuevas)/len(ses):.0%})")

    # Validación: piezas con datos < CORTE; entreno [INICIO_ENTRENO, CORTE); test >= CORTE.
    compras, esperadas = calidades(imp, ses, antes=True)
    rango = rangos_popularidad(primeras, CORTE)
    entreno = [s for s in nuevas if INICIO_ENTRENO <= s["ts"] < CORTE]
    test = [s for s in nuevas if s["ts"] >= CORTE]
    f_ent = preparar(entreno, cat, compras, esperadas, rango, con_puesto=True, dejar_uno_fuera=True)
    f_test = preparar(test, cat, compras, esperadas, rango, con_puesto=True, dejar_uno_fuera=False)
    print(f"entreno {len(f_ent)} compras · test {len(f_test)} compras\n")

    k = len(SENALES)
    puestos = list(range(k, k + len(PUESTOS)))
    idx = {n: i for i, n in enumerate(SENALES)}
    comparar = {
        "solo posición": puestos,
        "v0 (popularidad + misma categoría)": [idx["popularidad"], idx["misma_categoria"]] + puestos,
        "e-samen-v2 (todas las señales)": None,
    }
    resultados = {}
    for nombre, cols in comparar.items():
        w = entrenar(f_ent, cols)
        resultados[nombre] = validar(f_test, w, cols)
        print(f"{nombre:<38} {resultados[nombre]}")
        if cols is None:
            w_val = w

    print("\nseñal                     coef.   ×odds")
    for n, c in zip(SENALES + PUESTOS, w_val):
        print(f"{n:<24} {c:7.3f} {math.exp(c):7.2f}")

    # Modelo final para vivo: piezas con TODO el historial, entrenado con todas las compras sin recompra.
    compras, esperadas = calidades(imp, ses, antes=None)
    rango = rangos_popularidad(primeras, None)
    f_todo = preparar(nuevas, cat, compras, esperadas, rango, con_puesto=True, dejar_uno_fuera=True)
    w = entrenar(f_todo)[: len(SENALES)]  # sin los puestos: al recomendar valoramos el producto
    lc = {pid: log_calidad(pid, compras, esperadas) for pid in set(esperadas) | set(compras)}
    lc_nuevo = math.log(calidad_tipica(compras, esperadas))
    lc.update({pid: lc_nuevo for pid in cat if pid not in lc})  # productos sin historial: calidad típica
    (DATA / "eleccion.json").write_text(json.dumps({"w": w.tolist(), "senales": SENALES, "lc": lc, "rango": rango,
                                                    "validacion": resultados}), encoding="utf-8")
    print(f"\nguardado data/eleccion.json ({len(lc)} productos con calidad)")


if __name__ == "__main__":
    main()
