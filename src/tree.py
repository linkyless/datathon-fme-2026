"""R6 · El "modelo grande": LightGBM (árboles) entrenado como ELECCIÓN dentro del carrusel. Dueño: Ranking.

    python -m src.tree      # entrena, valida (acierto del 1.º) y guarda data/arbol.txt + data/arbol.json

La idea, para un product manager:
- Igual que e-samen-v2 (src/choice.py): en cada visita con compra el carrusel antiguo enseñó 10 productos y el
  comprador eligió uno. El modelo aprende qué hace que un producto GANE a sus 9 vecinos de carrusel. Comparar solo
  dentro de lo enseñado evita el sesgo del carrusel antiguo (enseñaba lo popular).
- Diferencia con la v2: en vez de una neurona (cada señal suma por su cuenta), cientos de árboles pequeños que
  aprenden COMBINACIONES ("más barato que lo visto, pero solo si es de su subcategoría habitual").
  LightGBM lo hace con el objetivo "lambdarank": cada visita es un grupo de 10 y el comprado debe quedar 1.º.
- El carrusel antiguo enseñaba siempre 10 de la MISMA categoría que lo último visto: el árbol no puede aprender
  "misma categoría". Por eso, al recomendar, los candidatos salen de esa categoría (como en la v2).
- Las recompras se quitan (en vivo no existen: aviso de los organizadores).
- Sin mirar el futuro: perfiles y co-visitas de las filas de entrenamiento se calculan con datos anteriores a
  INICIO_ENTRENO; los del test, con datos anteriores a CORTE. La calidad se calcula como en la v2.
- La posición entra como señal al entrenar (lo de arriba se compra más solo por estar arriba); al recomendar
  todos los candidatos llevan puesto 1, así compiten por lo que son.
"""
from __future__ import annotations

import json
import math
import pickle
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
CORTE = "2026-11-07"
INICIO_ENTRENO = "2026-10-01"
INICIO_PARADA = "2026-10-31"
"""La última semana del entrenamiento sirve para decidir cuántos árboles usar (parada temprana)."""
SUAVIZADO = 5.0
DECAIMIENTO = 0.7
PARAMS_LGB = {"objective": "lambdarank", "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50,
              "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0,
              "lambdarank_truncation_level": 10, "verbose": -1, "seed": 29}
"""Árboles pequeños (15 hojas) y aprendizaje lento: con ~10.000 compras, árboles grandes memorizarían."""
MAX_ARBOLES = 1000
N_FRIO = 300
"""Comprador sin vistas: se puntúan los N_FRIO mejores productos por calidad y popularidad."""

SENALES = [
    ("log_calidad", "compras / compras esperadas por sus posiciones (suavizado hacia lo típico)"),
    ("popularidad", "-log(1 + puesto en compradores distintos)"),
    ("misma_subcategoria", "misma subcategoría que lo último visto"),
    ("misma_marca", "misma marca que lo último visto"),
    ("sub_en_vistas", "su subcategoría aparece en lo visto"),
    ("log_ratio_precio", "log(precio / precio de lo último visto)"),
    ("log_precio", "log(precio)"),
    ("revisita", "ya lo ha visto en esta sesión"),
    ("frio", "el comprador no tiene vistas"),
    ("covisita", "log(1 + puntos de co-visita con lo visto)"),
    ("sub_en_perfil", "su subcategoría está en las 3 que más compra el comprador"),
    ("marca_en_perfil", "su marca está en las 3 que más compra el comprador"),
    ("cat_en_perfil", "su categoría está en las 3 que más compra el comprador"),
    ("log_ratio_precio_habitual", "log(precio / precio mediano de lo que compra el comprador)"),
    ("log_compras_comprador", "log(1 + compras del comprador)"),
    ("tasa_ips", "tasa de compra del producto cuando se enseñaba, corregida por propensity (perfil de Artai)"),
    ("movil", "entra desde ios/android"),
    ("ficha", "está en la ficha de un producto (page = product)"),
    ("puesto", "puesto en el carrusel (al recomendar, siempre 1)"),
]
NOMBRES = [n for n, _ in SENALES]


# ---------------------------------------------------------------- calidad (copiado de src/choice.py, Lester)
_MU: dict = {}


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


# ---------------------------------------------------------------- señales de los candidatos de una visita
def _precio(d: dict) -> float:
    try:
        return float(d.get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


def senales(cand: list[str], vistos: list[str], device: str, page: str, cat: dict, lc: dict[str, float],
            rango: dict[str, int], covisitas: dict, perfil: dict | None, perfiles_producto: dict,
            puestos: list[int] | None = None) -> np.ndarray:
    """Matriz (len(cand), len(SENALES)). puestos=None -> todos en el puesto 1 (al recomendar)."""
    n_pop = len(rango) + 1
    lc_tipica = lc.get("__tipica__", 0.0)
    ultimo = cat.get(vistos[-1], {}) if vistos else {}
    p_ult = _precio(ultimo)
    subs_vistas = {cat.get(v, {}).get("subcategory") for v in vistos} - {None, ""}
    vistos_set = set(vistos)
    perfil = perfil or {}
    p_subs, p_marcas, p_cats = set(perfil.get("subcategorias", [])), set(perfil.get("marcas", [])), \
        set(perfil.get("categorias", []))
    p_med = float(perfil.get("precio_mediano") or 0)
    log_compras = math.log1p(float(perfil.get("compras") or 0))
    movil = float(device in ("ios", "android"))
    ficha = float(page == "product")
    frio = float(not vistos)

    covis: dict[str, float] = {}
    for k, pid in enumerate(reversed(vistos)):
        for pos, (otro, peso) in enumerate(covisitas.get(pid, [])):
            covis[otro] = covis.get(otro, 0.0) + (DECAIMIENTO ** k) * peso / (pos + 1)

    X = np.zeros((len(cand), len(SENALES)), dtype=np.float32)
    for i, pid in enumerate(cand):
        d = cat.get(pid, {})
        precio = max(_precio(d), 1.0)
        X[i] = (lc.get(pid, lc_tipica),
                -math.log1p(rango.get(pid, n_pop)),
                float(bool(ultimo) and d.get("subcategory") == ultimo.get("subcategory")),
                float(bool(ultimo) and d.get("brand") == ultimo.get("brand")),
                float(d.get("subcategory") in subs_vistas),
                math.log(precio / max(p_ult, 1.0)) if vistos else 0.0,
                math.log(precio),
                float(pid in vistos_set),
                frio,
                math.log1p(covis.get(pid, 0.0)),
                float(d.get("subcategory") in p_subs),
                float(d.get("brand") in p_marcas),
                float(d.get("category") in p_cats),
                math.log(precio / p_med) if p_med > 0 else 0.0,
                log_compras,
                float(perfiles_producto.get(pid, {}).get("tasa_ips", 0.0)),
                movil,
                ficha,
                float(puestos[i]) if puestos else 1.0)
    return X


# ---------------------------------------------------------------- datos de entrenamiento
def _vistos(s: dict) -> list[str]:
    return [v["product_id"] for v in (s["rv"] or []) if v.get("product_id")]


def _es_recompra(s: dict, primera: dict) -> bool:
    t = primera.get((s["u"], s["p"]))
    return t is not None and t < s["ts"]


def _perfiles(hasta: str) -> tuple[dict, dict]:
    """Perfiles de Artai (src/coldstart.py) con datos anteriores a `hasta`, guardados en data/ (no se sube)."""
    ruta = DATA / f"perfiles_{hasta}.pkl"
    if not ruta.exists():
        from src import coldstart
        ruta.write_bytes(pickle.dumps((coldstart._construir_perfil_comprador(hasta),
                                       coldstart._construir_perfil_producto(hasta))))
    return pickle.loads(ruta.read_bytes())


PLIEGUES = 5
"""Calidad "fuera de pliegue" para el entrenamiento (ver _calidad_por_pliegue)."""


def _pliegue(s: dict) -> int:
    return zlib.crc32(f"{s['u']}|{s['ts']}".encode()) % PLIEGUES  # igual en cada ejecución


def _calidad_por_pliegue(ses: list[dict], compras: dict, esperadas: dict) -> list[dict[str, float]]:
    """Para las filas de entrenamiento, la calidad de cada producto se calcula SIN las compras de su propio
    pliegue (1/5 de las visitas), escalando las del resto ×5/4. Con "dejar uno fuera" (lo que hace la v2) el
    comprado tiene un valor exacto distinto al de no comprado y un árbol lo memoriza (medido: acierto del 1.º
    0,112, como el azar). Fuera de pliegue, todas las filas de un pliegue comparten valor: no hay nada que
    memorizar."""
    por_pliegue = [Counter() for _ in range(PLIEGUES)]
    for s in ses:
        if s["ts"] < CORTE:
            por_pliegue[_pliegue(s)][s["p"]] += 1
    mu = calidad_tipica(compras, esperadas)
    escala = PLIEGUES / (PLIEGUES - 1)
    salida = []
    for k in range(PLIEGUES):
        fuera = {pid: (compras.get(pid, 0) - por_pliegue[k].get(pid, 0)) * escala
                 for pid in set(compras) | set(esperadas)}
        salida.append({pid: math.log((c + SUAVIZADO * mu) / (esperadas.get(pid, 0.0) + SUAVIZADO))
                       for pid, c in fuera.items()})
    return salida


def _matriz(sesiones: list[dict], cat, compras, esperadas, rango, covisitas, perfiles,
            lc_pliegues: list[dict] | None):
    """X (10 filas por visita), y (1 = el comprado) y el tamaño de cada grupo (visita).
    lc_pliegues: calidad fuera de pliegue (entrenamiento); None = calidad normal (test)."""
    pc, pp = perfiles
    bloques, ys, grupos = [], [], []
    lc_normal = None
    for s in sesiones:
        shown = list(s["shown"])
        if lc_pliegues is not None:
            lc = lc_pliegues[_pliegue(s)]
        else:
            lc_normal = lc_normal or {pid: log_calidad(pid, compras, esperadas) for pid in set(esperadas) | set(compras)}
            lc = lc_normal
        bloques.append(senales(shown, _vistos(s), s.get("dev", ""), s.get("page", ""), cat, lc, rango, covisitas,
                               pc.get(s["u"]), pp, puestos=list(range(1, len(shown) + 1))))
        ys.extend(float(pid == s["p"]) for pid in shown)
        grupos.append(len(shown))
    return np.vstack(bloques), np.asarray(ys), grupos


def acierto(puntos: np.ndarray, y: np.ndarray, grupos: list[int]) -> tuple[float, float]:
    """(acierto del 1.º, acierto en los 3 primeros): ¿el comprado es el que más puntúa de su visita?"""
    a1 = a3 = 0
    i = 0
    for g in grupos:
        orden = np.argsort(-puntos[i:i + g], kind="stable")
        comprado = int(np.argmax(y[i:i + g]))
        a1 += orden[0] == comprado
        a3 += comprado in orden[:3]
        i += g
    return a1 / len(grupos), a3 / len(grupos)


# ---------------------------------------------------------------- usar el modelo (lo llama ranking.recommend)
_modelo: dict | None = None
_leido = False


def modelo_guardado() -> dict | None:
    """data/arbol.txt + data/arbol.json, leídos UNA vez. None (y aviso en consola) si faltan o no hay lightgbm."""
    global _modelo, _leido
    if not _leido:
        _leido = True
        try:
            import lightgbm as lgb
            meta = json.loads((DATA / "arbol.json").read_text(encoding="utf-8"))
            if meta.get("senales") != NOMBRES:
                raise ValueError("arbol.json de otra versión")
            _modelo = {"booster": lgb.Booster(model_file=str(DATA / "arbol.txt")), "lc": meta["lc"],
                       "rango": meta["rango"]}
        except (ImportError, OSError, ValueError, KeyError) as e:
            _modelo = None
            print(f"AVISO arbol: usar_arbol NO hace nada ({e}). Ejecuta: python -m src.tree", flush=True)
    return _modelo


_cache: dict = {}


def _por_categoria(estado) -> dict[str, list[str]]:
    clave = ("cat", id(estado.catalogo))
    if clave not in _cache:
        por: dict[str, list[str]] = defaultdict(list)
        for pid, d in estado.catalogo.items():
            por[d.get("category")].append(pid)
        _cache[clave] = dict(por)
    return _cache[clave]


def _mejores_frio(estado, modelo: dict) -> list[str]:
    """Para compradores sin vistas: los N_FRIO mejores productos según calidad + popularidad (sin perfil)."""
    clave = ("frio", id(estado.catalogo), id(modelo))
    if clave not in _cache:
        lc, rango = modelo["lc"], modelo["rango"]
        tip, n = lc.get("__tipica__", 0.0), len(rango) + 1
        todos = list(estado.catalogo)
        todos.sort(key=lambda p: lc.get(p, tip) - 0.5 * math.log1p(rango.get(p, n)), reverse=True)
        _cache[clave] = todos[:N_FRIO]
    return _cache[clave]


def candidatos(peticion: dict, estado, modelo: dict, vale) -> list[str]:
    """Con vistas: todo lo disponible de la categoría de lo último visto, más lo visto de esa categoría.
    Sin vistas: los N_FRIO mejores por calidad y popularidad. `vale` = disponible (y no comprado si se filtra)."""
    cat = estado.catalogo
    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    if vistos:
        categoria = cat.get(vistos[-1], {}).get("category")
        cand = _por_categoria(estado).get(categoria, []) + \
            [pid for pid in reversed(vistos) if cat.get(pid, {}).get("category") == categoria]
    else:
        cand = _mejores_frio(estado, modelo)
    return [pid for pid in dict.fromkeys(cand) if vale(pid)]


def puntuar(peticion: dict, cand: list[str], estado, modelo: dict) -> np.ndarray:
    ctx = peticion.get("context") or {}
    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    perfil = getattr(estado, "perfiles_comprador", {}).get(str(peticion.get("user_id", "")))
    X = senales(cand, vistos, str(ctx.get("device", "")), str(ctx.get("page", "")), estado.catalogo,
                modelo["lc"], modelo["rango"], estado.covisitas, perfil, getattr(estado, "perfiles_producto", {}))
    return modelo["booster"].predict(X, num_threads=1)


# ---------------------------------------------------------------- principal
def main() -> None:
    import lightgbm as lgb

    from src.catalog import load_catalog as cargar_catalogo
    from src.covisits import cargar_covisitas

    ruta = DATA / "eleccion.pkl"
    if not ruta.exists():  # mismos datos que la v2: python -m src.choice --descargar
        from src.choice import descargar
        descargar()
    d = pickle.loads(ruta.read_bytes())
    ses, imp, primeras = d["ses"], d["imp"], d["compras"]
    primera = {(f["u"], f["p"]): f["t"] for f in primeras}
    cat = cargar_catalogo()
    ses = [s for s in ses if s["p"] in s["shown"]]
    nuevas = [s for s in ses if not _es_recompra(s, primera)]
    print(f"compras en el carrusel: {len(ses)} · sin recompra: {len(nuevas)}")

    compras, esperadas = calidades(imp, ses, antes=True)
    rango = {pid: i for i, (pid, _) in enumerate(Counter(f["p"] for f in primeras if f["t"] < CORTE).most_common())}
    entreno = [s for s in nuevas if INICIO_ENTRENO <= s["ts"] < CORTE]
    test = [s for s in nuevas if s["ts"] >= CORTE]
    print(f"entreno {len(entreno)} compras ({INICIO_ENTRENO} -> {CORTE}) · test {len(test)} compras (>= {CORTE})")

    cov_ent, per_ent = cargar_covisitas(INICIO_ENTRENO), _perfiles(INICIO_ENTRENO)
    cov_test, per_test = cargar_covisitas(CORTE), _perfiles(CORTE)
    lc_pliegues = _calidad_por_pliegue(ses, compras, esperadas)
    X, y, g = _matriz(entreno, cat, compras, esperadas, rango, cov_ent, per_ent, lc_pliegues)
    Xt, yt, gt = _matriz(test, cat, compras, esperadas, rango, cov_test, per_test, None)

    # 1) ¿Cuántos árboles? Entrena hasta INICIO_PARADA y para cuando la última semana deja de mejorar.
    n_a = sum(1 for s in entreno if s["ts"] < INICIO_PARADA)
    filas_a = sum(g[:n_a])
    parada = lgb.train({**PARAMS_LGB, "metric": "ndcg", "eval_at": [1]},
                       lgb.Dataset(X[:filas_a], y[:filas_a], group=g[:n_a]), MAX_ARBOLES,
                       valid_sets=[lgb.Dataset(X[filas_a:], y[filas_a:], group=g[n_a:])],
                       callbacks=[lgb.early_stopping(50, verbose=False)])
    n_arboles = max(parada.best_iteration, 50)
    # 2) Modelo final: todo el entrenamiento con ese número de árboles.
    modelo = lgb.train(PARAMS_LGB, lgb.Dataset(X, y, group=g), n_arboles)

    print(f"\nárboles: {n_arboles}")
    a1, a3 = acierto(modelo.predict(Xt), yt, gt)
    Xt1 = Xt.copy()
    Xt1[:, NOMBRES.index("puesto")] = 1.0
    b1, b3 = acierto(modelo.predict(Xt1), yt, gt)
    print(f"test (con su puesto real, como la v2): acierto del 1.º {a1:.3f} · en los 3 primeros {a3:.3f}")
    print(f"test (todos en puesto 1, como en vivo):  acierto del 1.º {b1:.3f} · en los 3 primeros {b3:.3f}")
    print("(al azar: 0.100 y 0.300)")

    imp_gan = modelo.feature_importance("gain")
    print(f"\n{'señal':<28}{'% de la ganancia':>18}   qué es")
    for (nombre, texto), v in sorted(zip(SENALES, imp_gan), key=lambda t: -t[1]):
        print(f"{nombre:<28}{100 * v / imp_gan.sum():>17.1f}%   {texto}")

    # Para vivo: mismo modelo validado; calidad y popularidad con TODO el historial.
    compras_t, esperadas_t = calidades(imp, ses, antes=None)
    lc = {pid: log_calidad(pid, compras_t, esperadas_t) for pid in set(esperadas_t) | set(compras_t)}
    lc["__tipica__"] = math.log(calidad_tipica(compras_t, esperadas_t))  # productos nuevos: calidad típica
    rango_t = {pid: i for i, (pid, _) in enumerate(Counter(f["p"] for f in primeras).most_common())}
    modelo.save_model(str(DATA / "arbol.txt"))
    (DATA / "arbol.json").write_text(json.dumps({"senales": NOMBRES, "lc": lc, "rango": rango_t, "arboles": n_arboles,
                                                 "validacion": {"acierto_1_puesto_real": a1, "acierto_3_puesto_real": a3,
                                                                "acierto_1_puesto_1": b1, "acierto_3_puesto_1": b3}}),
                                     encoding="utf-8")
    print(f"\nguardado data/arbol.txt y data/arbol.json ({len(lc)} productos con calidad)")


if __name__ == "__main__":
    main()
