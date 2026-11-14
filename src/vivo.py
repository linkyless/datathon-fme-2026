"""e-samen-v4 · Aprender DEL SIMULADOR EN VIVO (team_29.feedback). Dueño: Operador (Lester).

    python -m src.vivo            # descarga feedback (7 h), entrena, valida y guarda data/vivo.json

Por qué (sáb 19:00, honesto): el historial del carrusel engaña. El carrusel antiguo SOLO enseñaba la categoría de lo
último visto, así que ningún modelo entrenado con él (v2, árbol R6) puede ver que la categoría FAVORITA del comprador
vende ~4× más que la de lo último visto. En cambio `feedback` son NUESTRAS respuestas (CF, C, D, E, M, fallback...)
con lo que hizo el simulador que puntúa. Medido en 850.000 productos enseñados y 709 compras:
  - ya lo vio en esta sesión ×10 · misma subcategoría ×2,6 · misma categoría ×2,7
  - su categoría favorita (SOLO historial anterior: sin fuga) ×2,2. OJO: con la actividad de hoy salía ×11, pero era
    FUGA (la actividad posterior a nuestra respuesta, incluida la compra, decidía la "favorita").
  - top 10 de ventas > top 100 > top 1000.
Modelo: regresión logística por PRODUCTO ENSEÑADO (¿se compró?), con el puesto como señal para descontarlo; al
recomendar se puntúan los candidatos sin puesto y se ordenan. Se valida por tiempo (las últimas 1,5 h).
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
L2 = 2.0
HOY = "2026-11-14"
"""Corte para las piezas del entrenamiento: todo lo de antes de hoy (el feedback es de hoy)."""
OBJETIVO_CLIC = 0.3
"""Las compras son pocas (709): cada clic cuenta como 0,3 compras para que el modelo tenga más señal (la compra
siempre pasa por un clic). Se valida SOLO con compras."""

SENALES = ["revisita", "revisita_ultimo", "fav1", "fav2", "fav3", "misma_cat", "misma_sub", "misma_marca",
           "popularidad", "log_precio", "ya_comprado", "frio_x_fav1", "frio_x_popularidad"]
PUESTOS = [f"puesto_{i}" for i in range(2, 11)]


def senales(cand: list[str], vistos: list[str], favoritas: list[str], comprados: set[str], cat: dict,
            rango: dict[str, int]) -> np.ndarray:
    ultimo = cat.get(vistos[-1], {}) if vistos else {}
    frio = 0.0 if vistos else 1.0
    n_pop = len(rango) + 1
    vset = set(vistos)
    X = np.zeros((len(cand), len(SENALES)))
    for k, pid in enumerate(cand):
        d = cat.get(pid, {})
        c = d.get("category")
        f1 = float(len(favoritas) > 0 and c == favoritas[0])
        pop = -math.log1p(rango.get(pid, n_pop))
        try:
            lp = math.log(max(float(d.get("price_eur") or 1), 1.0))
        except (TypeError, ValueError):
            lp = 0.0
        X[k] = (float(pid in vset), float(bool(vistos) and pid == vistos[-1]),
                f1, float(len(favoritas) > 1 and c == favoritas[1]), float(len(favoritas) > 2 and c == favoritas[2]),
                float(bool(ultimo) and c == ultimo.get("category")),
                float(bool(ultimo) and d.get("subcategory") == ultimo.get("subcategory")),
                float(bool(ultimo) and d.get("brand") == ultimo.get("brand")),
                pop, lp, float(pid in comprados), frio * f1, frio * pop)
    return X


# ---------------------------------------------------------------- entrenamiento
def descargar(horas: int = 8) -> list[dict]:
    from src.rawtree import query
    fb = query(f"""SELECT f.request_id::String rid, f.ts::String ts, f.shown shown, f.clicked::String c,
        f.purchased::String p, r.user_id::String u, r.recent_views rv
      FROM team_29.feedback f
      JOIN (SELECT request_id, user_id, recent_views FROM shop.requests
            WHERE ts::DateTime64(3) > now64(3) - INTERVAL {horas} HOUR) r ON r.request_id::String = f.request_id::String
      WHERE f.ts::DateTime64(3) > now64(3) - INTERVAL {horas} HOUR AND f.request_id::String != '__init__'""", timeout=900)
    DATA.mkdir(exist_ok=True)
    (DATA / "feedback_vivo.pkl").write_bytes(pickle.dumps(fb))
    return fb


def matriz(fb, cat, cu, comp, rango):
    Xs, ys, ws, ts = [], [], [], []
    for f in fb:
        sh = f["shown"] or []
        if len(sh) != 10:
            continue
        vistos = [v["product_id"] for v in (f["rv"] or []) if v.get("product_id")]
        X = senales(sh, vistos, cu.get(f["u"], []), comp.get(f["u"], set()), cat, rango)
        P = np.zeros((10, len(PUESTOS)))
        for j in range(1, 10):
            P[j, j - 1] = 1.0
        Xs.append(np.hstack([X, P]))
        y = np.array([1.0 if f["p"] == pid else (OBJETIVO_CLIC if f["c"] == pid else 0.0) for pid in sh])
        ys.append(y)
        ts.append(f["ts"])
    return np.vstack(Xs), np.concatenate(ys), np.repeat(ts, 10)


def entrenar(X, y, iters=40):
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Xb @ w)))
        g = Xb.T @ (y - p) - L2 * np.r_[0, w[1:]]
        H = -(Xb * (p * (1 - p))[:, None]).T @ Xb - L2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        paso = np.linalg.solve(H, g)
        w -= paso
        if np.abs(paso).max() < 1e-7:
            break
    return w


def validar_slates(fb, cat, cu, comp, rango, w) -> dict:
    """En cada respuesta con COMPRA: ¿el comprado es el que más puntúa (sin puesto) de los 10?"""
    k = len(SENALES)
    a1 = a3 = n = 0
    for f in fb:
        sh = f["shown"] or []
        if not f["p"] or f["p"] not in sh or len(sh) != 10:
            continue
        vistos = [v["product_id"] for v in (f["rv"] or []) if v.get("product_id")]
        s = senales(sh, vistos, cu.get(f["u"], []), comp.get(f["u"], set()), cat, rango) @ w[1:k + 1]
        orden = list(np.argsort(-s, kind="stable"))
        y = sh.index(f["p"])
        a1 += orden[0] == y
        a3 += y in orden[:3]
        n += 1
    return {"compras": n, "acierto_1": a1 / max(n, 1), "acierto_top3": a3 / max(n, 1)}


# ---------------------------------------------------------------- recomendar
class Modelo:
    def __init__(self, w):
        self.w = np.asarray(w)[1:len(SENALES) + 1]

    @classmethod
    def cargar(cls, ruta: Path = DATA / "vivo.json") -> "Modelo | None":
        if not ruta.exists():
            return None
        return cls(json.loads(ruta.read_text(encoding="utf-8"))["w"])


_rango_cache: dict = {}


def _rango(estado) -> dict[str, int]:
    clave = id(estado.populares)
    if clave not in _rango_cache:
        _rango_cache.clear()
        _rango_cache[clave] = {pid: i for i, pid in enumerate(estado.populares)}
    return _rango_cache[clave]


_por_cat_cache: dict = {}


def _por_categoria(estado, rango) -> dict[str, list[str]]:
    """category -> productos ordenados por ventas (los 150 primeros: el resto casi nunca gana)."""
    clave = (id(estado.catalogo), id(estado.populares))
    if clave not in _por_cat_cache:
        por: dict[str, list[str]] = {}
        for pid in sorted(estado.catalogo, key=lambda p: rango.get(p, 10**9)):
            por.setdefault(estado.catalogo[pid].get("category"), []).append(pid)
        _por_cat_cache.clear()
        _por_cat_cache[clave] = {c: l[:150] for c, l in por.items()}
    return _por_cat_cache[clave]


def recomendar(peticion: dict, estado, modelo: Modelo, filtrar_comprados: bool = False) -> list[str]:
    cat, disp = estado.catalogo, estado.disponibles
    rango = _rango(estado)
    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    uid = str(peticion.get("user_id", ""))
    favoritas = getattr(estado, "categorias_usuario", {}).get(uid, [])
    comprados = estado.comprados.get(uid, set())
    por = _por_categoria(estado, rango)
    cats = list(dict.fromkeys(favoritas[:3] + ([cat.get(vistos[-1], {}).get("category")] if vistos else [])))
    cand = list(reversed(vistos)) + [pid for c in cats for pid in por.get(c, [])] + estado.populares[:100]
    cand = [pid for pid in dict.fromkeys(cand) if pid in disp and not (filtrar_comprados and pid in comprados)]
    s = senales(cand, vistos, favoritas, comprados, cat, rango) @ modelo.w
    elegidos = [cand[i] for i in np.argsort(-s, kind="stable")[:10]]
    if len(elegidos) < 10:
        elegidos += [p for p in estado.populares if p in disp and p not in elegidos][:10 - len(elegidos)]
    return elegidos


# ---------------------------------------------------------------- main
def main() -> None:
    import sys
    from src import eleccion, usuarios
    from src.catalogo import cargar_catalogo, cargar_populares

    fb = descargar() if ("--descargar" in sys.argv or not (DATA / "feedback_vivo.pkl").exists()) \
        else pickle.loads((DATA / "feedback_vivo.pkl").read_bytes())
    cat = cargar_catalogo()
    rango = {p: i for i, p in enumerate(cargar_populares())}
    # SIN FUGA: favoritas y comprados SOLO del historial anterior a hoy. Con shop.events de hoy se cuela la actividad
    # POSTERIOR a la respuesta (incluida la propia compra): la favorita pasaba de ×2,2 (real) a ×11 (falso).
    cu = eleccion.cargar_categorias_usuario(HOY)
    comp = usuarios.cargar_comprados(HOY)
    fb = sorted(fb, key=lambda f: f["ts"])
    corte = fb[int(len(fb) * 0.8)]["ts"]
    ent = [f for f in fb if f["ts"] < corte]
    val = [f for f in fb if f["ts"] >= corte]
    X, y, _ = matriz(ent, cat, cu, comp, rango)
    w = entrenar(X, y)
    print(f"entreno {len(ent)} respuestas · validación {len(val)} (desde {corte})")
    print("validación (de los 10 enseñados, ¿el comprado es el 1.º?):", validar_slates(val, cat, cu, comp, rango, w))
    w_pop = np.zeros_like(w)
    w_pop[1 + SENALES.index("popularidad")] = 1.0
    print("  referencia: solo popularidad                           ", validar_slates(val, cat, cu, comp, rango, w_pop))
    print("\nseñal                 coef.  ×odds")
    for n, c in zip(["constante"] + SENALES + PUESTOS, w):
        print(f"{n:<20} {c:7.3f} {math.exp(c):7.2f}")
    X, y, _ = matriz(fb, cat, cu, comp, rango)
    w = entrenar(X, y)
    (DATA / "vivo.json").write_text(json.dumps({"w": w.tolist(), "senales": SENALES, "entrenado_con": len(fb)}),
                                    encoding="utf-8")
    print(f"\nguardado data/vivo.json (entrenado con las {len(fb)} respuestas)")


if __name__ == "__main__":
    main()
