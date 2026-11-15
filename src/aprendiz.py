"""e-samen · Aprendiz: un modelo que aprende de NUESTRO feedback en vivo. Dueño: Evaluación (Alex).

    python -m src.aprendiz                 # baja lo nuevo (feedback, peticiones, eventos), reentrena, valida y guarda
    python -m src.aprendiz --ensayo 3000   # además ensaya recomendar() sobre peticiones reales (NO envía nada)
    python -m src.aprendiz --sin-descarga  # reentrena con lo que ya hay en data/aprendiz/
    python -m src.aprendiz --nombre ap3    # guarda en data/aprendiz_ap3.json SIN pisar data/aprendiz.json (para un A/B)
    python -m src.aprendiz --nombre ap7c --sin misma_sub   # el mismo entreno sin una señal (control de un A/B limpio)

Por qué (medido el sáb 14/11 con 77.680 respuestas nuestras): lo que más mueve la compra en el simulador no es qué
producto popular va primero, sino la relación previa de ESE comprador con ESE producto y con su categoría:
  - lo tiene en el carrito de hoy sin comprar ×5,7 · en un carrito antiguo sin comprar ×2,2 · ya lo compró ×0,05
  - lo ha mirado en esta sesión ×2,4 (más si además es de los que se venden)
  - parte de sus vistas que caen en esa categoría: de 0,01 % (casi nunca la mira) a 0,83 % (más del 40 %) en el hueco 1
El historial del carrusel antiguo no puede enseñar nada de esto (solo mostraba populares), así que aquí los PESOS
salen únicamente de `team_29.feedback`. El historial solo se usa como memoria de lo que hizo cada comprador
(carritos, compras, qué categorías mira) y pesa poco en los números por producto (PESO_HISTORIAL).

Cómo funciona, en tres piezas:
  1. `Mundo`: lo que se sabe de cada comprador y de cada producto en un instante. Se alimenta evento a evento
     (`mundo.evento(...)`) con shop.events. Es la MISMA clase al entrenar y al recomendar.
  2. Entrenar = volver a pasar el día en orden: antes de cada respuesta nuestra se aplican solo los eventos
     ANTERIORES a esa petición (sin fuga de futuro) y se apuntan las señales de los 10 productos enseñados.
     Con eso, una regresión logística (una sola neurona) aprende cuánto vale cada señal; el puesto en la lista
     entra como señal para descontarlo y al recomendar se ignora.
  3. `recomendar(peticion, mundo, modelo, disponibles)`: junta candidatos (sus carritos, lo que ha mirado, lo mejor
     de sus categorías y de lo último visto), los puntúa y devuelve 10 válidos. Nunca enseña lo ya comprado.

Mejora solo: cada ejecución baja únicamente el feedback y los eventos nuevos y reentrena con todo lo acumulado.

Para conectarlo en vivo (lo hacen sus dueños; este archivo no toca ranking.py ni servicio.py):
    mundo = aprendiz.Mundo.al_dia(estado.catalogo)        # al arrancar: historial + eventos de hoy (~1 min)
    mundo.ponerse_al_dia()                                # en el hilo de refresco, cada 1-2 s (una consulta ligera)
    mundo.catalogo = estado.catalogo                      # cada vez que el servicio recarga el catálogo
    modelo = aprendiz.Modelo.cargar()                     # y otra vez tras cada reentreno
    items = aprendiz.recomendar(peticion, mundo, modelo, estado.disponibles)

A/B entre dos entrenos (p. ej. AP2 congelado frente a AP3 reentrenado), en la máquina del servicio:
    cp data/aprendiz.json data/aprendiz_ap2.json          # congela los pesos que están en vivo
    python -m src.aprendiz --nombre ap3                   # reentrena y guarda aparte
    aprendiz.Modelo.cargar("ap2") / aprendiz.Modelo.cargar("ap3")   # un modelo por brazo, el mismo Mundo para los dos

AP7 (sáb noche) = el mismo modelo con una señal más, `misma_sub`. Para un A/B limpio, en la máquina del servicio:
    python -m src.aprendiz --nombre ap7                          # con la señal (aprendida del feedback: ×1,39)
    python -m src.aprendiz --sin-descarga --nombre ap7c --sin misma_sub   # control: mismos datos, sin ella
    aprendiz.recomendar(peticion, mundo, Modelo.cargar("ap7"), disponibles, forzar={"misma_sub": 3.0})   # brazo fuerte

AP8 (dom 06:00) = modelo de ELECCIÓN: aprende "de los 10 enseñados, cuál se eligió" (ver `GRUPOS["x"]`):
    python -m src.aprendiz --nombre ap8 --eleccion --sin o1,o2            # elección + señales nuevas (grupo x)
    python -m src.aprendiz --sin-descarga --nombre ap8c --sin o1,o2,h,x   # control: el modelo por producto de AP7

Exploración (`recomendar(..., explorar=True)`): en la mitad de las peticiones (EXPLORAR_FRACCION, elegidas por el
request_id) los huecos 9 y 10 dejan de ser los del modelo y enseñan un producto NUEVO y uno de FUERA de las listas
de mejores, los dos de una categoría que le interesa a ese comprador. Para qué: hoy solo sabemos qué pasa con lo
que el modelo ya cree bueno; de los productos nuevos teníamos 1.698 enseñados y 4 compras en todo el sábado. Como la
mitad que explora se elige por el request_id, se reconstruye después sin apuntar nada (`toca_explorar`) y la otra
mitad del mismo brazo sirve de comparación: el informe del final del entreno dice qué pasó y cuánto costó.
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import random
import threading
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data" / "aprendiz"
ARCHIVO_MODELO = DATA.parent / "aprendiz.json"

INICIO_VIVO_MS = 1794646800000
"""Sáb 14/11 09:00 UTC (10:00 de Barcelona): primer evento de shop.events."""
PESO_HISTORIAL = 0.1
"""Cuánto cuenta cada vista/compra del historial (60 días) frente a una de hoy en los números por producto.
Con 0.1, el historial entero pesa como ~6 días y los productos nuevos no salen perjudicados mucho tiempo."""
PSEUDO_VISTAS = 50.0
"""Suavizado de la tasa de compra: un producto con pocas vistas se acerca a la tasa media en vez de dispararse."""
L2 = 1.0
PESO_CLIC = 0.0
"""Se aprende solo de COMPRAS (es lo que puntúa). Probado contar cada clic como 0,3 compras: la validación sale
igual (logloss 0,00660 frente a 0,00659) y los pesos dejan de leerse como "× probabilidad de compra"."""
VENTANA_MS = 20 * 60 * 1000
RETRASO_MS = 3000
"""Al entrenar se supone que el Mundo va 3 s por detrás de la petición, como en vivo (medido: 2,7 s con un sondeo
de eventos cada 1,5 s). Así lo que se aprende es lo que de verdad se sabrá al responder."""
N_CATEGORIA, N_SUBCATEGORIA, N_GLOBAL = 40, 20, 40
"""Candidatos de relleno: los mejores de cada categoría / subcategoría / de toda la tienda."""
CATEGORIAS_AFINES, SUBCATEGORIAS_AFINES = 3, 5
SEGUNDOS_LISTAS = 120
EXPLORAR_FRACCION = 0.5
"""Parte de las peticiones que exploran cuando se pide `explorar=True`. La otra parte es el grupo de comparación."""
HUECO_NUEVO, HUECO_RESTO = 8, 9
"""Huecos 9 y 10 (se cuenta desde 0): los que menos valen (×0,37 del primero), así explorar cuesta poco."""
INTENTOS_EXPLORAR = 30
"""Cada cuánto se recalculan las listas de mejores por categoría (los números por producto van cambiando)."""

CON_NUEVO = ["carrito_hoy", "visto_sesion", "afin_cat", "afin_sub", "misma_cat", "pop", "tasa", "misma_sub"]
"""APO1: señales que tienen un peso distinto en compradores nuevos (is_new_user). Para un nuevo, el peso es el de la
señal + el de su `n_`. Faltan carrito_antiguo y ya_comprado: un comprador nuevo no tiene historial."""
PSEUDO_TIPO = 500.0
"""Suavizado de la tasa de un tipo o de una marca (en vistas del historial): con pocas vistas se acerca a la media."""

VISTA, CARRITO, COMPRA = 0, 1, 2
_TIPO = {"view": VISTA, "add_to_cart": CARRITO, "purchase": COMPRA}

SENALES = [
    ("carrito_hoy", "lo metió hoy en el carrito y no lo ha comprado"),
    ("carrito_antiguo", "lo metió en el carrito en el historial y nunca lo compró"),
    ("visto_sesion", "está en recent_views (y no lo ha comprado)"),
    ("ya_comprado", "ya lo compró (hoy o en el historial). Al recomendar se quita siempre"),
    ("afin_cat", "raíz de la parte de sus vistas que caen en la categoría del producto"),
    ("afin_sub", "ídem con la subcategoría"),
    ("misma_cat", "misma categoría que lo último visto"),
    ("pop", "log de la parte de las compras de la tienda que se lleva el producto"),
    ("tasa", "log de compras por vista del producto (suavizado)"),
    ("sin_vistas", "la petición no trae recent_views (home). No cambia el orden: ajusta el nivel"),
    ("nuevo", "comprador nuevo (is_new_user). No cambia el orden: ajusta el nivel"),
    ("misma_sub", "misma subcategoría que lo último visto, en productos que NO son suyos (ni carrito ni visto)"),
    ("tasa_tipo", "ORGÁNICO: log de compras por vista de los productos de su TIPO (subcategoría y tramo de precio)"),
    ("pop_tipo", "ORGÁNICO: log de lo que vende de media un producto de su tipo"),
    ("tasa_marca", "ORGÁNICO: log de compras por vista de su marca"),
] + [("n_" + n, f"solo en compradores NUEVOS: corrección del peso de {n}") for n in CON_NUEVO] + [
    ("cocompra_hoy", "HOY: quien miró lo que este comprador ha mirado, ¿cuánto acabó comprando este producto? (shop.events)"),
    ("cf_hist", "HISTORIAL: quien compró lo que este comprador compró, ¿cuánto compró también este producto? (60 días)"),
    ("gusto_hist", "HISTORIAL: gusto personal (factorización de los carritos y compras de 60 días): cuánto encaja con ESE comprador"),
    ("ctx_dev_sub", "CONTEXTO: cuánto más o menos se compra su subcategoría desde ese dispositivo (log, compras de 60 días)"),
    ("parte_ses_cat", "parte de recent_views que es de la categoría del producto"),
    ("prod_nuevo", "producto NUEVO: sin actividad en el historial de 60 días (sus números propios aún no dicen nada)"),
    ("precio_rel_sub", "log del precio dividido por el precio mediano de su subcategoría"),
    ("vistas_hoy_u", "log(1 + veces que ese comprador ha mirado hoy el producto)"),
    ("co_hay", "cocompra_hoy > 0: alguien que miró hoy lo mismo que este comprador acabó comprando el producto (sí/no)"),
    ("cf_hay", "cf_hist > 0: alguien que compró lo mismo que este comprador compró también el producto (sí/no)"),
    ("co_raiz", "raíz de cocompra_hoy: deja que esa señal suba deprisa al principio y luego se aplane"),
    ("cf_raiz", "raíz de cf_hist: ídem"),
]
"""Pocas a propósito. Probadas y quitadas el sáb 14/11 porque no mejoraban la validación (cada una movía el error
menos de un 0,1 %): es lo último visto, lo miró hoy fuera de la sesión, dispositivo, y "sabemos algo del comprador"
(sustituida por `nuevo`).
`misma_sub` se añadió el sáb por la noche (AP7; AP4-AP6 son las variantes de orden del Operador): en las compras
del historial, los más vendidos de la subcategoría de lo último visto aciertan el 20,5 % (recall@10 de
`python -m src.evaluar --recall`) frente al 9,2 % de la afinidad de categoría; en nuestro carrusel el efecto es
menor (×1,47 en productos sin relación; nada en los suyos).
Las señales nuevas van SIEMPRE al final de la lista: un modelo guardado antes de existir una señal no la trae y
se carga igual, con peso 0 para ella (así AP3 sigue dando exactamente las mismas listas)."""
NOMBRES = [n for n, _ in SENALES]
_I = {n: i for i, n in enumerate(NOMBRES)}
GRUPOS = {"o1": ["n_" + n for n in CON_NUEVO], "o2": ["tasa_tipo", "pop_tipo", "tasa_marca"]}
"""APO (dom madrugada; O = datos orgánicos). Dos ideas, cada una un grupo de señales, para un A/B con control común:
  o1 = pesos propios para compradores nuevos (el modelo predecía 3,24 compras/100 en nuevos y salían 2,67; en
       conocidos 1,29 y salían 1,75: las señales no valen lo mismo en los dos grupos).
  o2 = lo que se sabe del TIPO de producto en los 60 días de shop.history_events (compras por vista y ventas medias de
       su subcategoría y tramo de precio, y de su marca). Un producto nuevo no tiene números propios, pero sí tipo.
       Los números salen del historial; cuánto PESAN lo decide, como siempre, nuestro feedback.
    python -m src.aprendiz --nombre apoc --sin o1,o2                  # control
    python -m src.aprendiz --sin-descarga --nombre apo1 --sin o2      # control + o1
    python -m src.aprendiz --sin-descarga --nombre apo2 --sin o1      # control + o2"""
GRUPOS["h"] = ["cocompra_hoy", "cf_hist"]
"""APH1 (dom madrugada) = modelo HÍBRIDO: dos especialistas y un reparto por tipo de comprador.
  - `cocompra_hoy` aprende de la tienda de HOY, evento a evento: sirve para compradores nuevos y productos nuevos.
  - `cf_hist` usa los 60 días de compras de cada comprador conocido (filtrado colaborativo producto a producto).
  - Además se entrenan DOS regresiones, una solo con las respuestas a compradores nuevos y otra con las de conocidos
    (`--solo nuevos` / `--solo conocidos`); el servicio usa una u otra según `is_new_user`.
    python -m src.aprendiz --nombre aphc --sin o1,o2,h                                  # control (= AP7 reentrenado)
    python -m src.aprendiz --sin-descarga --nombre aph1_n --sin o1,o2 --solo nuevos     # especialista de nuevos
    python -m src.aprendiz --sin-descarga --nombre aph1_c --sin o1,o2 --solo conocidos  # especialista de conocidos"""
N_COCOMPRA, N_CF, VECINOS_CF, FUENTES_HOY = 15, 20, 30, 8
"""Candidatos que aporta cada fuente nueva, vecinos guardados por producto y cuántos productos mirados se usan."""
PSEUDO_COCOMPRA = 20.0
GRUPOS["x"] = ["gusto_hist", "ctx_dev_sub", "parte_ses_cat", "prod_nuevo", "precio_rel_sub", "vistas_hoy_u",
               "co_hay", "cf_hay", "co_raiz", "cf_raiz"]
"""AP8 (dom 06:00) = modelo de ELECCIÓN (`--eleccion`). Dos cambios sobre el híbrido:
  1. Cómo se aprende. Hasta ahora: "¿se compra este producto enseñado, sí o no?" (una fila por producto). Así los pesos
     mezclan dos cosas: qué PRODUCTO es mejor y qué PETICIÓN iba a comprar de todos modos (quien tiene carrito hoy compra
     más, enseñes lo que enseñes). Ahora: "de los 10 que enseñamos, ¿cuál eligió?" (logit condicional, solo listas con
     compra o clic). Lo que es igual para los 10 se cancela y queda solo lo que sirve para ORDENAR. Resultado: lo suyo
     pesa menos (carrito de hoy ×6 -> ×2,4) y la medida de elección mejora (EXPERIMENTOS, dom 06:00).
  2. Señales nuevas (grupo `x`), cada una comprobada quitándola o añadiéndola: gusto personal por factorización del
     historial, contexto (dispositivo × subcategoría), parte de la sesión en esa categoría, producto nuevo, precio
     frente al típico de su subcategoría y cuántas veces lo ha mirado hoy. Las cuatro últimas (`co_*`, `cf_*`) salen
     de probar árboles (LightGBM) encima del modelo: lo que encontraban era sobre todo la FORMA de cocompra_hoy y
     cf_hist (suben deprisa y se aplanan). Con esa forma dentro, el modelo sigue siendo una suma de pesos que se lee.
    python -m src.aprendiz --nombre ap8 --eleccion --sin o1,o2     # el modelo
    python -m src.aprendiz --sin-descarga --nombre ap8c --sin o1,o2,h,x   # control por producto (= AP7), mismo momento"""
N_GUSTO, DIM_GUSTO = 15, 64
"""Candidatos que aporta el gusto personal y nº de factores de la factorización."""
MAX_NUEVOS = 2
"""Tope de productos NUEVOS que no son del comprador en una lista del modelo de elección. Por qué: el modelo les da
×4,8 (y, enseñados, se compran ~2 veces más que uno conocido en el mismo hueco), pero solo los habíamos enseñado
~0,5 veces por lista; sin tope pondría ~5 por lista, mucho más allá de lo que hemos visto. Se sube cuando el feedback
del propio AP8 lo respalde. Los nuevos que el comprador ha mirado o metido en el carrito no cuentan para el tope."""
PESO_CLIC_ELECCION, L2_ELECCION = 0.3, 3.0
"""En el modelo de elección una lista con clic y sin compra cuenta 0,3 (hay 3 veces más clics que compras: afinan los
pesos sin mandar sobre las compras). Probado 0 / 0,15 / 0,3 / 0,5 / 1 y L2 1-30: la medida apenas cambia."""
_N_BASE = _I["tasa_marca"] + 1
_I_CO, _I_CF = _I["cocompra_hoy"], _I["cf_hist"]
_I_X = [_I[n] for n in GRUPOS["x"]]
_I_GUSTO, _I_X5 = _I["gusto_hist"], slice(_I["ctx_dev_sub"], _I["vistas_hoy_u"] + 1)
_I_FORMA = [_I[n] for n in ("co_hay", "cf_hay", "co_raiz", "cf_raiz")]
_I_BASE_NUEVO = [_I[n] for n in CON_NUEVO]
_I_NUEVO = [_I["n_" + n] for n in CON_NUEVO]
DE_PRODUCTO = ["pop", "tasa", "tasa_tipo", "pop_tipo", "tasa_marca"]
"""Señales que solo dependen del producto: con ellas se hacen las listas de mejores (candidatos)."""


# ---------------------------------------------------------------- RawTree (solo lectura)
def _query(sql: str, timeout: float = 300) -> list[dict]:
    from src.rawtree import query
    for intento in range(4):
        try:
            return query(sql, timeout=timeout)
        except Exception as e:  # red inestable: reintentar unas pocas veces
            if intento == 3:
                raise
            print(f"  reintento ({e})"[:160], flush=True)
            time.sleep(2 + 2 * intento)
    return []


def _ahora_servidor_ms() -> int:
    """La hora la da RawTree: el reloj del portátil va segundos por detrás (medido por el Operador)."""
    return int(_query("SELECT toUnixTimestamp64Milli(now64(3)) AS t", 20)[0]["t"])


def _entre(a_ms: int, b_ms: int) -> str:
    return (f"ts::DateTime64(3) >= fromUnixTimestamp64Milli(toInt64({a_ms})) "
            f"AND ts::DateTime64(3) < fromUnixTimestamp64Milli(toInt64({b_ms}))")


def _guardar(nombre: str, objeto) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / (nombre + ".tmp")
    tmp.write_bytes(pickle.dumps(objeto, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(DATA / nombre)  # sustitución de golpe: si se corta a medias no queda un archivo roto


def _leer(nombre: str, vacio):
    f = DATA / nombre
    return pickle.loads(f.read_bytes()) if f.exists() else vacio


def cargar_historial() -> dict:
    """Memoria de los 60 días: se baja UNA vez (el historial no cambia) y se guarda en data/aprendiz/.
    Devuelve compras y carritos por comprador, sus vistas por subcategoría y vistas/compras por producto."""
    h = _leer("historial.pkl", None)
    if h is not None:
        return _con_dispositivo(h)
    print("historial: bajando (solo la primera vez, ~1-2 min)...", flush=True)
    pares = {}
    for tipo in ("purchase", "add_to_cart"):
        d: dict[str, set] = defaultdict(set)
        for f in _query(f"""SELECT user_id::String AS u, product_id::String AS p FROM shop.history_events
                            WHERE event_type = '{tipo}' GROUP BY u, p LIMIT 5000000"""):
            d[sys.intern(f["u"])].add(sys.intern(f["p"]))
        pares[tipo] = dict(d)
    afin: dict[str, dict] = defaultdict(dict)
    trozos = 8
    for k in range(trozos):  # vistas por comprador y subcategoría (los eventos no traen categoría: se une al catálogo)
        for f in _query(f"""SELECT e.user_id::String AS u, c.s AS s, count() AS n
              FROM shop.history_events AS e
              INNER JOIN (SELECT product_id::String AS product_id, any(subcategory::String) AS s
                          FROM shop.catalog GROUP BY product_id) AS c ON e.product_id::String = c.product_id
              WHERE e.event_type = 'view' AND cityHash64(e.user_id::String) % {trozos} = {k}
              GROUP BY u, s LIMIT 5000000"""):
            afin[sys.intern(f["u"])][sys.intern(f["s"])] = int(f["n"])
    productos = {f["p"]: (int(f["v"]), int(f["c"])) for f in _query(
        """SELECT product_id::String AS p, countIf(event_type = 'view') AS v, countIf(event_type = 'purchase') AS c
           FROM shop.history_events GROUP BY p""")}
    h = {"compras": pares["purchase"], "carritos": pares["add_to_cart"], "afinidad": dict(afin), "productos": productos}
    _guardar("historial.pkl", h)
    print(f"historial: {len(h['compras'])} compradores con compras, {len(h['carritos'])} con carritos, "
          f"{len(h['afinidad'])} con vistas, {len(productos)} productos", flush=True)
    return _con_dispositivo(h)


def _con_dispositivo(h: dict) -> dict:
    """Añade al historial guardado las compras por dispositivo y producto (para `ctx_dev_sub`). Una consulta ligera,
    una sola vez. Si falla, la señal vale 0 y todo lo demás sigue igual (se vuelve a intentar en el siguiente arranque)."""
    if "dispositivo" not in h:
        try:
            filas = _query("""SELECT device::String AS d, product_id::String AS p, count() AS n FROM shop.history_events
                              WHERE event_type = 'purchase' GROUP BY d, p LIMIT 500000""", 60)
            h["dispositivo"] = [(f["d"], f["p"], int(f["n"])) for f in filas]
            _guardar("historial.pkl", h)
        except Exception as e:
            print(f"historial: sin compras por dispositivo ({e})"[:160], flush=True)
    return h


def bajar_vivo(hasta_ms: int | None = None) -> tuple[list, dict, dict]:
    """Descarga INCREMENTAL de lo de hoy: solo lo posterior a lo que ya hay en data/aprendiz/.
    Devuelve (eventos ordenados [(t_ms, user, producto, tipo)], peticiones {request_id: ...}, feedback {request_id: ...})."""
    hasta_ms = hasta_ms or _ahora_servidor_ms() - 5000
    ev = _leer("eventos.pkl", {"hasta_ms": INICIO_VIVO_MS, "filas": []})
    a = ev["hasta_ms"]
    while a < hasta_ms:
        b = min(a + VENTANA_MS, hasta_ms)
        filas = _query(f"""SELECT toUnixTimestamp64Milli(ts::DateTime64(3)) AS t, user_id::String AS u,
                           product_id::String AS p, event_type::String AS e FROM shop.events WHERE {_entre(a, b)}
                           LIMIT 1000000""")
        ev["filas"] += [(int(f["t"]), sys.intern(f["u"]), sys.intern(f["p"]), _TIPO[f["e"]])
                        for f in filas if f["e"] in _TIPO]
        print(f"  eventos {time.strftime('%H:%M', time.gmtime(a / 1000))} UTC: {len(filas)}", flush=True)
        a = b
    ev["filas"].sort(key=lambda f: f[0])
    ev["hasta_ms"] = hasta_ms
    _guardar("eventos.pkl", ev)

    # Peticiones y feedback: se vuelve a bajar un margen de 10 min por si alguna fila llegó tarde (se pisa por id).
    pf = _leer("respuestas.pkl", {"hasta_ms": INICIO_VIVO_MS, "peticiones": {}, "feedback": {}})
    if "version" not in pf:  # archivo de antes de guardar la versión: se baja una vez lo que falta
        pf["version"] = {}
        a = INICIO_VIVO_MS
        while a < pf["hasta_ms"]:
            b = min(a + 6 * VENTANA_MS, pf["hasta_ms"])
            for f in _query(f"""SELECT request_id::String AS rid, model_version::String AS mv FROM {_equipo()}.feedback
                                WHERE {_entre(a, b)} LIMIT 1000000"""):
                pf["version"][f["rid"]] = sys.intern(f["mv"] or "")
            a = b
    a = max(INICIO_VIVO_MS, pf["hasta_ms"] - 10 * 60 * 1000)
    while a < hasta_ms:
        b = min(a + VENTANA_MS, hasta_ms)
        for f in _query(f"""SELECT request_id::String AS rid, toUnixTimestamp64Milli(ts::DateTime64(3)) AS t,
                            user_id::String AS u, is_new_user::Bool AS nuevo, `context.device`::String AS dev,
                            recent_views AS rv FROM shop.requests WHERE {_entre(a, b)} LIMIT 1000000"""):
            pf["peticiones"][f["rid"]] = (int(f["t"]), sys.intern(f["u"]), bool(f["nuevo"]), sys.intern(f["dev"] or ""),
                                          tuple(sys.intern(p) for p in _productos_vistos(f["rv"])))
        n = 0
        for f in _query(f"""SELECT request_id::String AS rid, shown, clicked::String AS c, purchased::String AS p,
                            served_by::String AS sb, model_version::String AS mv FROM {_equipo()}.feedback
                            WHERE {_entre(a, b)} AND request_id::String != '__init__' LIMIT 1000000"""):
            ens = _lista(f["shown"])
            if len(ens) == 10:
                pf["feedback"][f["rid"]] = (tuple(sys.intern(p) for p in ens), f["c"] or "", f["p"] or "", f["sb"] == "team")
                pf["version"][f["rid"]] = sys.intern(f["mv"] or "")
                n += 1
        print(f"  feedback {time.strftime('%H:%M', time.gmtime(a / 1000))} UTC: {n}", flush=True)
        a = b
    pf["hasta_ms"] = hasta_ms
    _guardar("respuestas.pkl", pf)
    return ev["filas"], pf["peticiones"], pf["feedback"]


def _equipo() -> str:
    from src.rawtree import TEAM
    return TEAM


def _lista(x) -> list:
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except ValueError:
            return []
    return list(x or [])


def _productos_vistos(rv) -> list[str]:
    """recent_views -> [product_id, ...] de más antiguo a más reciente (puede traer repetidos)."""
    return [v["product_id"] for v in _lista(rv) if isinstance(v, dict) and v.get("product_id")]


# ---------------------------------------------------------------- el Mundo: lo que se sabe en un instante
class Mundo:
    """Estado por comprador y por producto, alimentado evento a evento. Un solo hilo escribe (`evento`,
    `ponerse_al_dia`) y otro lee (`recomendar`): el candado evita leer un diccionario a medio cambiar."""

    def __init__(self, catalogo: dict[str, dict], historial: dict | None = None):
        self.catalogo = catalogo
        h = historial or {"compras": {}, "carritos": {}, "afinidad": {}, "productos": {}}
        self.comprados: dict[str, set] = {u: set(s) for u, s in h["compras"].items()}
        self.carrito_antiguo: dict[str, set] = h["carritos"]          # incluye lo comprado: se resta al leer
        self.carrito_hoy: dict[str, set] = {}
        self.conocidos: set = set(h["productos"])   # productos con actividad en el historial: el resto son NUEVOS
        self.vistas_sub: dict[str, dict] = {u: dict(d) for u, d in h["afinidad"].items()}  # user -> subcat -> n
        self.vistas_p: dict[str, float] = {}
        self.compras_p: dict[str, float] = {}
        for p, (v, c) in h["productos"].items():
            self.vistas_p[p] = PESO_HISTORIAL * v
            self.compras_p[p] = PESO_HISTORIAL * c
        self.total_vistas = sum(self.vistas_p.values())
        self.total_compras = sum(self.compras_p.values())
        self.hasta_ms = INICIO_VIVO_MS
        self._recientes: set = set()
        self._candado = threading.Lock()
        self._tipos = _numeros_por_tipo(catalogo, h["productos"])
        self.vistas_hoy: dict[str, list] = {}       # comprador -> últimos productos mirados hoy (sin repetir)
        self.vistas_hoy_p: dict[str, int] = {}      # producto -> vistas de hoy
        self.cocompra: dict[str, dict] = {}         # X -> {Y: veces que alguien miró X hoy y después compró Y}
        self._cf = _vecinos_cf(h["compras"]) if historial else {}
        self.vistas_u: dict[str, dict] = {}         # comprador -> {producto: veces que lo ha mirado hoy} (los de vistas_hoy)
        self._gusto = _gusto_svd(h["compras"], h["carritos"]) if historial else None
        self._ctx_dev = _lift_dispositivo(catalogo, h.get("dispositivo") or [])

    # ---- escribir
    def evento(self, t: int, u: str, p: str, tipo: int) -> None:
        if tipo == VISTA:
            sub = self.catalogo.get(p, {}).get("subcategory")
            if sub:
                d = self.vistas_sub.setdefault(u, {})
                d[sub] = d.get(sub, 0) + 1
            self.vistas_p[p] = self.vistas_p.get(p, 0.0) + 1
            self.total_vistas += 1
            self.vistas_hoy_p[p] = self.vistas_hoy_p.get(p, 0) + 1
            v = self.vistas_hoy.setdefault(u, [])
            if p in v:
                v.remove(p)
            v.append(p)
            n = self.vistas_u.setdefault(u, {})
            n[p] = n.get(p, 0) + 1
            if len(v) > 20:
                n.pop(v[0], None)
                del v[0]
        elif tipo == CARRITO:
            self.carrito_hoy.setdefault(u, set()).add(p)
        elif tipo == COMPRA:
            self.comprados.setdefault(u, set()).add(p)
            self.compras_p[p] = self.compras_p.get(p, 0.0) + 1
            self.total_compras += 1
            for x in self.vistas_hoy.get(u, ()):
                if x != p:  # mirar X y comprar X ya lo cuenta `visto_sesion`
                    d = self.cocompra.setdefault(x, {})
                    d[p] = d.get(p, 0) + 1
        if t > self.hasta_ms:
            self.hasta_ms = t

    def aplicar(self, filas: list[tuple]) -> None:
        with self._candado:
            for f in filas:
                self.evento(*f)

    def ponerse_al_dia(self, solape_ms: int = 5000) -> int:
        """EN VIVO: trae de shop.events lo posterior a lo último aplicado (con unos segundos de solape por si algún
        evento llegó tarde; lo repetido se descarta). Una consulta ligera: llamar cada 1-2 s fuera del bucle que
        responde. Devuelve cuántos eventos nuevos entraron."""
        desde = self.hasta_ms - solape_ms
        filas = _query(f"""SELECT toUnixTimestamp64Milli(ts::DateTime64(3)) AS t, user_id::String AS u,
                           product_id::String AS p, event_type::String AS e FROM shop.events
                           WHERE ts::DateTime64(3) >= fromUnixTimestamp64Milli(toInt64({desde}))
                           ORDER BY ts LIMIT 200000""", timeout=10)
        nuevas = []
        for f in filas:
            clave = (int(f["t"]), f["u"], f["p"], f["e"])
            if clave not in self._recientes and f["e"] in _TIPO:
                self._recientes.add(clave)
                nuevas.append((clave[0], sys.intern(f["u"]), sys.intern(f["p"]), _TIPO[f["e"]]))
        self.aplicar(nuevas)
        if len(self._recientes) > 50000:  # solo hace falta recordar lo del solape
            self._recientes = {c for c in self._recientes if c[0] >= self.hasta_ms - 2 * solape_ms}
        return len(nuevas)

    @classmethod
    def al_dia(cls, catalogo: dict[str, dict]) -> "Mundo":
        """Para arrancar el servicio: historial + todos los eventos de hoy guardados + lo que falte hasta ahora."""
        mundo = cls(catalogo, cargar_historial())
        eventos, _, _ = bajar_vivo()
        mundo.aplicar(eventos)
        mundo.ponerse_al_dia()
        return mundo

    # ---- leer
    def foto(self, u: str) -> dict:
        """Copia pequeña de lo que se sabe de un comprador (para leerla sin candado después)."""
        with self._candado:
            comprados = self.comprados.get(u) or ()
            hoy = self.carrito_hoy.get(u) or ()
            antiguo = self.carrito_antiguo.get(u) or ()
            return {"u": u, "comprados": set(comprados),
                    "carrito_hoy": {p for p in hoy if p not in comprados},
                    "carrito_antiguo": {p for p in antiguo if p not in comprados and p not in hoy},
                    "vistas_sub": dict(self.vistas_sub.get(u) or {}),
                    "vistas_hoy": list(self.vistas_hoy.get(u) or ()),
                    "vistas_n": dict(self.vistas_u.get(u) or {})}

    def numeros_producto(self, p: str) -> tuple[float, float]:
        """(pop, tasa) de un producto con lo visto hasta ahora. Las dos son proporciones: no crecen con las horas."""
        compras = self.compras_p.get(p, 0.0)
        vistas = self.vistas_p.get(p, 0.0)
        # sin ningún dato todavía (Mundo vacío) se usa una tasa media típica para que el logaritmo exista
        media = self.total_compras / self.total_vistas if self.total_compras > 0 and self.total_vistas > 0 else 0.05
        pop = math.log((compras + 1.0) / (self.total_compras + 5000.0))
        tasa = math.log((compras + PSEUDO_VISTAS * media) / (vistas + PSEUDO_VISTAS))
        return pop, tasa


    def afines_hoy(self, fuentes: list[str]) -> dict[str, float]:
        """producto -> parte de las vistas de HOY de `fuentes` que acabaron en compra de ese producto (sumada)."""
        r: dict[str, float] = {}
        with self._candado:
            for x in fuentes:
                d = self.cocompra.get(x)
                if d:
                    k = 1.0 / (self.vistas_hoy_p.get(x, 0) + PSEUDO_COCOMPRA)
                    for y, n in d.items():
                        r[y] = r.get(y, 0.0) + n * k
        return r

    def afines_historial(self, comprados) -> dict[str, float]:
        """producto -> parecido (coseno) con lo que este comprador ya compró, sumado. Vacío si no ha comprado nada."""
        r: dict[str, float] = {}
        for x in comprados:
            for y, v in self._cf.get(x, ()):
                r[y] = r.get(y, 0.0) + v
        return r

    def numeros_tipo(self, d: dict) -> tuple[float, float, float]:
        """(tasa_tipo, pop_tipo, tasa_marca) de un producto según su ficha del catálogo. Sirve igual para uno nuevo."""
        t = self._tipos
        if not t:
            return 0.0, 0.0, 0.0
        sub = d.get("subcategory")
        tasa, pop = t["tipo"].get((sub, _tramo(t["cortes"].get(sub), d.get("price_eur"))), t["media"])
        return tasa, pop, t["marca"].get(d.get("brand"), t["media"][0])

    def gusto(self, u: str | None, productos: list[str]) -> np.ndarray | None:
        """Cuánto encaja cada producto con el gusto de ese comprador (sus factores · los del producto).
        None si el comprador no tiene carritos ni compras en el historial; 0 para un producto que no estaba."""
        g = self._gusto
        if not g or u not in g["u"]:
            return None
        return g["V"][[g["p"].get(p, -1) for p in productos]] @ g["U"][g["u"][u]]   # -1 = última fila, que es todo ceros

    def mejores_gusto(self, u: str | None, n: int) -> list[str]:
        """Los n productos del historial que mejor encajan con el gusto de ese comprador (candidatos)."""
        g = self._gusto
        if not g or u not in g["u"]:
            return []
        s = g["V"][:-1] @ g["U"][g["u"][u]]
        return [g["productos"][j] for j in np.argpartition(-s, n)[:n]]


def _vecinos_cf(compras: dict[str, set]) -> dict[str, list]:
    """Filtrado colaborativo producto a producto con las compras de los 60 días: para cada producto, los
    VECINOS_CF que más comparten compradores con él (coseno = compradores en común / raíz de los de cada uno).
    Se calcula una vez (~2 s) y se guarda en data/aprendiz/cf.pkl: el historial no cambia."""
    guardado = _leer("cf.pkl", None)
    if guardado is not None and guardado.get("n") == len(compras):
        return guardado["vecinos"]
    from scipy import sparse
    productos = sorted({p for s in compras.values() for p in s})
    if not productos:
        return {}
    idx = {p: i for i, p in enumerate(productos)}
    filas, cols = [], []
    for k, s in enumerate(compras.values()):
        filas += [k] * len(s)
        cols += [idx[p] for p in s]
    M = sparse.csr_matrix((np.ones(len(filas), dtype=np.float32), (filas, cols)), shape=(len(compras), len(productos)))
    C = (M.T @ M).toarray()
    n = np.sqrt(np.maximum(C.diagonal(), 1.0))
    C = C / n[:, None] / n[None, :]
    np.fill_diagonal(C, 0.0)
    vecinos = {}
    for i, p in enumerate(productos):
        mejores = np.argpartition(-C[i], VECINOS_CF)[:VECINOS_CF]
        vecinos[p] = [(productos[j], float(C[i, j])) for j in mejores if C[i, j] > 0]
    _guardar("cf.pkl", {"n": len(compras), "vecinos": vecinos})
    return vecinos


def _gusto_svd(compras: dict[str, set], carritos: dict[str, set]) -> dict | None:
    """Gusto personal por factorización (SVD truncada, DIM_GUSTO factores) de la tabla comprador × producto de los
    60 días: 1 si lo metió en el carrito, +1 si lo compró. Cada comprador y cada producto quedan resumidos en
    DIM_GUSTO números; su producto escalar dice cuánto encajan, también para productos que ese comprador nunca tocó.
    Se calcula una vez (~5 s) y se guarda en data/aprendiz/gusto.pkl: el historial no cambia."""
    clave = (len(compras), len(carritos), DIM_GUSTO)
    guardado = _leer("gusto.pkl", None)
    if guardado is not None and guardado.get("clave") == clave:
        return guardado
    from scipy import sparse
    from scipy.sparse.linalg import svds
    usuarios = sorted(set(compras) | set(carritos))
    productos = sorted({p for d in (compras, carritos) for s in d.values() for p in s})
    if min(len(usuarios), len(productos)) <= DIM_GUSTO:
        return None
    ui = {u: i for i, u in enumerate(usuarios)}
    pi = {p: i for i, p in enumerate(productos)}
    filas, cols = [], []
    for d in (compras, carritos):
        for u, s in d.items():
            filas += [ui[u]] * len(s)
            cols += [pi[p] for p in s]
    M = sparse.csr_matrix((np.ones(len(filas), dtype=np.float32), (filas, cols)), shape=(len(usuarios), len(productos)))
    U, s, Vt = svds(M, k=DIM_GUSTO)   # los pares repetidos (carrito y compra) se suman al construir la matriz
    g = {"clave": clave, "u": ui, "p": pi, "productos": productos, "U": (U * s).astype(np.float32),
         "V": np.vstack([Vt.T, np.zeros((1, DIM_GUSTO))]).astype(np.float32)}
    _guardar("gusto.pkl", g)
    return g


def _lift_dispositivo(catalogo: dict, filas: list[tuple]) -> dict[str, dict]:
    """dispositivo -> subcategoría -> log(parte de las compras hechas desde ese dispositivo que caen en la subcategoría
    / esa misma parte en toda la tienda). 0 = como la media. Sirve igual para un comprador o un producto nuevos."""
    por: dict = defaultdict(Counter)
    todo: Counter = Counter()
    for d, p, n in filas:
        sub = catalogo.get(p, {}).get("subcategory")
        if sub:
            por[d][sub] += n
            todo[sub] += n
    total = sum(todo.values())
    if not total:
        return {}
    return {d: {s: math.log((c[s] / sum(c.values()) + 1e-4) / (todo[s] / total + 1e-4)) for s in todo}
            for d, c in por.items()}


def _afines(mundo: Mundo, foto: dict, vistos: list[str]) -> tuple[dict, dict]:
    """(afines de hoy, afines del historial) de un comprador; se guardan en su foto para no calcularlos dos veces.
    Fuentes de hoy: lo mirado en la sesión (sin vistas, lo último que miró hoy) y su carrito de hoy."""
    if "co" not in foto:
        fuentes = list(dict.fromkeys(reversed(vistos)))[:FUENTES_HOY] or foto.get("vistas_hoy", [])[-FUENTES_HOY:]
        foto["co"] = mundo.afines_hoy(list(dict.fromkeys(fuentes + list(foto["carrito_hoy"]))))
        foto["cf"] = mundo.afines_historial(foto["comprados"])
    return foto["co"], foto["cf"]


def _tramo(cortes: tuple | None, precio) -> int:
    """Tramo de precio dentro de su subcategoría: 0 barato, 1 medio, 2 caro (tercios del catálogo)."""
    try:
        precio = float(precio)
    except (TypeError, ValueError):
        return 1
    if not cortes:
        return 1
    return 0 if precio <= cortes[0] else (1 if precio <= cortes[1] else 2)


def _numeros_por_tipo(catalogo: dict, productos: dict) -> dict:
    """Con las vistas y compras por producto del historial (shop.history_events, 60 días), agrupa por TIPO
    (subcategoría × tramo de precio) y por marca. Se calcula una vez: el historial no cambia."""
    if not productos:
        return {}
    precios: dict[str, list] = defaultdict(list)
    for d in catalogo.values():
        if d.get("subcategory") and d.get("price_eur") is not None:
            precios[d["subcategory"]].append(float(d["price_eur"]))
    cortes = {s: (float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))) for s, v in precios.items()}
    tipo: dict = defaultdict(lambda: [0.0, 0.0, 0])   # vistas, compras, nº de productos
    marca: dict = defaultdict(lambda: [0.0, 0.0])
    tv = tc = 0.0
    for p, (v, c) in productos.items():
        d = catalogo.get(p)
        if not d:
            continue
        a = tipo[(d.get("subcategory"), _tramo(cortes.get(d.get("subcategory")), d.get("price_eur")))]
        a[0] += v; a[1] += c; a[2] += 1
        b = marca[d.get("brand")]
        b[0] += v; b[1] += c
        tv += v; tc += c
    if not tv or not tc:
        return {}
    media = tc / tv

    def tasa(v: float, c: float) -> float:
        return math.log((c + PSEUDO_TIPO * media) / (v + PSEUDO_TIPO))

    def pop(c: float, n: int) -> float:  # misma escala que `pop`: lo que vendería un producto medio de ese tipo
        return math.log((PESO_HISTORIAL * c / max(n, 1) + 1.0) / (PESO_HISTORIAL * tc + 5000.0))

    return {"cortes": cortes, "tipo": {k: (tasa(v, c), pop(c, n)) for k, (v, c, n) in tipo.items()},
            "marca": {k: tasa(v, c) for k, (v, c) in marca.items()}, "media": (math.log(media), pop(tc, len(productos)))}


def senales(mundo: Mundo, foto: dict, vistos: list[str], nuevo: bool, candidatos: list[str],
            con_h: bool = True, dev: str = "", con_x: bool = True) -> np.ndarray:
    """Matriz (len(candidatos), len(SENALES)). La misma función al entrenar y al recomendar.
    con_h=False / con_x=False se saltan las señales de los grupos "h" / "x" (para los modelos que no las usan: quedan
    a 0). `dev` = context.device de la petición (solo lo usa `ctx_dev_sub`)."""
    co, cf = _afines(mundo, foto, vistos) if con_h or con_x else ({}, {})
    cat = mundo.catalogo
    ultimo = vistos[-1] if vistos else None
    d_ult = cat.get(ultimo, {}) if ultimo else {}
    en_sesion = set(vistos)
    total = sum(foto["vistas_sub"].values())
    por_cat: dict[str, int] = {}
    if total:
        sub_a_cat = _subcategoria_a_categoria(cat)
        for sub, n in foto["vistas_sub"].items():
            c = sub_a_cat.get(sub)
            if c:
                por_cat[c] = por_cat.get(c, 0) + n
    X = np.zeros((len(candidatos), len(SENALES)))
    for i, p in enumerate(candidatos):
        d = cat.get(p, {})
        comprado = p in foto["comprados"]
        pop, tasa = mundo.numeros_producto(p)
        suyo = p in foto["carrito_hoy"] or p in foto["carrito_antiguo"] or p in en_sesion
        X[i, :_N_BASE] = (p in foto["carrito_hoy"],
                p in foto["carrito_antiguo"],
                p in en_sesion and not comprado,
                comprado,
                math.sqrt(por_cat.get(d.get("category"), 0) / total) if total else 0.0,
                math.sqrt(foto["vistas_sub"].get(d.get("subcategory"), 0) / total) if total else 0.0,
                bool(ultimo) and d.get("category") == d_ult.get("category"),
                pop, tasa,
                not vistos, nuevo,
                bool(ultimo) and not suyo and not comprado and d.get("subcategory") == d_ult.get("subcategory"),
                *mundo.numeros_tipo(d))
        if co or cf:
            X[i, _I_CO] = math.sqrt(co.get(p, 0.0))
            X[i, _I_CF] = math.sqrt(cf.get(p, 0.0))
    if nuevo:
        X[:, _I_NUEVO] = X[:, _I_BASE_NUEVO]
    if con_x:
        gusto = mundo.gusto(foto.get("u"), candidatos)
        if gusto is not None:
            X[:, _I_GUSTO] = gusto
        X[:, _I_FORMA] = np.column_stack([X[:, _I_CO] > 0, X[:, _I_CF] > 0, np.sqrt(X[:, _I_CO]), np.sqrt(X[:, _I_CF])])
        lift = mundo._ctx_dev.get(dev) or {}
        mediana = _mediana_subcategoria(cat)
        cat_sesion = Counter(cat.get(p, {}).get("category") for p in vistos)
        vistas_n = foto.get("vistas_n") or {}
        conocidos = mundo.conocidos
        for i, p in enumerate(candidatos):
            d = cat.get(p, {})
            sub, precio = d.get("subcategory"), d.get("price_eur")
            X[i, _I_X5] = (lift.get(sub, 0.0),
                           cat_sesion.get(d.get("category"), 0) / len(vistos) if vistos else 0.0,
                           bool(conocidos) and p not in conocidos,
                           math.log(float(precio) / mediana[sub]) if precio and mediana.get(sub) else 0.0,
                           math.log1p(vistas_n.get(p, 0)))
    return X


_SUB_CAT: dict = {}
_MEDIANA: dict = {}


def _mediana_subcategoria(cat: dict) -> dict[str, float]:
    """subcategoría -> precio mediano de sus productos. Se recalcula si cambia el catálogo (precios nuevos)."""
    clave = (id(cat), len(cat))
    if _MEDIANA.get("clave") != clave:
        precios: dict[str, list] = defaultdict(list)
        for d in cat.values():
            if d.get("subcategory") and d.get("price_eur"):
                precios[d["subcategory"]].append(float(d["price_eur"]))
        _MEDIANA["mapa"] = {s: float(np.median(v)) for s, v in precios.items()}
        _MEDIANA["clave"] = clave
    return _MEDIANA["mapa"]


def _subcategoria_a_categoria(cat: dict) -> dict[str, str]:
    """subcategoría -> categoría (cada subcategoría pertenece a una sola). Se recalcula si cambia el catálogo."""
    clave = (id(cat), len(cat))
    if _SUB_CAT.get("clave") != clave:
        _SUB_CAT.clear()
        _SUB_CAT["clave"] = clave
        _SUB_CAT["mapa"] = {d.get("subcategory"): d.get("category") for d in cat.values() if d.get("subcategory")}
    return _SUB_CAT["mapa"]


# ---------------------------------------------------------------- el modelo
class Modelo:
    """Pesos de la regresión logística + las listas de "mejores" para sacar candidatos."""

    def __init__(self, pesos: dict[str, float], constante: float, puestos: list[float], info: dict | None = None):
        self.pesos = pesos                         # solo las señales con las que se entrenó este modelo
        self.w = np.array([pesos.get(n, 0.0) for n in NOMBRES])   # las que no tiene pesan 0
        self.constante = constante
        self.puestos = np.array(puestos)          # 10 valores; el del puesto 1 es 0
        self.info = info or {}
        self._listas: dict = {}
        self._listas_t = 0.0

    @staticmethod
    def archivo(nombre: str | None = None) -> Path:
        """data/aprendiz.json, o data/aprendiz_<nombre>.json para tener varios entrenos a la vez (A/B)."""
        return ARCHIVO_MODELO if not nombre else ARCHIVO_MODELO.with_name(f"aprendiz_{nombre}.json")

    @classmethod
    def cargar(cls, nombre: str | None = None) -> "Modelo | None":
        archivo = cls.archivo(nombre)
        if not archivo.exists():
            return None
        j = json.loads(archivo.read_text(encoding="utf-8"))
        if not set(j["pesos"]) <= set(NOMBRES):  # trae señales que este código no conoce: mejor no usarlo
            return None
        return cls(j["pesos"], j["constante"], j["puestos"], j.get("info"))

    def guardar(self, nombre: str | None = None) -> None:
        archivo = self.archivo(nombre)
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(json.dumps({"pesos": self.pesos, "constante": self.constante,
                                       "puestos": [float(x) for x in self.puestos], "info": self.info},
                                      indent=1, ensure_ascii=False), encoding="utf-8")

    def listas(self, mundo: Mundo, forzar: bool = False) -> dict:
        """Mejores productos (por las dos señales de producto) de la tienda, por categoría y por subcategoría."""
        if forzar or not self._listas or time.monotonic() - self._listas_t > SEGUNDOS_LISTAS:
            # el modelo de elección trae aparte los pesos con los que se hacen las listas (ver entrenar_eleccion)
            pesos = self.info.get("pesos_listas") or self.pesos
            w = [pesos.get(n, 0.0) for n in DE_PRODUCTO]
            con_tipo = any(w[2:])
            puntos = []
            for p, d in list(mundo.catalogo.items()):
                pop, tasa = mundo.numeros_producto(p)
                valor = w[0] * pop + w[1] * tasa
                if con_tipo:
                    valor += sum(a * b for a, b in zip(w[2:], mundo.numeros_tipo(d)))
                puntos.append((valor, p, d.get("category"), d.get("subcategory")))
            puntos.sort(reverse=True)
            por_cat: dict[str, list] = defaultdict(list)
            por_sub: dict[str, list] = defaultdict(list)
            for _, p, c, s in puntos:
                por_cat[c].append(p)
                por_sub[s].append(p)
            todos = [p for _, p, _, _ in puntos]
            # Para explorar: los productos nuevos (sin actividad en el historial) y los que quedan fuera de las
            # listas de mejores. Sin historial cargado no se sabe qué es nuevo: no se explora con nuevos.
            conocidos = mundo.conocidos
            self._listas = {"todos": todos, "cat": dict(por_cat), "sub": dict(por_sub),
                            "sub_a_cat": _subcategoria_a_categoria(mundo.catalogo),
                            "nuevos": {c: [p for p in l if p not in conocidos] for c, l in por_cat.items()} if conocidos else {},
                            "nuevos_todos": [p for p in todos if p not in conocidos] if conocidos else [],
                            "resto": {c: l[N_CATEGORIA:] for c, l in por_cat.items()},
                            "resto_todos": todos[N_GLOBAL:]}
            self._listas_t = time.monotonic()
        return self._listas

    def valor(self, X: np.ndarray) -> float:
        """Compras esperadas de una lista ya ordenada (suma de la probabilidad de cada hueco, con su puesto)."""
        z = self.constante + X @ self.w + self.puestos[:len(X)]
        return float((1 / (1 + np.exp(-z))).sum())


def _leer_peticion(peticion: dict) -> tuple[str, list[str], bool]:
    """(user_id, productos vistos de más antiguo a más reciente, ¿comprador nuevo?)."""
    nuevo = peticion.get("is_new_user")
    return (str(peticion.get("user_id") or ""), _productos_vistos(peticion.get("recent_views")),
            nuevo is True or nuevo == 1 or str(nuevo).lower() == "true")


def _dispositivo(peticion: dict) -> str:
    """context.device de la petición, venga `context` como objeto, como texto JSON o ya aplanado."""
    c = peticion.get("context")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            c = None
    if isinstance(c, dict) and c.get("device"):
        return str(c["device"])
    return str(peticion.get("context.device") or "")


def candidatos(mundo: Mundo, modelo: Modelo, foto: dict, vistos: list[str], vale) -> list[str]:
    listas = modelo.listas(mundo)
    cat = mundo.catalogo
    cand = list(reversed(vistos)) + list(foto["carrito_hoy"]) + list(foto["carrito_antiguo"])
    if vistos:
        d = cat.get(vistos[-1], {})
        cand += listas["cat"].get(d.get("category"), [])[:N_CATEGORIA]
        cand += listas["sub"].get(d.get("subcategory"), [])[:N_SUBCATEGORIA]
    subs = sorted(foto["vistas_sub"].items(), key=lambda kv: -kv[1])
    por_cat: Counter = Counter()
    for sub, n in subs:
        por_cat[listas["sub_a_cat"].get(sub)] += n
    for c, _ in por_cat.most_common(CATEGORIAS_AFINES):
        cand += listas["cat"].get(c, [])[:N_CATEGORIA]
    for sub, _ in subs[:SUBCATEGORIAS_AFINES]:
        cand += listas["sub"].get(sub, [])[:N_SUBCATEGORIA]
    if modelo.w[_I_CO] or modelo.w[_I_CF]:  # solo los modelos híbridos: los demás dan las mismas listas que antes
        co, cf = _afines(mundo, foto, vistos)
        cand += sorted(co, key=co.get, reverse=True)[:N_COCOMPRA] + sorted(cf, key=cf.get, reverse=True)[:N_CF]
    if modelo.w[_I_GUSTO]:  # solo los modelos que usan el gusto personal
        cand += mundo.mejores_gusto(foto.get("u"), N_GUSTO)
    cand += listas["todos"][:N_GLOBAL]
    return [p for p in dict.fromkeys(cand) if vale(p)]


def toca_explorar(request_id: str) -> bool:
    """¿Esta petición es de las que exploran? Depende solo del request_id: sale lo mismo en vivo y al analizar."""
    return bool(request_id) and zlib.crc32(str(request_id).encode()) % 1000 < EXPLORAR_FRACCION * 1000


def _al_azar(fuente: list[str] | None, azar: random.Random, vale) -> str | None:
    """Un producto válido de la lista, elegido al azar (todos con la misma probabilidad). None si no encuentra."""
    if not fuente:
        return None
    for _ in range(INTENTOS_EXPLORAR):
        p = fuente[azar.randrange(len(fuente))]
        if vale(p):
            return p
    return None


def _explorar(request_id: str, mundo: Mundo, modelo: Modelo, foto: dict, vistos: list[str], elegidos: list[str],
              vale) -> list[str]:
    """Cambia los huecos 9 y 10 por un producto nuevo y uno de fuera de las listas de mejores. Los dos salen de la
    categoría que le interesa al comprador (la de lo último visto; sin vistas, la que más mira; si no se sabe nada,
    de toda la tienda). Los 8 primeros huecos no se tocan."""
    listas = modelo.listas(mundo)
    categoria = mundo.catalogo.get(vistos[-1], {}).get("category") if vistos else None
    if not categoria and foto["vistas_sub"]:
        por_cat: Counter = Counter()
        for sub, n in foto["vistas_sub"].items():
            por_cat[listas["sub_a_cat"].get(sub)] += n
        categoria = por_cat.most_common(1)[0][0]
    azar = random.Random(zlib.crc32(str(request_id).encode()))  # misma petición -> misma respuesta
    salida = list(elegidos)
    for hueco, de_su_categoria, de_la_tienda in ((HUECO_NUEVO, listas["nuevos"].get(categoria), listas["nuevos_todos"]),
                                                 (HUECO_RESTO, listas["resto"].get(categoria), listas["resto_todos"])):
        for fuente in (de_su_categoria, de_la_tienda):
            p = _al_azar(fuente, azar, lambda p: vale(p) and p not in salida)
            if p:
                salida[hueco] = p
                break
    return salida


def recomendar(peticion: dict, mundo: Mundo, modelo: Modelo, disponibles: set[str], explorar: bool = False,
               forzar: dict[str, float] | None = None) -> list[str]:
    """10 product_id distintos, disponibles y nunca ya comprados por ese comprador, de mejor a peor.
    Con explorar=True, en la mitad de las peticiones los huecos 9 y 10 son de exploración (ver `_explorar`).
    `forzar` = {señal: multiplicador} sustituye el peso aprendido de esas señales en ESTA llamada, para probar en un
    A/B una señal más fuerte o más floja de lo que dice el feedback sin reentrenar. Ejemplo: {"misma_sub": 3.0}."""
    u, vistos, nuevo = _leer_peticion(peticion)
    foto = mundo.foto(u)
    comprados = foto["comprados"]

    def vale(p: str) -> bool:
        return p in disponibles and p not in comprados

    cand = candidatos(mundo, modelo, foto, vistos, vale)
    elegidos: list[str] = []
    w = modelo.w
    if forzar:
        w = w.copy()
        for nombre, multiplicador in forzar.items():
            w[_I[nombre]] = math.log(multiplicador)
    if cand:
        s = senales(mundo, foto, vistos, nuevo, cand, con_h=bool(w[_I_CO] or w[_I_CF]), dev=_dispositivo(peticion),
                    con_x=bool(w[_I_X].any())) @ w
        orden = np.argsort(-s, kind="stable")
        tope = modelo.info.get("max_nuevos")
        if tope is None or not mundo.conocidos:
            elegidos = [cand[i] for i in orden[:10]]
        else:  # modelo de elección: como mucho `tope` productos NUEVOS que no sean del comprador (ver MAX_NUEVOS)
            suyos, nuevos = foto["carrito_hoy"] | foto["carrito_antiguo"] | set(vistos), 0
            for i in orden:
                if cand[i] not in mundo.conocidos and cand[i] not in suyos:
                    if nuevos >= tope:
                        continue
                    nuevos += 1
                elegidos.append(cand[i])
                if len(elegidos) == 10:
                    break
    if len(elegidos) < 10:  # casi nunca: se completa con los mejores de la tienda y, en último caso, con cualquiera
        ya = set(elegidos)
        for fuente, exigir in ((modelo.listas(mundo)["todos"], vale), (sorted(disponibles), vale),
                               (sorted(disponibles), disponibles.__contains__)):
            for p in fuente:
                if len(elegidos) == 10:
                    break
                if p not in ya and exigir(p):
                    elegidos.append(p)
                    ya.add(p)
    if explorar and len(elegidos) == 10 and toca_explorar(peticion.get("request_id") or ""):
        elegidos = _explorar(peticion["request_id"], mundo, modelo, foto, vistos, elegidos, vale)
    return elegidos


# ---------------------------------------------------------------- entrenar
def repasar(mundo: Mundo, eventos: list[tuple], peticiones: list[tuple[int, str]], al_llegar) -> None:
    """Vuelve a pasar el día en orden: antes de cada petición (t, request_id) se aplican SOLO los eventos
    anteriores a ella (menos RETRASO_MS), y entonces se llama a al_llegar(request_id). Ninguna señal mira el futuro."""
    i, n = 0, len(eventos)
    for t, rid in peticiones:
        while i < n and eventos[i][0] < t - RETRASO_MS:
            mundo.evento(*eventos[i])
            i += 1
        al_llegar(rid)


def matriz_entreno(catalogo: dict, historial: dict, eventos: list, peticiones: dict, feedback: dict) -> dict:
    """Una fila por producto enseñado: señales + 9 columnas de puesto. Devuelve X, compra, clic, t y los ids."""
    orden = sorted((peticiones[rid][0], rid) for rid in feedback if rid in peticiones)
    mundo = Mundo(catalogo, historial)
    Xs, ys, cs = [], [], []

    def al_llegar(rid: str) -> None:
        _, u, nuevo, dev, vistos = peticiones[rid]
        ens, clic, compra, _ = feedback[rid]
        Xs.append(senales(mundo, mundo.foto(u), list(vistos), nuevo, list(ens), dev=dev))
        ys.append([p == compra for p in ens])
        cs.append([p == clic for p in ens])

    repasar(mundo, eventos, orden, al_llegar)
    puestos = np.tile(np.eye(10)[:, 1:], (len(orden), 1))
    return {"X": np.hstack([np.vstack(Xs), puestos]), "compra": np.array(ys, dtype=float).ravel(),
            "clic": np.array(cs, dtype=float).ravel(), "t": np.array([t for t, _ in orden]),
            "rids": [rid for _, rid in orden]}


def solo_compradores(datos: dict, nuevos: bool) -> dict:
    """Las respuestas de un solo tipo de comprador (nuevos o conocidos): para entrenar un especialista."""
    f = (datos["X"][:, _I["nuevo"]] > 0) == nuevos
    r = f.reshape(-1, 10)[:, 0]
    return {"X": datos["X"][f], "compra": datos["compra"][f], "clic": datos["clic"][f], "t": datos["t"][r],
            "rids": [x for x, ok in zip(datos["rids"], r) if ok]}


def _perdida(y: np.ndarray, z: np.ndarray) -> float:
    return float(-(y * z - np.logaddexp(0, z)).sum())


def ajustar(X: np.ndarray, y: np.ndarray, l2: float = L2) -> np.ndarray:
    """Regresión logística con numpy (método de Newton con paso controlado). Devuelve [constante, pesos...].
    Las columnas se ponen en la misma escala para ajustar y los pesos se devuelven en la escala original."""
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-12] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    reg = l2 * np.r_[0.0, np.ones(Z.shape[1] - 1)]
    w = np.zeros(Z.shape[1])
    w[0] = math.log(max(y.mean(), 1e-9) / max(1 - y.mean(), 1e-9))
    f = _perdida(y, Z @ w) + 0.5 * float(reg @ (w * w))
    for _ in range(100):
        p = 1 / (1 + np.exp(-(Z @ w)))
        g = Z.T @ (p - y) + reg * w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg + 1e-9)
        paso = np.linalg.solve(H, g)
        escala = 1.0
        while True:  # si el paso entero empeora, se prueba con la mitad
            w2 = w - escala * paso
            f2 = _perdida(y, Z @ w2) + 0.5 * float(reg @ (w2 * w2))
            if f2 <= f or escala < 1e-4:
                break
            escala /= 2
        mejora = f - f2
        w, f = w2, f2
        if mejora < 1e-7 * max(1.0, abs(f)):
            break
    return np.r_[w[0] - float((w[1:] * mu / sd).sum()), w[1:] / sd]


def _logloss(y: np.ndarray, z: np.ndarray) -> float:
    return _perdida(y, z) / len(y)


def entrenar(datos: dict, peso_clic: float, parte_validacion: float = 0.3, hablar: bool = True,
             sin: tuple[str, ...] = ()) -> tuple[Modelo, dict]:
    """Entrena y valida POR TIEMPO: se aprende con el principio del día y se comprueba con el final.
    El modelo que se devuelve está entrenado con TODO (la validación solo dice cuánto fiarse).
    `sin` = señales que este entreno NO usa (para un A/B limpio: mismo momento, mismos datos, una señal menos)."""
    X, compra, clic = datos["X"], datos["compra"], datos["clic"]
    k = len(SENALES)
    usadas = [n for n in NOMBRES if n not in sin]
    if sin:  # columna a cero = la señal no existe para este modelo (su peso sale 0 y no se guarda)
        X = X.copy()
        X[:, [_I[n] for n in sin if n in _I]] = 0.0
    t_fila = np.repeat(datos["t"], 10)
    corte = float(np.quantile(datos["t"], 1 - parte_validacion))
    ent, val = t_fila < corte, t_fila >= corte
    objetivo = np.maximum(compra, peso_clic * clic)

    def medir(w: np.ndarray) -> float:
        return _logloss(compra[val], w[0] + X[val] @ w[1:])

    w_puesto = ajustar(X[ent][:, k:], compra[ent])
    base = _logloss(compra[val], w_puesto[0] + X[val][:, k:] @ w_puesto[1:])
    w_val = ajustar(X[ent], objetivo[ent])
    if peso_clic > 0:  # el objetivo mezclado sube la media: se recalibra la constante con las compras
        w_val[0] += math.log(compra[ent].mean() / objetivo[ent].mean())
    info = {"respuestas": int(len(datos["t"])), "compras": int(compra.sum()), "clics": int(clic.sum()),
            "respuestas_validacion": int(val.sum() // 10), "compras_validacion": int(compra[val].sum()),
            "logloss_solo_puesto": base, "logloss_modelo": medir(w_val), "peso_clic": peso_clic}
    info["mejora_pct"] = 100 * (base - info["logloss_modelo"]) / base

    # Curva de aprendizaje: ¿mejora con más datos? Se entrena con el último 25/50/75/100 % de lo anterior al corte.
    t_ent = datos["t"][datos["t"] < corte]
    curva = []
    for parte in (0.25, 0.5, 0.75, 1.0):
        desde = float(np.quantile(t_ent, 1 - parte))
        f = ent & (t_fila >= desde)
        w = ajustar(X[f], objetivo[f])
        if peso_clic > 0:
            w[0] += math.log(max(compra[f].mean(), 1e-9) / objetivo[f].mean())
        curva.append((int(f.sum() // 10), int(compra[f].sum()), 100 * (base - medir(w)) / base))
    info["curva"] = curva

    # En las respuestas con compra de la validación: ¿el modelo (sin mirar el puesto) pone el comprado arriba?
    z = (X[val][:, :k] @ w_val[1:k + 1]).reshape(-1, 10)
    yv = compra[val].reshape(-1, 10)
    con = yv.sum(1) > 0
    puesto_real = yv[con].argmax(1)
    rango = (z[con] > z[con][np.arange(con.sum()), puesto_real][:, None]).sum(1)
    info["acierto_1"] = float((rango == 0).mean())
    info["acierto_3"] = float((rango < 3).mean())
    info["enseñado_1"] = float((puesto_real == 0).mean())
    info["enseñado_3"] = float((puesto_real < 3).mean())

    # Calibración: listas ordenadas por lo que predice el modelo, en 5 grupos: predicho frente a real.
    p_lista = (1 / (1 + np.exp(-(w_val[0] + X[val] @ w_val[1:])))).reshape(-1, 10).sum(1)
    real = yv.sum(1)
    grupos = np.array_split(np.argsort(p_lista), 5)
    info["calibracion"] = [(100 * float(p_lista[g].mean()), 100 * float(real[g].mean())) for g in grupos]

    w = ajustar(X, objetivo)
    if peso_clic > 0:
        w[0] += math.log(compra.mean() / objetivo.mean())
    modelo = Modelo({n: float(w[1 + _I[n]]) for n in usadas}, float(w[0]), [0.0] + [float(v) for v in w[k + 1:]], info)
    modelo_val = Modelo({n: float(w_val[1 + _I[n]]) for n in usadas}, float(w_val[0]),
                        [0.0] + [float(v) for v in w_val[k + 1:]], {"corte_ms": corte})
    if hablar:
        _contar(modelo, info)
    return modelo, {"modelo_validacion": modelo_val, "corte_ms": corte}


def ajustar_eleccion(X: np.ndarray, elegido: np.ndarray, peso: np.ndarray, l2: float = L2_ELECCION) -> np.ndarray:
    """Logit condicional (numpy + L-BFGS). X son listas de 10 filas seguidas; `elegido` marca UNA fila por lista y
    `peso` dice cuánto cuenta cada lista. Devuelve un peso por columna, sin constante: lo que es igual para los 10
    productos se cancela. Las columnas se ponen en la misma escala para ajustar y se devuelven en la original."""
    from scipy.optimize import minimize
    k = X.shape[1]
    sd = X.std(0)
    sd[sd < 1e-12] = 1.0
    Z = (X / sd).reshape(-1, 10, k)
    Y = elegido.reshape(-1, 10)

    def f(w: np.ndarray) -> tuple[float, np.ndarray]:
        z = Z @ w
        z -= z.max(1, keepdims=True)
        pr = np.exp(z)
        pr /= pr.sum(1, keepdims=True)
        perdida = -(peso * np.log((pr * Y).sum(1))).sum() + 0.5 * l2 * float(w @ w)
        return float(perdida), np.einsum("lik,li->k", Z, (pr - Y) * peso[:, None]) + l2 * w

    return minimize(f, np.zeros(k), jac=True, method="L-BFGS-B", options={"maxiter": 500}).x / sd


def _eleccion(z: np.ndarray, elegido: np.ndarray) -> np.ndarray:
    """Por lista: -log P(el elegido | uno de los 10 fue elegido). Al azar sale log(10) = 2,30; más bajo = ordena mejor.
    A diferencia del logloss por producto, no premia adivinar qué PETICIONES compran: solo mira el orden."""
    z = z.reshape(-1, 10)
    z = z - z.max(1, keepdims=True)
    return -np.log((np.exp(z) * elegido.reshape(-1, 10)).sum(1) / np.exp(z).sum(1))


def entrenar_eleccion(datos: dict, peso_clic: float = PESO_CLIC_ELECCION, parte_validacion: float = 0.3,
                      hablar: bool = True, sin: tuple[str, ...] = ()) -> tuple[Modelo, dict]:
    """AP8: aprende "de los 10 enseñados, cuál se eligió" con las listas que tuvieron compra (peso 1) o clic sin compra
    (peso_clic). Valida POR TIEMPO como `entrenar` y compara, en las mismas listas, con el modelo por producto (con sus
    mismas señales y con las de AP7). El modelo que se devuelve está entrenado con todo."""
    k = len(SENALES)
    fuera = set(sin) | {"sin_vistas", "nuevo"}     # iguales para los 10 productos de una lista: no pueden ordenar
    usadas = [n for n in NOMBRES if n not in fuera]
    X = datos["X"][:, [_I[n] for n in usadas] + list(range(k, k + 9))]
    compra, clic = datos["compra"].reshape(-1, 10), datos["clic"].reshape(-1, 10)
    corte = float(np.quantile(datos["t"], 1 - parte_validacion))
    antes = datos["t"] < corte
    con_compra, con_clic = compra.sum(1) > 0, clic.sum(1) > 0

    def filas(listas: np.ndarray) -> np.ndarray:
        return (np.flatnonzero(listas)[:, None] * 10 + np.arange(10)).ravel()

    def ajustar_con(filtro: np.ndarray, columnas=slice(None)) -> np.ndarray:
        partes = [(con_compra & filtro, compra, 1.0)]
        if peso_clic > 0:
            partes.append((con_clic & ~con_compra & filtro, clic, peso_clic))
        return ajustar_eleccion(np.vstack([X[filas(f)][:, columnas] for f, _, _ in partes]),
                                np.concatenate([y[f].ravel() for f, y, _ in partes]),
                                np.concatenate([np.full(int(f.sum()), p) for f, _, p in partes]))

    val_c, val_k = con_compra & ~antes, con_clic & ~antes
    fc, fk = filas(val_c), filas(val_k)

    def medir(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return _eleccion(z[fc], compra[val_c]), _eleccion(z[fk], clic[val_k])

    rng = np.random.default_rng(0)

    def diferencia(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
        d = a - b
        medias = d[rng.integers(0, len(d), (400, len(d)))].mean(1)
        return float(d.mean()), float(np.quantile(medias, .025)), float(np.quantile(medias, .975))

    w_val = ajustar_con(antes)
    z_val = X @ w_val
    e_compra, e_clic = medir(z_val)
    w_puesto = ajustar_con(antes, slice(len(usadas), None))
    info = {"tipo": "eleccion", "max_nuevos": MAX_NUEVOS, "respuestas": int(len(datos["t"])), "compras": int(con_compra.sum()),
            "clics": int(con_clic.sum()), "peso_clic": peso_clic, "l2": L2_ELECCION,
            "listas_validacion": (int(val_c.sum()), int(val_k.sum())),
            "eleccion": (float(e_compra.mean()), float(e_clic.mean())),
            "eleccion_solo_puesto": tuple(float(e.mean()) for e in medir(X[:, len(usadas):] @ w_puesto)), "frente_a": {}}

    # Los mismos datos con el modelo de siempre ("¿se compra este producto, sí o no?"): con estas señales y con las de AP7
    ent = np.repeat(antes, 10)
    for nombre, nombres in (("por producto, mismas señales", usadas + ["sin_vistas", "nuevo"]),
                            ("por producto, señales de AP7", NOMBRES[:12])):
        Xp = datos["X"][:, [_I[n] for n in nombres] + list(range(k, k + 9))]
        wp = ajustar(Xp[ent], datos["compra"][ent])
        r_compra, r_clic = medir(Xp @ wp[1:])
        info["frente_a"][nombre] = {"eleccion": (float(r_compra.mean()), float(r_clic.mean())),
                                    "compra": diferencia(e_compra, r_compra), "clic": diferencia(e_clic, r_clic)}
        if nombre.endswith("mismas señales"):  # las listas de mejores (candidatos) se siguen haciendo con estos pesos:
            # dentro de una lista la popularidad casi no ordena, pero solo hemos enseñado productos populares
            info["pesos_listas"] = {n: float(wp[1 + nombres.index(n)]) for n in DE_PRODUCTO if n in nombres}
        del Xp

    s = (z_val - X[:, len(usadas):] @ w_val[len(usadas):])[fc].reshape(-1, 10)   # sin el puesto
    puesto_real = compra[val_c].argmax(1)
    rango = (s > s[np.arange(len(s)), puesto_real][:, None]).sum(1)
    info["acierto_1"], info["acierto_3"] = float((rango == 0).mean()), float((rango < 3).mean())
    info["enseñado_1"], info["enseñado_3"] = float((puesto_real == 0).mean()), float((puesto_real < 3).mean())

    def modelo_de(w: np.ndarray, extra: dict) -> Modelo:
        # la constante solo sirve para `valor()`: se elige para que la media predicha sea la tasa real de compra
        z, a, b = X @ w, -30.0, 5.0
        for _ in range(50):
            c = (a + b) / 2
            a, b = (c, b) if (1 / (1 + np.exp(-(c + z)))).mean() < datos["compra"].mean() else (a, c)
        return Modelo({n: float(v) for n, v in zip(usadas, w)}, (a + b) / 2, [0.0] + [float(v) for v in w[len(usadas):]], extra)

    modelo = modelo_de(ajustar_con(np.ones(len(antes), dtype=bool)), info)
    modelo_val = modelo_de(w_val, {"corte_ms": corte, "pesos_listas": info["pesos_listas"], "max_nuevos": MAX_NUEVOS})
    if hablar:
        _contar_eleccion(modelo, info)
    return modelo, {"modelo_validacion": modelo_val, "corte_ms": corte}


def _contar_eleccion(modelo: Modelo, info: dict) -> None:
    print(f"\nENTRENADO (elección) con {info['compras']} listas con compra y {info['clics']} con clic, de "
          f"{info['respuestas']} respuestas nuestras; una lista con clic sin compra cuenta {info['peso_clic']}")
    print("Cada señal: por cuánto multiplica la probabilidad de ser el elegido de la lista (>1 sube, <1 baja)")
    for n, texto in SENALES:
        if n in modelo.pesos:
            print(f"  {n:<16} ×{math.exp(modelo.pesos[n]):7.2f}   {texto}")
    print("  puestos 2..10    " + " ".join(f"×{math.exp(v):.2f}" for v in modelo.puestos[1:]))
    nc, nk = info["listas_validacion"]
    print(f"\nVALIDACIÓN por tiempo (último 30 %: {nc} listas con compra, {nk} con clic; modelo entrenado solo con lo anterior)")
    print("  medida de elección = -log P(el elegido | la lista); al azar 2,303; más bajo = ordena mejor")
    print(f"    solo el puesto                    compra {info['eleccion_solo_puesto'][0]:.4f} · clic {info['eleccion_solo_puesto'][1]:.4f}")
    for nombre, r in info["frente_a"].items():
        print(f"    {nombre:<33} compra {r['eleccion'][0]:.4f} · clic {r['eleccion'][1]:.4f}")
    print(f"    ESTE MODELO (elección)            compra {info['eleccion'][0]:.4f} · clic {info['eleccion'][1]:.4f}")
    for nombre, r in info["frente_a"].items():
        print(f"    diferencia frente a «{nombre}» (IC 95 %, negativo = mejor): compra {r['compra'][0]:+.4f} "
              f"[{r['compra'][1]:+.4f}, {r['compra'][2]:+.4f}] · clic {r['clic'][0]:+.4f} [{r['clic'][1]:+.4f}, {r['clic'][2]:+.4f}]")
    print(f"  listas con compra: el modelo pone el comprado 1.º en el {100 * info['acierto_1']:.1f} % (nosotros lo pusimos "
          f"1.º en el {100 * info['enseñado_1']:.1f} %); entre los 3 primeros {100 * info['acierto_3']:.1f} % "
          f"(nosotros {100 * info['enseñado_3']:.1f} %)")


def _contar(modelo: Modelo, info: dict) -> None:
    print(f"\nENTRENADO con {info['respuestas']} respuestas nuestras ({info['compras']} compras, {info['clics']} clics); "
          f"peso de un clic = {info['peso_clic']}")
    print("Cada señal: por cuánto multiplica la probabilidad relativa de compra (>1 sube, <1 baja)")
    for n, texto in SENALES:
        if n in modelo.pesos:
            print(f"  {n:<16} ×{math.exp(modelo.pesos[n]):7.2f}   {texto}")
        else:
            print(f"  {n:<16}  (no se usa en este entreno)")
    print("  puestos 2..10    " + " ".join(f"×{math.exp(v):.2f}" for v in modelo.puestos[1:]))
    print(f"\nVALIDACIÓN por tiempo (último 30 %: {info['respuestas_validacion']} respuestas, "
          f"{info['compras_validacion']} compras; modelo entrenado solo con lo anterior)")
    print(f"  error (logloss) solo con el puesto {info['logloss_solo_puesto']:.5f} · con el modelo "
          f"{info['logloss_modelo']:.5f}  →  {info['mejora_pct']:+.1f} % mejor")
    print("  ¿mejora con más datos? (entrenando con la parte más reciente de lo anterior al corte)")
    for n, c, m in info["curva"]:
        print(f"    {n:>6} respuestas, {c:>4} compras → {m:+.1f} % mejor que solo el puesto")
    print(f"  respuestas con compra: el modelo pone el comprado 1.º en el {100 * info['acierto_1']:.1f} % "
          f"(nosotros lo pusimos 1.º en el {100 * info['enseñado_1']:.1f} %); entre los 3 primeros "
          f"{100 * info['acierto_3']:.1f} % (nosotros {100 * info['enseñado_3']:.1f} %)")
    print("  calibración (listas de menos a más prometedoras): compras/100 predichas → reales")
    print("    " + "  ".join(f"{a:.2f}→{b:.2f}" for a, b in info["calibracion"]))


def ensayo(catalogo: dict, historial: dict, eventos: list, peticiones: dict, feedback: dict, modelo: Modelo,
           corte_ms: float, n: int, semilla: int = 0) -> dict:
    """Ensayo general SIN ENVIAR NADA: vuelve a pasar el día y, en n peticiones reales posteriores al corte, llama a
    recomendar() con lo que se sabía en ese instante. Comprueba que las respuestas son válidas, cuánto tarda y cuánto
    VALDRÍA la lista según el modelo frente a la que enseñamos de verdad. OJO: el valor es una estimación del modelo
    (entrenado solo con lo anterior al corte), no una medición: la medición es un A/B en vivo."""
    from src import catalogo as cat_mod
    from src.validar import errores_respuesta
    disponibles = cat_mod.disponibles(catalogo)
    orden = sorted((peticiones[rid][0], rid) for rid in feedback if rid in peticiones)
    posteriores = [rid for t, rid in orden if t >= corte_ms]
    rng = np.random.default_rng(semilla)
    elegidas = set(rng.choice(posteriores, size=min(n, len(posteriores)), replace=False))
    mundo = Mundo(catalogo, historial)
    filas = []

    def al_llegar(rid: str) -> None:
        if rid not in elegidas:
            return
        t, u, nuevo, dev, vistos = peticiones[rid]
        ens, _, compra, _ = feedback[rid]
        peticion = {"request_id": rid, "user_id": u, "is_new_user": nuevo, "context": {"device": dev},
                    "recent_views": [{"product_id": p} for p in vistos]}
        t0 = time.perf_counter()
        items = recomendar(peticion, mundo, modelo, disponibles)
        ms = (time.perf_counter() - t0) * 1000
        foto = mundo.foto(u)
        con_exp = recomendar(peticion, mundo, modelo, disponibles, explorar=True)
        filas.append({
            "explora": con_exp != items,
            "exp_invalida": bool(errores_respuesta(con_exp, disponibles)) or any(p in foto["comprados"] for p in con_exp),
            "exp_mismos_8": con_exp[:8] == items[:8],
            "exp_nuevo": con_exp[HUECO_NUEVO] not in mundo.conocidos,
            "exp_resto": con_exp[HUECO_RESTO] != items[HUECO_RESTO],
            "valor_exp": modelo.valor(senales(mundo, foto, list(vistos), nuevo, con_exp, dev=dev)),
            "nuevo": nuevo, "sin_vistas": not vistos, "ms": ms,
            "invalida": bool(errores_respuesta(items, disponibles)),
            "con_comprado": any(p in foto["comprados"] for p in items),
            "valor_nuestro": modelo.valor(senales(mundo, foto, list(vistos), nuevo, list(ens), dev=dev)),
            "valor_nuevo": modelo.valor(senales(mundo, foto, list(vistos), nuevo, items, dev=dev)),
            "compra_real": bool(compra),
            "iguales": len(set(items) & set(ens)),
            "carritos": sum(p in foto["carrito_hoy"] or p in foto["carrito_antiguo"] for p in items),
            "primero_carrito": items[0] in foto["carrito_hoy"] or items[0] in foto["carrito_antiguo"],
            "primero_visto": items[0] in vistos,
        })

    modelo.listas(mundo, forzar=True)
    repasar(mundo, eventos, orden, al_llegar)

    def media(clave: str, f=lambda x: True) -> float:
        v = [x[clave] for x in filas if f(x)]
        return float(np.mean(v)) if v else float("nan")

    ms = sorted(x["ms"] for x in filas)
    r = {"n": len(filas), "invalidas": sum(x["invalida"] for x in filas),
         "con_comprado": sum(x["con_comprado"] for x in filas),
         "ms_p50": ms[len(ms) // 2], "ms_p95": ms[int(len(ms) * .95)], "ms_max": ms[-1]}
    print(f"\nENSAYO sobre {r['n']} peticiones reales posteriores al corte (sin enviar nada)")
    print(f"  inválidas: {r['invalidas']} · con algo ya comprado: {r['con_comprado']} · "
          f"tiempo p50 {r['ms_p50']:.2f} ms, p95 {r['ms_p95']:.2f} ms, máx {r['ms_max']:.2f} ms")
    print(f"  la lista nueva comparte de media {media('iguales'):.1f} de 10 productos con la que enseñamos; "
          f"lleva {media('carritos'):.1f} productos de sus carritos; el 1.º es de un carrito en el "
          f"{100 * media('primero_carrito'):.0f} % y algo visto en la sesión en el {100 * media('primero_visto'):.0f} %")
    print("  compras/100 peticiones SEGÚN EL MODELO (estimación, no medición):")
    for nombre, f in (("todas", lambda x: True), ("comprador nuevo", lambda x: x["nuevo"]),
                      ("comprador conocido", lambda x: not x["nuevo"]), ("sin vistas (home)", lambda x: x["sin_vistas"])):
        k = sum(1 for x in filas if f(x))
        print(f"    {nombre:<20} n={k:<5} lo que enseñamos {100 * media('valor_nuestro', f):.2f} "
              f"(real: {100 * media('compra_real', f):.2f}) → lista nueva {100 * media('valor_nuevo', f):.2f}")
        r[nombre] = (100 * media("valor_nuestro", f), 100 * media("compra_real", f), 100 * media("valor_nuevo", f))
    exp = lambda x: x["explora"]  # noqa: E731
    k = sum(1 for x in filas if exp(x))
    r["exploracion"] = {"n": k, "invalidas": sum(x["exp_invalida"] for x in filas),
                        "valor_sin": 100 * media("valor_nuevo", exp), "valor_con": 100 * media("valor_exp", exp)}
    print(f"  EXPLORACIÓN (explorar=True): exploran {k} de {len(filas)} peticiones ({100 * k / max(len(filas), 1):.0f} %); "
          f"inválidas o con algo ya comprado: {r['exploracion']['invalidas']}; los 8 primeros huecos intactos en el "
          f"{100 * media('exp_mismos_8', exp):.0f} %; hueco 9 con producto nuevo {100 * media('exp_nuevo', exp):.0f} %; "
          f"hueco 10 cambiado {100 * media('exp_resto', exp):.0f} %")
    print(f"    coste según el modelo en las que exploran (compras/100): sin explorar {r['exploracion']['valor_sin']:.2f} → "
          f"explorando {r['exploracion']['valor_con']:.2f}")
    return r


def informe_exploracion(peticiones: dict, feedback: dict, versiones: dict, historial: dict, horas: float = 3) -> None:
    """Qué pasó con lo explorado, leído del feedback. Por cada versión, compara las peticiones a las que les tocaba
    explorar (por su request_id) con las que no. En una versión que NO explora los dos grupos deben salir iguales
    (sirve de comprobación); en una que explora, la diferencia es lo que aporta y lo que cuesta explorar."""
    conocidos = set(historial["productos"])
    ts = [peticiones[rid][0] for rid in feedback if rid in peticiones]
    if not ts:
        return
    desde = max(ts) - horas * 3600 * 1000
    grupos: dict = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])  # n, nuevos h9, clic h9-10, compra h9-10, compra lista, clic lista, nuevos lista
    for rid, (ens, clic, compra, equipo) in feedback.items():
        if not equipo or rid not in peticiones or peticiones[rid][0] < desde:
            continue
        g = grupos[(versiones.get(rid, ""), toca_explorar(rid))]
        g[0] += 1
        g[1] += ens[HUECO_NUEVO] not in conocidos
        g[2] += clic in (ens[HUECO_NUEVO], ens[HUECO_RESTO])
        g[3] += compra in (ens[HUECO_NUEVO], ens[HUECO_RESTO])
        g[4] += bool(compra)
        g[5] += bool(clic)
        g[6] += sum(p not in conocidos for p in ens)
    print(f"\nEXPLORACIÓN en el feedback de las últimas {horas:g} h (respuestas servidas por nosotros)")
    print(f"  {'versión':<18}{'le tocaba':>10}{'respuestas':>12}{'hueco 9 nuevo':>15}{'clics h9-10':>13}{'compras h9-10':>15}"
          f"{'compras lista':>15}{'clics lista':>13}{'nuevos/lista':>14}")
    for (version, toca), g in sorted(grupos.items()):
        if g[0] < 50:
            continue
        print(f"  {version:<18}{'sí' if toca else 'no':>10}{g[0]:>12}{100 * g[1] / g[0]:>13.1f} %{g[2]:>13}{g[3]:>15}"
              f"{100 * g[4] / g[0]:>13.2f} %{100 * g[5] / g[0]:>11.2f} %{g[6] / g[0]:>14.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-descarga", action="store_true", help="no bajar nada nuevo: usar data/aprendiz/")
    ap.add_argument("--ensayo", type=int, default=0, help="nº de peticiones reales para ensayar recomendar()")
    ap.add_argument("--nombre", default=None,
                    help="guardar en data/aprendiz_<nombre>.json en vez de data/aprendiz.json (no pisa el que está en vivo)")
    ap.add_argument("--sin", default="",
                    help="señales que este entreno NO usa, separadas por comas (p. ej. misma_sub para el control de un A/B)")
    ap.add_argument("--solo", choices=["nuevos", "conocidos"], default=None,
                    help="entrenar solo con las respuestas a ese tipo de comprador (especialista del híbrido)")
    ap.add_argument("--peso-clic", type=float, default=None,
                    help=f"cuánto cuenta un clic sin compra como si fuera compra (por defecto {PESO_CLIC}; con --eleccion, "
                         f"{PESO_CLIC_ELECCION})")
    ap.add_argument("--eleccion", action="store_true",
                    help="AP8: aprender 'cuál de los 10 enseñados se elige' en vez de 'se compra o no' producto a producto")
    args = ap.parse_args()

    from src import catalogo as cat_mod
    catalogo = cat_mod.cargar_catalogo()
    historial = cargar_historial()
    if args.sin_descarga:
        eventos = _leer("eventos.pkl", {"filas": []})["filas"]
        pf = _leer("respuestas.pkl", {"peticiones": {}, "feedback": {}})
        peticiones, feedback = pf["peticiones"], pf["feedback"]
    else:
        eventos, peticiones, feedback = bajar_vivo()
    versiones = _leer("respuestas.pkl", {}).get("version", {})
    print(f"datos: {len(eventos)} eventos de hoy, {len(peticiones)} peticiones, {len(feedback)} respuestas con feedback")

    t0 = time.time()
    datos = matriz_entreno(catalogo, historial, eventos, peticiones, feedback)
    print(f"señales calculadas en {time.time() - t0:.0f} s ({len(datos['t'])} respuestas)")

    sin = tuple(s for n in args.sin.split(",") if n for s in GRUPOS.get(n, [n]))  # "o1" / "o2" = el grupo entero
    desconocidas = [n for n in sin if n not in NOMBRES]
    if desconocidas:
        raise SystemExit(f"--sin: señales que no existen: {desconocidas}. Las que hay: {NOMBRES}")
    if args.solo:
        datos = solo_compradores(datos, args.solo == "nuevos")
        sin += ("nuevo",) + tuple(GRUPOS["o1"])  # constantes dentro de un solo tipo de comprador
    sin = tuple(dict.fromkeys(sin))
    if args.eleccion:
        modelo, extra = entrenar_eleccion(datos, PESO_CLIC_ELECCION if args.peso_clic is None else args.peso_clic, sin=sin)
    else:
        modelo, extra = entrenar(datos, PESO_CLIC if args.peso_clic is None else args.peso_clic, sin=sin)
    modelo.info["entrenado_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    if args.ensayo:
        modelo.info["ensayo"] = ensayo(catalogo, historial, eventos, peticiones, feedback, extra["modelo_validacion"],
                                       extra["corte_ms"], args.ensayo)
    informe_exploracion(peticiones, feedback, versiones, historial)
    modelo.guardar(args.nombre)
    print(f"\nguardado {Modelo.archivo(args.nombre)} (no se sube al repo)")


if __name__ == "__main__":
    main()
