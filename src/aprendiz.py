"""e-samen · Aprendiz: un modelo que aprende de NUESTRO feedback en vivo. Dueño: Evaluación (Alex).

    python -m src.aprendiz                 # baja lo nuevo (feedback, peticiones, eventos), reentrena, valida y guarda
    python -m src.aprendiz --ensayo 3000   # además ensaya recomendar() sobre peticiones reales (NO envía nada)
    python -m src.aprendiz --sin-descarga  # reentrena con lo que ya hay en data/aprendiz/

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
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import threading
import time
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
"""Cada cuánto se recalculan las listas de mejores por categoría (los números por producto van cambiando)."""

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
]
"""Pocas a propósito. Probadas y quitadas el sáb 14/11 porque no mejoraban la validación (cada una movía el error
menos de un 0,1 %): es lo último visto, misma subcategoría que lo último visto, lo miró hoy fuera de la sesión,
dispositivo, y "sabemos algo del comprador" (sustituida por `nuevo`)."""
NOMBRES = [n for n, _ in SENALES]
_I = {n: i for i, n in enumerate(NOMBRES)}


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
        return h
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
                            served_by::String AS sb FROM {_equipo()}.feedback
                            WHERE {_entre(a, b)} AND request_id::String != '__init__' LIMIT 1000000"""):
            ens = _lista(f["shown"])
            if len(ens) == 10:
                pf["feedback"][f["rid"]] = (tuple(sys.intern(p) for p in ens), f["c"] or "", f["p"] or "", f["sb"] == "team")
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

    # ---- escribir
    def evento(self, t: int, u: str, p: str, tipo: int) -> None:
        if tipo == VISTA:
            sub = self.catalogo.get(p, {}).get("subcategory")
            if sub:
                d = self.vistas_sub.setdefault(u, {})
                d[sub] = d.get(sub, 0) + 1
            self.vistas_p[p] = self.vistas_p.get(p, 0.0) + 1
            self.total_vistas += 1
        elif tipo == CARRITO:
            self.carrito_hoy.setdefault(u, set()).add(p)
        elif tipo == COMPRA:
            self.comprados.setdefault(u, set()).add(p)
            self.compras_p[p] = self.compras_p.get(p, 0.0) + 1
            self.total_compras += 1
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
            return {"comprados": set(comprados),
                    "carrito_hoy": {p for p in hoy if p not in comprados},
                    "carrito_antiguo": {p for p in antiguo if p not in comprados and p not in hoy},
                    "vistas_sub": dict(self.vistas_sub.get(u) or {})}

    def numeros_producto(self, p: str) -> tuple[float, float]:
        """(pop, tasa) de un producto con lo visto hasta ahora. Las dos son proporciones: no crecen con las horas."""
        compras = self.compras_p.get(p, 0.0)
        vistas = self.vistas_p.get(p, 0.0)
        # sin ningún dato todavía (Mundo vacío) se usa una tasa media típica para que el logaritmo exista
        media = self.total_compras / self.total_vistas if self.total_compras > 0 and self.total_vistas > 0 else 0.05
        pop = math.log((compras + 1.0) / (self.total_compras + 5000.0))
        tasa = math.log((compras + PSEUDO_VISTAS * media) / (vistas + PSEUDO_VISTAS))
        return pop, tasa


def senales(mundo: Mundo, foto: dict, vistos: list[str], nuevo: bool, candidatos: list[str]) -> np.ndarray:
    """Matriz (len(candidatos), len(SENALES)). La misma función al entrenar y al recomendar."""
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
        X[i] = (p in foto["carrito_hoy"],
                p in foto["carrito_antiguo"],
                p in en_sesion and not comprado,
                comprado,
                math.sqrt(por_cat.get(d.get("category"), 0) / total) if total else 0.0,
                math.sqrt(foto["vistas_sub"].get(d.get("subcategory"), 0) / total) if total else 0.0,
                bool(ultimo) and d.get("category") == d_ult.get("category"),
                pop, tasa,
                not vistos, nuevo)
    return X


_SUB_CAT: dict = {}


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
        self.pesos = pesos
        self.w = np.array([pesos[n] for n in NOMBRES])
        self.constante = constante
        self.puestos = np.array(puestos)          # 10 valores; el del puesto 1 es 0
        self.info = info or {}
        self._listas: dict = {}
        self._listas_t = 0.0

    @classmethod
    def cargar(cls) -> "Modelo | None":
        if not ARCHIVO_MODELO.exists():
            return None
        j = json.loads(ARCHIVO_MODELO.read_text(encoding="utf-8"))
        if list(j["pesos"]) != NOMBRES:  # archivo de otra versión del código: mejor no usarlo que usarlo mal
            return None
        return cls(j["pesos"], j["constante"], j["puestos"], j.get("info"))

    def guardar(self) -> None:
        ARCHIVO_MODELO.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO_MODELO.write_text(json.dumps({"pesos": self.pesos, "constante": self.constante,
                                              "puestos": [float(x) for x in self.puestos], "info": self.info},
                                             indent=1, ensure_ascii=False), encoding="utf-8")

    def listas(self, mundo: Mundo, forzar: bool = False) -> dict:
        """Mejores productos (por las dos señales de producto) de la tienda, por categoría y por subcategoría."""
        if forzar or not self._listas or time.monotonic() - self._listas_t > SEGUNDOS_LISTAS:
            w_pop, w_tasa = self.pesos["pop"], self.pesos["tasa"]
            puntos = []
            for p, d in list(mundo.catalogo.items()):
                pop, tasa = mundo.numeros_producto(p)
                puntos.append((w_pop * pop + w_tasa * tasa, p, d.get("category"), d.get("subcategory")))
            puntos.sort(reverse=True)
            por_cat: dict[str, list] = defaultdict(list)
            por_sub: dict[str, list] = defaultdict(list)
            for _, p, c, s in puntos:
                por_cat[c].append(p)
                por_sub[s].append(p)
            self._listas = {"todos": [p for _, p, _, _ in puntos], "cat": dict(por_cat), "sub": dict(por_sub),
                            "sub_a_cat": _subcategoria_a_categoria(mundo.catalogo)}
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
    cand += listas["todos"][:N_GLOBAL]
    return [p for p in dict.fromkeys(cand) if vale(p)]


def recomendar(peticion: dict, mundo: Mundo, modelo: Modelo, disponibles: set[str]) -> list[str]:
    """10 product_id distintos, disponibles y nunca ya comprados por ese comprador, de mejor a peor."""
    u, vistos, nuevo = _leer_peticion(peticion)
    foto = mundo.foto(u)
    comprados = foto["comprados"]

    def vale(p: str) -> bool:
        return p in disponibles and p not in comprados

    cand = candidatos(mundo, modelo, foto, vistos, vale)
    elegidos: list[str] = []
    if cand:
        s = senales(mundo, foto, vistos, nuevo, cand) @ modelo.w
        elegidos = [cand[i] for i in np.argsort(-s, kind="stable")[:10]]
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
        _, u, nuevo, _, vistos = peticiones[rid]
        ens, clic, compra, _ = feedback[rid]
        Xs.append(senales(mundo, mundo.foto(u), list(vistos), nuevo, list(ens)))
        ys.append([p == compra for p in ens])
        cs.append([p == clic for p in ens])

    repasar(mundo, eventos, orden, al_llegar)
    puestos = np.tile(np.eye(10)[:, 1:], (len(orden), 1))
    return {"X": np.hstack([np.vstack(Xs), puestos]), "compra": np.array(ys, dtype=float).ravel(),
            "clic": np.array(cs, dtype=float).ravel(), "t": np.array([t for t, _ in orden]),
            "rids": [rid for _, rid in orden]}


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


def entrenar(datos: dict, peso_clic: float, parte_validacion: float = 0.3, hablar: bool = True) -> tuple[Modelo, dict]:
    """Entrena y valida POR TIEMPO: se aprende con el principio del día y se comprueba con el final.
    El modelo que se devuelve está entrenado con TODO (la validación solo dice cuánto fiarse)."""
    X, compra, clic = datos["X"], datos["compra"], datos["clic"]
    k = len(SENALES)
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
    modelo = Modelo({n: float(v) for n, v in zip(NOMBRES, w[1:k + 1])}, float(w[0]), [0.0] + [float(v) for v in w[k + 1:]],
                    info)
    modelo_val = Modelo({n: float(v) for n, v in zip(NOMBRES, w_val[1:k + 1])}, float(w_val[0]),
                        [0.0] + [float(v) for v in w_val[k + 1:]], {"corte_ms": corte})
    if hablar:
        _contar(modelo, info)
    return modelo, {"modelo_validacion": modelo_val, "corte_ms": corte}


def _contar(modelo: Modelo, info: dict) -> None:
    print(f"\nENTRENADO con {info['respuestas']} respuestas nuestras ({info['compras']} compras, {info['clics']} clics); "
          f"peso de un clic = {info['peso_clic']}")
    print("Cada señal: por cuánto multiplica la probabilidad relativa de compra (>1 sube, <1 baja)")
    for n, texto in SENALES:
        print(f"  {n:<16} ×{math.exp(modelo.pesos[n]):7.2f}   {texto}")
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
        t, u, nuevo, _, vistos = peticiones[rid]
        ens, _, compra, _ = feedback[rid]
        peticion = {"request_id": rid, "user_id": u, "is_new_user": nuevo,
                    "recent_views": [{"product_id": p} for p in vistos]}
        t0 = time.perf_counter()
        items = recomendar(peticion, mundo, modelo, disponibles)
        ms = (time.perf_counter() - t0) * 1000
        foto = mundo.foto(u)
        filas.append({
            "nuevo": nuevo, "sin_vistas": not vistos, "ms": ms,
            "invalida": bool(errores_respuesta(items, disponibles)),
            "con_comprado": any(p in foto["comprados"] for p in items),
            "valor_nuestro": modelo.valor(senales(mundo, foto, list(vistos), nuevo, list(ens))),
            "valor_nuevo": modelo.valor(senales(mundo, foto, list(vistos), nuevo, items)),
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
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-descarga", action="store_true", help="no bajar nada nuevo: usar data/aprendiz/")
    ap.add_argument("--ensayo", type=int, default=0, help="nº de peticiones reales para ensayar recomendar()")
    ap.add_argument("--peso-clic", type=float, default=PESO_CLIC,
                    help="cuánto cuenta un clic sin compra como si fuera compra (0 = solo compras)")
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
    print(f"datos: {len(eventos)} eventos de hoy, {len(peticiones)} peticiones, {len(feedback)} respuestas con feedback")

    t0 = time.time()
    datos = matriz_entreno(catalogo, historial, eventos, peticiones, feedback)
    print(f"señales calculadas en {time.time() - t0:.0f} s ({len(datos['t'])} respuestas)")

    modelo, extra = entrenar(datos, args.peso_clic)
    modelo.info["entrenado_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    if args.ensayo:
        modelo.info["ensayo"] = ensayo(catalogo, historial, eventos, peticiones, feedback, extra["modelo_validacion"],
                                       extra["corte_ms"], args.ensayo)
    modelo.guardar()
    print(f"\nguardado {ARCHIVO_MODELO} (no se sube al repo)")


if __name__ == "__main__":
    main()
