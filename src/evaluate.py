"""Evaluación offline: pone nota a cada versión con la última semana del historial. Dueño: Evaluación.

    python -m src.evaluate                 # todos los BRAZOS con el test y el Estado guardados en data/
    python -m src.evaluate --calibrar      # además, compara con el leaderboard real de cada versión que ha ido en vivo
    python -m src.evaluate --recalcular    # rehace el Estado (cuando cambia alguna pieza)
    python -m src.evaluate --brazos C,CF,v2 --n 20000   # solo esos brazos y una parte del test (más rápido)
    python -m src.evaluate --recall        # aparte: recall@10 de las fuentes de candidatos sobre shop.history_events

Reproduce starter/offline_eval.ipynb con nuestro contrato (src/state.py). El "examen" son 100.000 visitas al carrusel
de la última semana; el Estado solo usa datos ANTERIORES a esa semana, para no mirar el futuro.

Para que el offline se parezca al leaderboard (sáb noche):
- Cada brazo usa EXACTAMENTE los parámetros con los que corrió en vivo (claves especiales de src/service.py
  incluidas) y, si la respuesta falla o es inválida, se sustituye por populares, como hace el servicio.
- `--calibrar` pide a la API pública del leaderboard la conversión esperada de cada versión nuestra y la del baseline
  en la MISMA ventana, y dice qué métrica offline ordena las versiones como el leaderboard (correlación de rangos).
- El leaderboard puntúa la PROBABILIDAD de compra del modelo oculto (por eso da 1,0545 % y no números redondos): no
  tiene el ruido de nuestras compras reales del feedback. Es la mejor verdad que tenemos para calibrar.
"""
from __future__ import annotations

import argparse
import math
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import requests

from src import ranking
from src.state import Estado, construir
from src.rawtree import TEAM, query
from src.validate import response_errors as errores_respuesta

DATA = Path(__file__).resolve().parent.parent / "data"
N_TEST = 100_000
BOOTSTRAP = 200
"""Cuántos remuestreos hace el bootstrap para el intervalo de confianza (ver `bootstrap()`)."""
TOPE_IPS = 50
"""Peso máximo de un acierto (1/propensity puede ser enorme y hacer que la nota baile). Mismo tope que Leonardo."""
LEADERBOARD = "https://datathon.rawtree.dev/api/state"
"""API pública del leaderboard: ?by=model&from=<epoch>&to=<epoch> (ventana de 15 min a 3 h, en pasos de 5 min)."""

# ---------------------------------------------------------------- brazos (= versiones, tal como corrieron en vivo)
_V0 = {"_sin_covisitas": True, "_sin_comprados": True}
BRAZOS: dict[str, dict | None] = {
    "baseline": None,                                          # más vendidos de siempre en stock: el 0 del leaderboard
    "C": _V0,                                                  # v0: más vendidos de la categoría de lo último visto
    "CF": {**_V0, "_filtrar_comprados": True},                 # C quitando lo que ese comprador ya compró
    "D": {"_sin_comprados": True},                             # co-visitas, sin filtro
    "E": {**_V0, "revisitas": 0.3},                            # C + re-visitas
    "T": {**_V0, "tendencia": 0.5},                            # C + tendencias (offline apenas hay modas)
    "A": {"_sin_covisitas": True},                             # v1 sin co-visitas (con el filtro de ranking)
    "B": {},                                                   # ranking.py con sus valores por defecto (= v1)
    "M": {"usar_modelo": True},                                # R5, regresión logística de Leonardo
    "MT": {"usar_modelo": True, "tendencia": 0.5},
    "MF": {"usar_modelo": True, "_filtrar_comprados": True},
    "MTF": {"usar_modelo": True, "tendencia": 0.5, "_filtrar_comprados": True},
    "V2": {"_eleccion": True, "_filtrar": True},               # e-samen-v2 (src/choice.py) quitando lo ya comprado
    "V2N": {"_eleccion": True, "_filtrar": False},             # e-samen-v2 sin quitarlo (VARIANTES_V2 de servicio.py)
    "V3": {"_eleccion": True, "_filtrar": True, "_favoritas": True},  # v2 + fríos a su categoría favorita
    "R6": {"usar_arbol": True},                                # árbol LightGBM de Leonardo (src/tree.py), sin lo comprado
    "A6": {"_combo": True},                                    # en vivo desde 18:51: R6 con vistas, V3 sin vistas
}
"""Nombre -> params como en VARIANTES de src/service.py. Para medir una idea nueva (p. ej. el árbol R6 de Leonardo,
con la clave que tenga en ranking.PARAMS), añade una línea aquí."""

EN_VIVO = [("baseline", "baseline"), ("e-samen-v0", "C"), ("e-samen-v1-A", "A"), ("e-samen-v1-B", "B"),
           ("e-samen-v1-C", "C"), ("e-samen-v1-CF", "CF"), ("e-samen-v1-D", "D"), ("e-samen-v1-E", "E"),
           ("e-samen-v1-T", "T"), ("e-samen-v1-A6", "A6")]
"""(model_version en vivo, brazo offline que lo reproduce). La v0 de la tarde equivale a C (no tenía más piezas).
M, MT, MF y MTF no están: en vivo corrió R5 v1 (con ya_comprado) y el código de hoy es R5 v2, otro modelo."""

COMPARAR_CON = ("baseline", "C")
"""Contra qué brazos se imprime la diferencia con su intervalo de confianza."""

MEDIDAS = ["hit_rate@10", "purchase_hit@10", "purchase_hit_sr", "IPS10_clics", "IPS10_compras", "IPS10_compras_sr",
           "PBM_compras", "PBM_compras_sr"]
"""Columnas de `aciertos()`. "_sr" = sin recompras: no cuenta como compra la de un producto que el comprador ya
había comprado antes del corte (el organizador avisa de que el CSV exagera las recompras)."""
MEDIDAS_IC = ["purchase_hit@10", "IPS10_compras", "PBM_compras_sr"]
"""Medidas que se imprimen con intervalo de confianza (las demás salen en la tabla)."""


def fecha_corte() -> str:
    """Último día de shop.history_sessions menos 7 días, como en el cuaderno."""
    ultimo = query("SELECT toDate(max(ts::DateTime64(3))) AS d FROM shop.history_sessions")[0]["d"]
    return str(date.fromisoformat(ultimo) - timedelta(days=7))


def _a_peticion(f: dict) -> dict:
    """Fila de history_sessions -> mismo formato que una fila de shop.requests (lo que espera recommend)."""
    return {
        "request_id": f["request_id"], "ts": f["ts"], "user_id": f["user_id"],
        "context": {"device": f["device"], "region": f["region"], "page": f["page"]},
        "recent_views": f.get("recent_views") or [],
        # Lo que pasó de verdad (recommend no lo mira; solo lo usan las métricas).
        "shown": f["shown"], "propensity": f["propensity"],
        "clicked": f.get("clicked") or None, "purchased": f.get("purchased") or None,
    }


def cargar_test(corte: str) -> list[dict]:
    """Las 100.000 visitas del examen. Se descargan una vez y luego se leen de data/ (siempre las mismas)."""
    ruta = DATA / f"test_{corte}.pkl"
    if ruta.exists():
        return pickle.loads(ruta.read_bytes())
    print(f"Descargando {N_TEST} visitas de test (solo la primera vez)...")
    filas = query(f"""
        SELECT request_id::String AS request_id, toString(ts) AS ts, user_id::String AS user_id,
               `context.device`::String AS device, `context.region`::String AS region,
               `context.page`::String AS page, recent_views, shown, propensity,
               clicked::String AS clicked, purchased::String AS purchased
        FROM shop.history_sessions WHERE ts >= '{corte}' ORDER BY rand() LIMIT {N_TEST}""", timeout=600)
    test = [_a_peticion(f) for f in filas]
    DATA.mkdir(exist_ok=True)
    ruta.write_bytes(pickle.dumps(test))
    return test


def cargar_estado(corte: str, recalcular: bool) -> Estado:
    """Estado construido solo con datos anteriores al corte. Se guarda en data/ porque tarda en calcularse."""
    ruta = DATA / f"estado_{corte}.pkl"
    cambiado = recalcular or not ruta.exists()
    if not cambiado:
        estado = pickle.loads(ruta.read_bytes())
    else:
        print("Construyendo el Estado offline...")
        estado = construir(hasta=corte)
    # Un Estado guardado antes de que se añadiera un campo no lo trae: se pone vacío.
    for campo in fields(Estado):
        if not hasattr(estado, campo.name):
            setattr(estado, campo.name, campo.default_factory())
    # construir() no rellena categorias_usuario (lo hace el refresco del servicio): A6 y V3 lo necesitan.
    if not estado.categorias_usuario:
        from src import choice as eleccion
        print("Calculando las categorías favoritas de cada comprador antes del corte (una vez)...")
        estado.categorias_usuario = eleccion.cargar_categorias_usuario(corte)
        cambiado = True
    if cambiado:  # solo el proceso principal llega aquí: los de cada brazo leen el Estado ya completo
        DATA.mkdir(exist_ok=True)
        ruta.write_bytes(pickle.dumps(estado))
    return estado


# ---------------------------------------------------------------- modelos que hay que preparar antes
def preparar_modelos(corte: str, brazos: list[str]) -> list[str]:
    """Crea lo que necesitan M* y v2, SIN mirar el futuro. Devuelve los brazos que no se pueden evaluar."""
    fuera = []
    if any(BRAZOS[b] and BRAZOS[b].get("usar_modelo") for b in brazos):
        from src import scoring as puntuacion
        if puntuacion.CORTE != corte:
            print(f"AVISO: puntuacion.CORTE={puntuacion.CORTE} ≠ corte {corte}: M, MT, MF y MTF no se evalúan")
            fuera += [b for b in brazos if BRAZOS[b] and BRAZOS[b].get("usar_modelo")]
        elif puntuacion.modelo_guardado() is None:  # falta o es de otra versión: M sería B en silencio
            print("Entrenando R5 (python -m src.scoring, datos anteriores al corte)...")
            puntuacion.main()
    if any(BRAZOS[b] and (BRAZOS[b].get("usar_arbol") or BRAZOS[b].get("_combo")) for b in brazos):
        try:
            _arbol_offline(corte)
        except Exception as e:
            print(f"AVISO: no se pudo preparar R6 ({e}); R6 y A6 no se evalúan")
            fuera += [b for b in brazos if BRAZOS[b] and (BRAZOS[b].get("usar_arbol") or BRAZOS[b].get("_combo"))]
    if True:  # la v2 también da la curva de posición de PBM: se prepara aunque no se evalúe el brazo
        try:
            _modelo_v2(corte)
        except Exception as e:  # sin red o sin el pkl de Lester: se evalúa todo lo demás
            print(f"AVISO: no se pudo preparar la v2 ({e}); se salta y PBM usa la curva de los clics")
            fuera += [b for b in brazos if BRAZOS[b] and BRAZOS[b].get("_eleccion")]
    return fuera


def _modelo_v2(corte: str):
    """Modelo de elección de Lester entrenado SOLO con datos < corte (su data/eleccion.json usa todo el historial,
    que incluye la semana del test: evaluarlo con él sería hacer trampa). Se guarda en data/ para los procesos.
    De paso guarda la curva de posición que aprende (ver `curva_posicion`)."""
    from src import choice as eleccion
    ruta = DATA / f"eleccion_offline_{corte}.pkl"
    if ruta.exists():
        guardado = pickle.loads(ruta.read_bytes())
        if isinstance(guardado, dict):  # si no es dict es el formato antiguo (solo el modelo, sin curva): se rehace
            return guardado["modelo"]
    if eleccion.CORTE != corte:
        raise ValueError(f"eleccion.CORTE={eleccion.CORTE} ≠ corte {corte}")
    if not (DATA / "eleccion.pkl").exists():
        print("Descargando los datos de la v2 (python -m src.choice --descargar, ~30 s)...")
        eleccion.descargar()
    print("Entrenando la v2 con datos anteriores al corte...")
    d = pickle.loads((DATA / "eleccion.pkl").read_bytes())
    ses, imp, primeras = d["ses"], d["imp"], d["compras"]
    primera = {(f["u"], f["p"]): f["t"] for f in primeras}
    ses = [s for s in ses if s["p"] in s["shown"]]
    nuevas = [s for s in ses if not eleccion._es_recompra(s, primera)]
    compras, esperadas = eleccion.calidades(imp, ses, antes=True)
    rango = eleccion.rangos_popularidad(primeras, corte)
    catalogo = cargar_estado(corte, recalcular=False).catalogo
    entreno = [s for s in nuevas if eleccion.INICIO_ENTRENO <= s["ts"] < corte]
    filas = eleccion.preparar(entreno, catalogo, compras, esperadas, rango, con_puesto=True, dejar_uno_fuera=True)
    w = eleccion.entrenar(filas)
    curva = [1.0] + [math.exp(x) for x in w[len(eleccion.SENALES):]]  # efecto del puesto 2..10 frente al 1.º
    lc = {pid: eleccion.log_calidad(pid, compras, esperadas) for pid in set(esperadas) | set(compras)}
    tipica = math.log(eleccion.calidad_tipica(compras, esperadas))
    lc.update({pid: tipica for pid in catalogo if pid not in lc})
    modelo = eleccion.Modelo(w[: len(eleccion.SENALES)], lc, rango)  # sin los puestos, como el modelo de vivo
    ruta.write_bytes(pickle.dumps({"modelo": modelo, "curva": curva}))
    return modelo


def _arbol_offline(corte: str) -> dict:
    """R6 de Leonardo sin mirar el futuro. Los árboles (data/arbol.txt) se entrenan solo con datos < corte, pero
    data/arbol.json guarda calidad y popularidad con TODO el historial (para vivo), y eso incluye las compras de la
    semana del test: el árbol "sabría" qué se compró en el examen. Aquí se recalculan con datos < corte, igual que
    en la validación de src/tree.py. Se deja instalado en src.tree para que ranking.recommend lo use."""
    import lightgbm as lgb
    from collections import Counter
    from src import tree as arbol
    if arbol.CORTE != corte:
        raise ValueError(f"arbol.CORTE={arbol.CORTE} ≠ corte {corte}")
    if not (DATA / "arbol.txt").exists():
        raise FileNotFoundError("falta data/arbol.txt: ejecuta python -m src.tree")
    ruta = DATA / f"arbol_offline_{corte}.pkl"
    if ruta.exists():
        lc, rango = pickle.loads(ruta.read_bytes())
    else:
        d = pickle.loads((DATA / "eleccion.pkl").read_bytes())
        ses = [s for s in d["ses"] if s["p"] in s["shown"]]
        compras, esperadas = arbol.calidades(d["imp"], ses, antes=True)
        lc = {pid: arbol.log_calidad(pid, compras, esperadas) for pid in set(esperadas) | set(compras)}
        lc["__tipica__"] = math.log(arbol.calidad_tipica(compras, esperadas))
        rango = {pid: i for i, (pid, _) in
                 enumerate(Counter(f["p"] for f in d["compras"] if f["t"] < corte).most_common())}
        ruta.write_bytes(pickle.dumps((lc, rango)))
    arbol._modelo = {"booster": lgb.Booster(model_file=str(DATA / "arbol.txt")), "lc": lc, "rango": rango}
    arbol._leido = True
    return arbol._modelo


# ---------------------------------------------------------------- recomendar como el servicio
def preparar_brazo(nombre: str, estado: Estado) -> tuple[Estado, dict, bool, bool, bool]:
    """(Estado del brazo, params para recommend, ¿filtrar lo comprado por comprador?, ¿usar la v2?, ¿combo A6?).
    Mismas claves especiales que respuesta_segura() de src/service.py."""
    params = BRAZOS[nombre]
    if params is None:  # baseline: solo catálogo, disponibles y más vendidos de siempre
        return Estado(catalogo=estado.catalogo, disponibles=estado.disponibles, populares=estado.populares), {}, \
            False, False, False
    params = dict(params)
    filtrar = bool(params.pop("_filtrar_comprados", False))
    v2 = bool(params.pop("_eleccion", False))
    combo = bool(params.pop("_combo", False))
    vaciar = [campo for clave, campo in (("_sin_covisitas", "covisitas"), ("_sin_comprados", "comprados"))
              if params.pop(clave, False)]
    return replace(estado, **{campo: {} for campo in vaciar}), params, filtrar, v2, combo


def recomendaciones(nombre: str, test: list[dict], estado: Estado, corte: str) -> tuple[list[list[str]], int]:
    """Las 10 de cada visita y cuántas tuvo que sustituir el "servicio" por populares (fallo o respuesta inválida)."""
    est, params, filtrar, v2, combo = preparar_brazo(nombre, estado)
    modelo = _modelo_v2(corte) if v2 or combo else None
    if v2 or combo:
        from src import choice as eleccion
    if combo or params.get("usar_arbol"):
        _arbol_offline(corte)  # en este proceso: ranking.recommend usará el árbol sin mirar el futuro
    disponibles = estado.disponibles
    rescate = [pid for pid in estado.populares if pid in disponibles][:10]
    recs, sustituidas = [], 0
    for t in test:
        e = est
        if filtrar:  # se filtra con las compras de verdad del comprador, ANTES de vaciar piezas (como el servicio)
            ya = estado.comprados.get(str(t.get("user_id", "")))
            if ya:
                e = replace(est, disponibles=est.disponibles - ya)
        try:
            if combo:  # A6: con vistas, el árbol; sin vistas, su categoría favorita con la v2 (servicio.py)
                if any(v.get("product_id") for v in (t.get("recent_views") or [])):
                    items = ranking.recommend(t, e, {"usar_arbol": True, "filtrar_comprados": True})
                else:
                    items = eleccion.recomendar(t, e, modelo, filtrar_comprados=True, favoritas_frio=True)
            elif v2:
                items = eleccion.recomendar(t, e, modelo, filtrar_comprados=params.get("_filtrar", True),
                                            favoritas_frio=params.get("_favoritas", False))
            else:
                items = ranking.recommend(t, e, params)
            if errores_respuesta(items, disponibles):
                raise ValueError("inválida")
        except Exception:
            items, sustituidas = rescate, sustituidas + 1
        recs.append(items)
    return recs, sustituidas


# ---------------------------------------------------------------- métricas
def curva_posicion(test: list[dict], corte: str) -> list[float]:
    """Cuánto vale cada puesto 1..10 frente al 1.º.

    Preferida: la que aprende el modelo de elección comparando DENTRO de cada carrusel (antes del corte), que separa
    el efecto del puesto del de qué producto había: [1, 0,59, 0,52, 0,41, ... 0,28] (sáb noche; R5 de Leonardo da
    parecido). Si no está, la de los clics del test por puesto, que sobrestima la caída (arriba iba lo popular)."""
    ruta = DATA / f"eleccion_offline_{corte}.pkl"
    if ruta.exists():
        guardado = pickle.loads(ruta.read_bytes())
        if isinstance(guardado, dict):
            return guardado["curva"]
    clics = [0] * 10
    for t in test:
        if t["clicked"] in t["shown"][:10]:
            clics[t["shown"].index(t["clicked"])] += 1
    return [max(c, 1) / max(clics[0], 1) for c in clics]


def es_recompra(test: list[dict], estado: Estado) -> list[bool]:
    """¿La compra de esa visita es de un producto que el comprador ya había comprado antes del corte?"""
    return [bool(t["purchased"]) and t["purchased"] in estado.comprados.get(str(t["user_id"]), ()) for t in test]


def por_visita(t: dict, recs: list[str], recompra: bool, curva: list[float]) -> list[float]:
    """Las MEDIDAS de una visita. hit/purchase_hit: 1/0, o NaN si no hubo clic/compra (su media sin NaN es la tasa).

    IPS10: por cada producto que el carrusel antiguo enseñó y que también está en nuestros 10, si se clicó/compró
    suma 1/propensity (tope TOPE_IPS): lo que se enseñaba poco pesa más cuando aparece.
    PBM (modelo de posición): igual, pero multiplicado por curva[nuestro puesto] / curva[puesto en que se enseñó]:
    subirlo al 1.º vale más que dejarlo en el 10.º, como en el leaderboard."""
    nan = float("nan")
    compra = t["purchased"]
    compra_sr = None if recompra else compra
    puesto = {pid: i for i, pid in enumerate(recs)}
    ips_clic = ips_compra = ips_compra_sr = pbm = pbm_sr = 0.0
    for j, (pid, prop) in enumerate(zip(t["shown"][:10], (t.get("propensity") or [])[:10])):
        prop = float(prop or 0)  # 0 o NULL no puede dar peso infinito
        if pid not in puesto or prop <= 0:
            continue
        peso = min(1 / prop, TOPE_IPS)
        posicion = curva[puesto[pid]] / curva[j]
        ips_clic += peso * (t["clicked"] == pid)
        ips_compra += peso * (compra == pid)
        ips_compra_sr += peso * (compra_sr == pid)
        pbm += peso * posicion * (compra == pid)
        pbm_sr += peso * posicion * (compra_sr == pid)
    return [float(t["clicked"] in puesto) if t["clicked"] else nan,
            float(compra in puesto) if compra else nan,
            float(compra_sr in puesto) if compra_sr else nan,
            ips_clic, ips_compra, ips_compra_sr, pbm, pbm_sr]


def aciertos(test: list[dict], recs: list[list[str]], recompras: list[bool], curva: list[float]) -> np.ndarray:
    """Matriz visitas × MEDIDAS (se guarda visita a visita para el bootstrap)."""
    return np.array([por_visita(t, r, rc, curva) for t, r, rc in zip(test, recs, recompras)], dtype=float)


def resumen(test: list[dict], recs: list[list[str]], matriz: np.ndarray) -> dict[str, float]:
    """Una cifra por medida. Las IPS10/PBM, en compras (o clics) esperadas por 100 visitas, como el leaderboard.
    Más el IPS/SNIPS del clic en el 1.º puesto del cuaderno oficial."""
    salida: dict[str, float] = {"n": len(test)}
    for k, m in enumerate(MEDIDAS):
        col = matriz[:, k] if len(matriz) else np.array([np.nan])
        valor = float(np.nanmean(col)) if np.any(~np.isnan(col)) else 0.0
        salida[m] = 100 * valor if m.startswith(("IPS", "PBM")) else valor
    pesos = premios = 0.0
    ips1 = 0.0
    for t, r in zip(test, recs):
        prop = float((t.get("propensity") or [0])[0] or 0)
        if r and r[0] == t["shown"][0] and prop > 0:
            pesos += 1 / prop
            premio = float(t["clicked"] == t["shown"][0])
            premios += premio / prop
            ips1 += premio / prop
    salida["SNIPS_slot1"] = premios / pesos if pesos else 0.0
    salida["IPS_slot1"] = ips1 / max(len(test), 1)
    return salida


def _evaluar_brazo(nombre: str, corte: str, n: int) -> tuple:
    """Lo que hace cada proceso en paralelo: lee test y Estado de data/ y evalúa UN brazo."""
    test = cargar_test(corte)[:n]
    estado = cargar_estado(corte, recalcular=False)
    recs, sustituidas = recomendaciones(nombre, test, estado, corte)
    matriz = aciertos(test, recs, es_recompra(test, estado), curva_posicion(test, corte))
    frias = [i for i, t in enumerate(test) if not t["recent_views"]]
    frio = resumen([test[i] for i in frias], [recs[i] for i in frias], matriz[frias])
    return nombre, resumen(test, recs, matriz), frio, sustituidas, matriz


def bootstrap(a: np.ndarray, b: np.ndarray, veces: int = BOOTSTRAP) -> list[tuple[float, float, float]]:
    """Diferencia a - b en cada columna, con su intervalo de confianza del 95 %.

    Bootstrap: se sortean, con repetición, tantas visitas como hay en el test y se recalcula la diferencia. Se
    repite `veces` veces; el 95 % central de esas diferencias es el intervalo. Las dos políticas usan SIEMPRE las
    mismas visitas sorteadas (bootstrap emparejado): así solo cuenta lo que cambia la política, no la suerte de
    qué visitas tocaron. Si el intervalo no incluye el 0, la diferencia no es casualidad."""
    azar = np.random.default_rng(0)  # semilla fija: el mismo resultado cada vez que se ejecuta
    diferencia = np.nanmean(a, axis=0) - np.nanmean(b, axis=0)
    muestras = []
    for _ in range(veces):
        idx = azar.integers(0, len(a), len(a))
        muestras.append(np.nanmean(a[idx], axis=0) - np.nanmean(b[idx], axis=0))
    bajo, alto = np.percentile(muestras, [2.5, 97.5], axis=0)
    return [(float(d), float(lo), float(hi)) for d, lo, hi in zip(diferencia, bajo, alto)]


# ---------------------------------------------------------------- calibración con el leaderboard
def versiones_en_vivo() -> dict[str, tuple[int, int, int]]:
    """model_version -> (peticiones, primer ts, último ts) en epoch, según nuestro feedback ⋈ submissions."""
    filas = query(f"""
        SELECT s.mv AS v, count() AS n, toUnixTimestamp(min(f.ts)) AS desde, toUnixTimestamp(max(f.ts)) AS hasta
        FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts FROM {TEAM}.feedback
              WHERE request_id != '__init__' AND ts > now() - INTERVAL 2 DAY) AS f
        INNER JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
                    FROM {TEAM}.submissions GROUP BY rid) AS s ON f.rid = s.rid
        GROUP BY v""")
    return {f["v"]: (int(f["n"]), int(f["desde"]), int(f["hasta"])) for f in filas}


def leaderboard(version: str, desde: int, hasta: int) -> dict | None:
    """Conversión esperada de `version` y del baseline en la ventana que la cubre (máx. las últimas 3 h)."""
    a = desde // 300 * 300
    b = max(-(-hasta // 300) * 300, a + 900)
    a = max(a, b - 10800)
    r = requests.get(LEADERBOARD, params={"by": "model", "from": a, "to": b}, timeout=30)
    r.raise_for_status()
    d = r.json()
    equipo = next((t for t in d["teams"] if t["team_id"] == TEAM), None)
    base = d["baseline"]
    if version == "baseline":  # el baseline oficial se mide sobre las mismas peticiones: lift 1 por definición
        fila = {"conversion": base["conversion"], "cold_conversion": base["cold_conversion"],
                "requests": base["requests"]}
    else:
        fila = next((v for v in (equipo or {}).get("versions", []) if v["model_version"] == version), None)
    if not fila:
        return None
    return {"conv": fila["conversion"], "frio": fila.get("cold_conversion"), "n": fila["requests"],
            "base": base["conversion"], "base_frio": base["cold_conversion"],
            "ventana": f"{datetime.fromtimestamp(a):%H:%M}-{datetime.fromtimestamp(b):%H:%M}"}


def _rangos(x: list[float]) -> np.ndarray:
    """Puesto de cada valor (empates: puesto medio)."""
    x = np.asarray(x, dtype=float)
    r = np.empty(len(x))
    r[np.argsort(x, kind="stable")] = np.arange(len(x))
    for v in np.unique(x):
        r[x == v] = r[x == v].mean()
    return r


def spearman(a: list[float], b: list[float]) -> float:
    """Correlación de rangos: 1 = ordenan las versiones igual; 0 = nada que ver; -1 = al revés."""
    ra, rb = _rangos(a), _rangos(b)
    return float(np.corrcoef(ra, rb)[0, 1]) if ra.std() > 0 and rb.std() > 0 else float("nan")


def calibrar(resultados: dict[str, dict], frios: dict[str, dict]) -> None:
    """¿Qué métrica offline se parece al leaderboard? Lift = conversión / la del baseline, en vivo y offline."""
    metricas = [m for m in resultados["baseline"] if m != "n"]
    vivas = versiones_en_vivo()
    filas = []
    for version, brazo in EN_VIVO:
        if brazo not in resultados or version not in vivas:
            continue
        lb = leaderboard(version, *vivas[version][1:])
        if lb and lb["base"] > 0:
            filas.append((version, brazo, lb))
    if len(filas) < 3:
        print("\nCalibración: hacen falta al menos 3 versiones con datos en el leaderboard.")
        return

    def lift(res: dict, brazo: str, m: str) -> float:
        base = res["baseline"].get(m) or 0.0
        return res[brazo].get(m, 0.0) / base if base else float("nan")

    print(f"\nCalibración con el leaderboard (conversión esperada; lift = versión / baseline en la misma ventana):")
    principales = ["purchase_hit@10", "purchase_hit_sr", "IPS10_compras", "IPS10_compras_sr", "PBM_compras",
                   "PBM_compras_sr"]
    print(f"{'versión':<17}{'brazo':<9}{'ventana':<13}{'pet.':>6}{'conv %':>8}{'base %':>8}{'LIFT':>7}" +
          "".join(f"{m[:14]:>16}" for m in principales))
    for version, brazo, lb in filas:
        print(f"{version:<17}{brazo:<9}{lb['ventana']:<13}{lb['n']:>6}{lb['conv']:>8.3f}{lb['base']:>8.3f}"
              f"{lb['conv'] / lb['base']:>7.2f}" + "".join(f"{lift(resultados, brazo, m):>16.2f}" for m in principales))

    vivo = [lb["conv"] / lb["base"] for _, _, lb in filas]
    puntuacion = []
    for m in metricas:
        off = [lift(resultados, brazo, m) for _, brazo, _ in filas]
        if any(math.isnan(x) for x in off):
            continue
        error = float(np.mean([abs(math.log(max(o, 1e-9) / v)) for o, v in zip(off, vivo)]))
        puntuacion.append((spearman(off, vivo), -error, m, error))
    puntuacion.sort(reverse=True)
    print(f"\n¿Qué métrica ordena las {len(filas)} versiones como el leaderboard? (1 = igual; error = cuánto se "
          f"desvía el lift, en log)")
    for rho, _, m, error in puntuacion:
        print(f"  {m:<20} correlación de rangos {rho:+.2f}   error medio {error:.2f}")

    frias = [(v, b, lb) for v, b, lb in filas if lb.get("frio") and lb.get("base_frio")]
    if len(frias) >= 3:
        vivo_f = [lb["frio"] / lb["base_frio"] for _, _, lb in frias]
        print("\nSegmento frío (en vivo: comprador nuevo o producto nuevo; offline: sin vistas recientes):")
        mejores = []
        for m in metricas:
            off = [lift(frios, b, m) for _, b, _ in frias]
            if not any(math.isnan(x) for x in off):
                mejores.append((spearman(off, vivo_f), m))
        for rho, m in sorted(mejores, reverse=True)[:5]:
            print(f"  {m:<20} correlación de rangos {rho:+.2f}")

    mejor = puntuacion[0][2]
    base_ahora = filas[-1][2]["base"]
    sin_vivo = [b for b in resultados if b not in {br for _, br, _ in filas}]
    if sin_vivo:
        print(f"\nPredicción con la mejor métrica ({mejor}), baseline actual {base_ahora:.3f} %:")
        for b in sin_vivo:
            print(f"  {b:<10} lift {lift(resultados, b, mejor):.2f}  →  ~{base_ahora * lift(resultados, b, mejor):.2f} %")


# ---------------------------------------------------------------- salida
def imprimir(resultados: dict[str, dict], sustituidas: dict[str, int] | None = None) -> None:
    columnas = ["n"] + MEDIDAS + ["SNIPS_slot1"]
    print(f"{'brazo':<11}" + "".join(f"{c[:15]:>16}" for c in columnas) + ("    sustit." if sustituidas else ""))
    for nombre, fila in resultados.items():
        celdas = "".join(f"{fila[c]:>16.4f}" if isinstance(fila[c], float) else f"{fila[c]:>16}" for c in columnas)
        print(f"{nombre:<11}{celdas}" + (f"{sustituidas[nombre]:>12}" if sustituidas else ""))


def imprimir_diferencias(aciertos_por_brazo: dict[str, np.ndarray], contra: str) -> None:
    """Cada brazo frente a `contra`: diferencia e intervalo del 95 % en las MEDIDAS_IC."""
    def veredicto(lo: float, hi: float) -> str:
        if lo == hi == 0:
            return "idéntico"
        return "sube" if lo > 0 else "baja" if hi < 0 else "puede ser suerte"

    cols = [MEDIDAS.index(m) for m in MEDIDAS_IC]
    print(f"\nDiferencia frente a {contra} (intervalo de confianza del 95 %, bootstrap de {BOOTSTRAP}; IPS/PBM por visita):")
    print(f"{'brazo':<11}" + "".join(f"{'Δ ' + m + ' [IC 95 %]':<52}" for m in MEDIDAS_IC))
    for nombre, a in aciertos_por_brazo.items():
        if nombre == contra:
            continue
        diferencias = bootstrap(a[:, cols], aciertos_por_brazo[contra][:, cols])
        print(f"{nombre:<11}" + "".join(f"{d:>+9.4f} [{lo:+.4f}, {hi:+.4f}] {veredicto(lo, hi):<17}"
                                        for d, lo, hi in diferencias))


# ---------------------------------------------------------------- recall@10 sobre shop.history_events (--recall)
RECALL_DIAS = 10
"""El examen del recall son las compras de los últimos RECALL_DIAS días de shop.history_events."""
RECALL_UNO_DE_CADA = 20
"""Se usa 1 de cada N compradores (elegidos por su user_id, siempre los mismos): bajar los 12 M de eventos sobra."""


def _recall_populares(ctx: dict, base: dict) -> list[str]:
    """Referencia mínima: los más vendidos de la tienda (lo que haría el baseline)."""
    return [p for p in base["populares"] if p not in ctx["comprados"]][:10]


def _recall_afinidad_categoria(ctx: dict, base: dict) -> list[str]:
    """Lo que hacemos ahora: los más vendidos de la categoría que más ha mirado ese comprador."""
    cuenta: dict[str, int] = {}
    for p in ctx["vistos"]:
        c = base["categoria"].get(p)
        if c:
            cuenta[c] = cuenta.get(c, 0) + 1
    if not cuenta:
        return _recall_populares(ctx, base)
    favorita = max(cuenta, key=cuenta.get)
    return [p for p in base["por_categoria"].get(favorita, []) if p not in ctx["comprados"]][:10]


def _recall_vistos_recientes(ctx: dict, base: dict) -> list[str]:
    """Los 10 últimos productos distintos que ha mirado (del más reciente al más antiguo), sin lo ya comprado."""
    return [p for p in dict.fromkeys(reversed(ctx["vistos"])) if p not in ctx["comprados"]][:10]


FUENTES_RECALL = {
    "populares": _recall_populares,
    "afinidad_categoria": _recall_afinidad_categoria,
    "vistos_recientes": _recall_vistos_recientes,
}
"""Fuentes de candidatos que mide `--recall`. Para probar una nueva (vecinos por embedding, co-visitas...), escribe
una función `f(ctx, base) -> lista de product_id` y añádela aquí. Solo cuentan sus 10 primeros.
  ctx  = lo que se sabe de ESE comprador justo antes de la compra: {"user", "vistos" (product_id en orden, del más
         antiguo al más reciente, con repetidos), "comprados" (set), "ahora" (ms)}.
  base = datos comunes calculados SOLO con lo anterior a los 10 días del examen: {"populares" (más vendidos, en
         orden), "por_categoria" (categoría -> más vendidos), "categoria" y "subcategoria" (product_id -> nombre)}."""


def _recall_datos() -> dict:
    """Eventos (vistas y compras de los 60 días) de 1 de cada RECALL_UNO_DE_CADA compradores, el catálogo y los más
    vendidos de ANTES de la ventana del examen. Se baja una vez y se guarda en data/."""
    ruta = DATA / f"recall_{RECALL_DIAS}d_1de{RECALL_UNO_DE_CADA}.pkl"
    if ruta.exists():
        return pickle.loads(ruta.read_bytes())
    print("Descargando los datos del recall (solo la primera vez, ~1 min)...")
    fin = int(query("SELECT toUnixTimestamp64Milli(max(ts::DateTime64(3))) AS t FROM shop.history_events")[0]["t"])
    inicio = fin - RECALL_DIAS * 24 * 3600 * 1000
    eventos: list[tuple] = []
    trozos = 4
    for k in range(trozos):
        filas = query(f"""
            SELECT user_id::String AS u, product_id::String AS p, event_type::String AS e,
                   toUnixTimestamp64Milli(ts::DateTime64(3)) AS t
            FROM shop.history_events
            WHERE event_type::String IN ('view', 'purchase')
              AND cityHash64(user_id::String) % {RECALL_UNO_DE_CADA * trozos} = {k} LIMIT 3000000""", timeout=600)
        eventos += [(f["u"], int(f["t"]), f["e"] == "purchase", f["p"]) for f in filas]
    ventas = query(f"""
        SELECT product_id::String AS p, count() AS n FROM shop.history_events
        WHERE event_type = 'purchase' AND ts::DateTime64(3) < fromUnixTimestamp64Milli(toInt64({inicio}))
        GROUP BY p""", timeout=300)
    catalogo = query("""SELECT product_id::String AS p, any(category::String) AS c, any(subcategory::String) AS s
                        FROM shop.catalog GROUP BY p""", timeout=120)
    datos = {"inicio": inicio, "fin": fin, "eventos": sorted(eventos),
             "ventas": {f["p"]: int(f["n"]) for f in ventas},
             "categoria": {f["p"]: f["c"] for f in catalogo}, "subcategoria": {f["p"]: f["s"] for f in catalogo}}
    DATA.mkdir(exist_ok=True)
    ruta.write_bytes(pickle.dumps(datos))
    return datos


def recall_historial() -> dict[str, dict]:
    """recall@10 de cada fuente de candidatos sobre las compras ORGÁNICAS de shop.history_events (no el carrusel).

    Por cada compra de los últimos RECALL_DIAS días se le da a la fuente lo que ese comprador había hecho ANTES
    (nunca después) y se mira si el producto comprado está entre los 10 que propone. Dos preguntas, dos columnas:
      - "todo lo anterior": con todas sus vistas previas a la compra. OJO: casi siempre se compra algo que se acaba
        de mirar, así que aquí repetir lo visto gana por definición. Sirve de techo, no para comparar fuentes.
      - "antes de verlo": solo con lo anterior a la PRIMERA vez que miró ese producto. Mide si la fuente es capaz de
        proponer algo que el comprador aún no ha visto y que acabará comprando: esta es la que compara fuentes.
    Solo cuentan las compras de un producto que ese comprador no había comprado antes (en vivo no hay recompras).
    No es una nota de conversión: sirve para descartar fuentes malas; lo que entra en vivo lo decide un A/B."""
    d = _recall_datos()
    populares = [p for p, _ in sorted(d["ventas"].items(), key=lambda kv: -kv[1])]
    por_categoria: dict[str, list[str]] = {}
    for p in populares:
        por_categoria.setdefault(d["categoria"].get(p, ""), []).append(p)
    base = {"populares": populares, "por_categoria": por_categoria,
            "categoria": d["categoria"], "subcategoria": d["subcategoria"]}

    aciertos = {n: [0, 0] for n in FUENTES_RECALL}
    compras = recompras = sin_vistas = 0
    usuario = None
    vistos: list[str] = []
    primera_vista: dict[str, int] = {}   # producto -> cuántas vistas llevaba el comprador ANTES de mirarlo por 1.ª vez
    comprados: set[str] = set()
    for u, t, es_compra, p in d["eventos"]:          # ordenados por comprador y por tiempo
        if u != usuario:
            usuario, vistos, primera_vista, comprados = u, [], {}, set()
        if not es_compra:
            primera_vista.setdefault(p, len(vistos))
            vistos.append(p)
            continue
        if t >= d["inicio"]:
            if p in comprados:
                recompras += 1
            else:
                compras += 1
                sin_vistas += not vistos
                antes_de_verlo = vistos[:primera_vista.get(p, len(vistos))]
                for nombre, fuente in FUENTES_RECALL.items():
                    for k, contexto in enumerate((vistos, antes_de_verlo)):
                        ctx = {"user": u, "vistos": contexto, "comprados": comprados, "ahora": t}
                        aciertos[nombre][k] += p in fuente(ctx, base)[:10]
        comprados.add(p)

    print(f"recall@10 sobre shop.history_events · últimos {RECALL_DIAS} días · 1 de cada {RECALL_UNO_DE_CADA} "
          f"compradores · {compras} compras (fuera {recompras} recompras; {sin_vistas} sin ninguna vista previa)")
    print(f"{'fuente':<24}{'todo lo anterior':>18}{'antes de verlo':>18}{'± (95 %)':>12}")
    salida = {}
    for nombre, (todo, antes) in aciertos.items():
        r_todo, r_antes = todo / max(compras, 1), antes / max(compras, 1)
        margen = 1.96 * math.sqrt(max(r_antes * (1 - r_antes), 1e-9) / max(compras, 1))
        salida[nombre] = {"todo_lo_anterior": r_todo, "antes_de_verlo": r_antes, "n": compras}
        print(f"{nombre:<24}{100 * r_todo:>16.2f} %{100 * r_antes:>16.2f} %{100 * margen:>10.2f} %")
    return salida


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--recall", action="store_true",
                      help="solo el recall@10 de las fuentes de candidatos sobre shop.history_events (FUENTES_RECALL)")
    args.add_argument("--recalcular", action="store_true", help="rehace el Estado offline")
    args.add_argument("--calibrar", action="store_true", help="compara con el leaderboard de las versiones en vivo")
    args.add_argument("--brazos", default=",".join(BRAZOS), help="brazos separados por comas (por defecto, todos)")
    args.add_argument("--n", type=int, default=N_TEST, help="cuántas visitas del test usar (por defecto, todas)")
    a = args.parse_args()
    if a.recall:  # medida aparte: no usa el carrusel, ni el Estado, ni los brazos
        recall_historial()
        return

    corte = fecha_corte()
    test = cargar_test(corte)[:a.n]
    cargar_estado(corte, a.recalcular)
    brazos = [b for b in a.brazos.split(",") if b in BRAZOS]
    for imprescindible in COMPARAR_CON:  # sin ellos no hay con qué comparar
        if imprescindible not in brazos:
            brazos.insert(0, imprescindible)
    fuera = preparar_modelos(corte, brazos)
    brazos = [b for b in brazos if b not in fuera]
    clics = sum(bool(t["clicked"]) for t in test)
    compras = sum(bool(t["purchased"]) for t in test)
    print(f"Corte {corte} · test {len(test)} visitas · {clics} con clic · {compras} con compra · "
          f"ranking.py por defecto (brazo B) = {ranking.NOMBRE}\n")

    # Un brazo por proceso. Máximo 4: cada uno carga su copia del test y del Estado (cientos de MB).
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        salidas = list(pool.map(_evaluar_brazo, brazos, [corte] * len(brazos), [a.n] * len(brazos)))
    resultados = {nombre: todo for nombre, todo, _, _, _ in salidas}
    frios = {nombre: frio for nombre, _, frio, _, _ in salidas}
    sustituidas = {nombre: s for nombre, _, _, s, _ in salidas}
    aciertos_por_brazo = {nombre: m for nombre, _, _, _, m in salidas}

    imprimir(resultados, sustituidas)
    print("\nSegmento frío (sin vistas recientes):")
    imprimir(frios)
    for contra in COMPARAR_CON:
        imprimir_diferencias(aciertos_por_brazo, contra)
    if a.calibrar:
        calibrar(resultados, frios)


if __name__ == "__main__":
    main()
