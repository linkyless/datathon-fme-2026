"""Arranque en frío: compradores nuevos y productos nuevos. Dueño: Datos y relato.

D3 y D4 de docs/guias/datos.md. Los embeddings en sí los calcula src/embeddings.py.
"""
from __future__ import annotations

import base64
import json
import time

import numpy as np

from src.catalogo import cargar_populares
from src.embeddings import MAX_TEXTOS, PAUSA_S, pedir_embeddings, texto_producto
from src.rawtree import insertar, query

VECINOS = 20

# Mezcla de populares_contexto (medición de Alex, sáb: el contexto solo hundía el hit de los
# fríos 0,60 -> 0,46): los primeros GLOBALES items son los más vendidos de siempre (los conocidos
# buenos) y el resto de la lista es específica del (device, region). Así el relleno nunca puede
# rendir peor que los populares generales, y la parte de contexto conserva su señal para la cola.
GLOBALES_ADELANTE = 20


def cargar_populares_contexto(hasta: str | None = None) -> dict[tuple[str, str], list[str]]:
    """(device, region) -> lista de 50 por tipo de comprador, de más a menos (mezclada con los populares generales).

    `hasta` ('YYYY-MM-DD'): usar solo datos con ts < hasta. None = todo (en vivo).
    """
    filtro = f" AND ts::DateTime64(3) < '{hasta}'" if hasta else ""
    # NOTA de coherencia con covisitas.py: aquí cada EVENTO de compra cuenta (una sesión que compra
    # dos veces suma 2); covisitas cuenta CADA SESIÓN UNA VEZ (el mayor peso). Son señales distintas
    # a propósito: "cuántas compras de ese tipo de comprador" vs "en cuántas sesiones se relacionan".
    filas = query(
        "SELECT device::String d, region::String r, product_id::String pid, count() c "
        "FROM shop.history_events "
        f"WHERE event_type = 'purchase'{filtro} "
        "GROUP BY d, r, pid "
        "ORDER BY d, r, c DESC, pid "          # pid desempata: orden estable
        "LIMIT 50 BY d, r"
    )
    por_contexto: dict[tuple[str, str], list[str]] = {}
    for f in filas:
        por_contexto.setdefault((f["d"], f["r"]), []).append(f["pid"])

    # Mezcla segura: los `GLOBALES_ADELANTE` más vendidos de siempre delante,
    # luego lo específico del contexto, y se completa con más globales hasta 50.
    globales = cargar_populares(hasta)
    salida: dict[tuple[str, str], list[str]] = {}
    for pareja, suyos in por_contexto.items():
        vistos: set[str] = set()
        mezclados = globales[:GLOBALES_ADELANTE]
        vistos.update(mezclados)
        mezclados += [p for p in suyos if p not in vistos and not vistos.add(p)]
        mezclados += [p for p in globales if p not in vistos and not vistos.add(p)]
        salida[pareja] = mezclados[:50]
    return salida


_ATRIBUTOS_CACHE: tuple[float, dict[str, tuple[str, str]]] | None = None
_VECTORES_CACHE: tuple[float, list[str], np.ndarray] | None = None


def _atributos() -> dict[str, tuple[str, str]]:
    """product_id -> (category, subcategory) del catálogo, con cache de 10 min."""
    global _ATRIBUTOS_CACHE
    if _ATRIBUTOS_CACHE and time.time() - _ATRIBUTOS_CACHE[0] < 600:
        return _ATRIBUTOS_CACHE[1]
    filas = query("SELECT product_id::String pid, category::String c, subcategory::String s FROM shop.catalog")
    atributos = {f["pid"]: (f["c"], f["s"]) for f in filas}
    _ATRIBUTOS_CACHE = (time.time(), atributos)
    return atributos


def _vectores(hasta: str | None, cache_s: int = 120) -> tuple[list[str], np.ndarray]:
    """Vectores normalizados de scratch (ids en el orden de las filas de la matriz).

    La usan cargar_vecinos y cargar_covisitas_heredadas: dentro de un mismo refresco
    del Estado se decodifican una sola vez (cache corto). `hasta` corta por created_at.
    """
    global _VECTORES_CACHE
    if _VECTORES_CACHE and time.time() - _VECTORES_CACHE[0] < cache_s:
        return _VECTORES_CACHE[1], _VECTORES_CACHE[2]
    filas = query("SELECT product_id::String pid, vec FROM team_29.scratch WHERE tipo = 'emb_v1'")
    decodificados = {
        f["pid"]: np.frombuffer(base64.b64decode(f["vec"]), dtype=np.float16).astype(np.float32)
        for f in filas
        if f["pid"] and f["vec"]  # ignora filas rotas (p. ej. product_id vacío) por si quedara alguna
    }
    if hasta:  # quitar productos creados después del corte (created_at < hasta)
        creados = {
            f["product_id"]
            for f in query(f"SELECT product_id FROM shop.catalog WHERE created_at < '{hasta}'")
        }
        decodificados = {pid: v for pid, v in decodificados.items() if pid in creados}
    ids = list(decodificados)
    matriz = np.vstack([decodificados[p] for p in ids]) if ids else np.zeros((0, 1), dtype=np.float32)
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    matriz = matriz / normas
    _VECTORES_CACHE = (time.time(), ids, matriz)
    return ids, matriz


def _vecinos_detallados(
    hasta: str | None = None,
    ids: list[str] | None = None,
    matriz: np.ndarray | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """product_id -> [(vecino, similitud), ...]: los 20 mejores DENTRO de su subcategoría.

    Si en su subcategoría hay menos de 20, completa con los de su categoría (lo que pide
    la D4). Similitud = producto escalar (los vectores están normalizados).
    """
    if ids is None or matriz is None:
        ids, matriz = _vectores(hasta)
    n = len(ids)
    if not n:
        return {}
    atributos = _atributos()
    sub_de = [atributos.get(p, ("", ""))[1] for p in ids]
    cat_de = [atributos.get(p, ("", ""))[0] for p in ids]

    salida: dict[str, list[tuple[str, float]]] = {}
    incompletos: list[int] = []

    # Paso 1: los mejores dentro de la propia subcategoría.
    por_sub: dict[str, list[int]] = {}
    for i in range(n):
        por_sub.setdefault(sub_de[i], []).append(i)
    for grupo in por_sub.values():
        bloque = matriz[grupo] @ matriz[grupo].T
        for fila, i_global in enumerate(grupo):
            pares: list[tuple[int, float]] = []
            for j in np.argsort(-bloque[fila]):
                j = int(j)
                if j == fila:  # descartarse a sí mismo
                    continue
                pares.append((grupo[j], float(bloque[fila, j])))
                if len(pares) == VECINOS:
                    break
            salida[ids[i_global]] = [(ids[a], s) for a, s in pares]
            if len(pares) < VECINOS:
                incompletos.append(i_global)

    # Paso 2: quien no llegó a 20 vecinos, completar con la misma categoría.
    if incompletos:
        por_cat: dict[str, list[int]] = {}
        for i in range(n):
            por_cat.setdefault(cat_de[i], []).append(i)
        bloques: dict[int, np.ndarray] = {}  # por índice de categoría (tupla cat_de) no hace falta: por grupo
        for i in incompletos:
            ya = salida[ids[i]]
            if len(ya) >= VECINOS:
                continue
            grupo = por_cat[cat_de[i]]
            if len(grupo) - 1 <= len(ya):  # ni con toda la categoría llega a 20
                continue
            clave = cat_de[i]
            if clave not in bloques:
                bloques[clave] = matriz[grupo] @ matriz[grupo].T
            bloque = bloques[clave]
            fila = grupo.index(i)
            puestos = {a for a, _ in ya} | {ids[i]}
            pares: list[tuple[str, float]] = []
            for j in np.argsort(-bloque[fila]):
                j = int(j)
                candidato = ids[grupo[j]]
                if candidato in puestos:
                    continue
                pares.append((candidato, float(bloque[fila, j])))
                if len(ya) + len(pares) == VECINOS:
                    break
            salida[ids[i]] = ya + pares
    return salida


def cargar_vecinos(hasta: str | None = None) -> dict[str, list[str]]:
    """product_id -> los 20 productos con descripción más parecida (por embeddings), del más al menos parecido.

    Vecinos DENTRO de la subcategoría del producto (si faltan, de la categoría); los calcula
    `_vecinos_detallados` desde team_NN.scratch. `hasta` ('YYYY-MM-DD') corta por created_at
    para que la evaluación offline no vea productos futuros.
    """
    return {pid: [v for v, _ in pares] for pid, pares in _vecinos_detallados(hasta).items()}


def cargar_covisitas_heredadas(
    hasta: str | None = None,
    covisitas: dict[str, list[tuple[str, float]]] | None = None,
    vecinos_det: dict[str, list[tuple[str, float]]] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Co-visitas "prestadas" para productos que aún no tienen (D6, informe punto 8 / ficha T7).

    Para cada producto SIN fila en `covisitas`: la media ponderada por similitud de las
    co-visitas de sus vecinos (los de `cargar_vecinos`), recortada a 20 pares ordenados.
    Devuelve un dict con la MISMA forma que `covisitas` (product_id -> [(otro, peso), ...]),
    listo para mezclarse (decisión con el Operador: campo nuevo `covisitas_heredadas` en el
    Estado, o mezclarlas dentro de `covisitas`; ranking puede leerlas igual).
    """
    cov = covisitas or {}
    if not cov:
        return {}
    det = vecinos_det if vecinos_det is not None else _vecinos_detallados(hasta)
    heredadas: dict[str, list[tuple[str, float]]] = {}
    for pid, pares in det.items():
        if pid in cov:
            continue  # ya tiene co-visitas reales: no se toca
        filas = [(v, s) for v, s in pares if v in cov]  # solo vecinos con co-visitas conocidas
        if not filas:
            continue
        total = sum(s for _, s in filas)
        if total <= 0:
            continue
        puntos: dict[str, float] = {}
        for v, s in filas:
            for otro, peso in cov[v]:
                puntos[otro] = puntos.get(otro, 0.0) + s * float(peso)
        orden = sorted(((o, w / total) for o, w in puntos.items()), key=lambda t: -t[1])[:VECINOS]
        heredadas[pid] = [(o, round(w, 4)) for o, w in orden if w > 0]
    return heredadas


# ---------- Perfiles para R6 (pedido de Leonardo, sáb ~18:50) ----------
# (1) perfil_comprador: top 3 categorías/subcategorías/marcas y precio mediano por comprador
#     (history_events + shop.events en vivo). (2) perfil_producto: veces enseñado, veces comprado
#     y tasa de compra corregida por propensity, desde history_sessions con arrayJoin.
# Se guardan en team_29.scratch con sello de versión (`gen`) y corte (`hasta`), y se leen con
# cargar_perfiles(hasta), que solo devuelve filas de su mismo corte (respetar `hasta` = no mirar futuro).

PERFIL_COMPRADOR = "perfil_comprador_v2"
PERFIL_PRODUCTO = "perfil_producto_v2"
# v1 de estos tipos quedó CORRUPTA en scratch (top-3 reducido a caracteres, sáb ~18:00) y no se puede
# borrar sin permiso admin: al subir a v2, el cargador ignora automáticamente todas las filas v1.
PERFIL_GEN_MIN = 1800  # segundos: no reconstruir si lo último en scratch es más reciente


def _union_en_vivo(hasta: str | None) -> str:
    """shop.events solo cuenta EN VIVO (hasta=None): con corte, serían datos del futuro.

    Los eventos no traen categoría: la del catálogo se une aquí también.
    """
    if hasta:
        return ""
    return ("UNION ALL SELECT e2.user_id::String AS u, c2.c AS c, c2.s AS s, c2.b AS b, "
            "e2.price_eur::Float64 AS pr "
            "FROM shop.events e2 "
            "INNER JOIN (SELECT product_id::String pid, category::String c, "
            "subcategory::String s, brand::String b FROM shop.catalog) c2 "
            "ON e2.product_id::String = c2.pid "
            "WHERE e2.event_type::String = 'purchase'")


def _construir_perfil_comprador(hasta: str | None) -> dict[str, dict]:
    """user_id -> {"categorias": [3], "subcategorias": [3], "marcas": [3], "precio_mediano": €, "compras": n}.

    Top-3 por dimensión con LIMIT 3 BY (probado; los trucos con groupArray de tuplas llegan
    achatados por el camino Dynamic y estropean el perfil).
    """
    filtro = f" WHERE e.event_type::String = 'purchase' AND e.ts::DateTime64(3) < '{hasta}'" if hasta \
        else " WHERE e.event_type::String = 'purchase'"
    union = _union_en_vivo(hasta)
    dim_map = (("c", "categorias"), ("s", "subcategorias"), ("b", "marcas"))
    top: dict[str, dict] = {}
    for dim, clave in dim_map:
        sql = f"""
        SELECT u, v, n FROM (
          SELECT u, {dim} AS v, count() AS n FROM (
            SELECT e.user_id::String AS u, c.c AS c, c.s AS s, c.b AS b, e.price_eur::Float64 AS pr
            FROM shop.history_events e
            INNER JOIN (SELECT product_id::String pid, category::String c,
                               subcategory::String s, brand::String b FROM shop.catalog) c
                      ON e.product_id::String = c.pid
            {filtro}
            {union}
          ) GROUP BY u, v ORDER BY n DESC
        ) LIMIT 3 BY u"""
        for f in query(sql, timeout=600):
            d = top.setdefault(f["u"], {"categorias": [], "subcategorias": [], "marcas": [],
                                        "precio_mediano": 0.0, "compras": 0})
            d[clave].append(str(f["v"]))  # las filas llegan ya de más a menos compras por comprador
    filas_med = query(f"""
        SELECT u, round(quantile(0.5)(pr), 2) AS med, count() AS compras FROM (
          SELECT e.user_id::String AS u, c.c AS c, c.s AS s, c.b AS b, e.price_eur::Float64 AS pr
          FROM shop.history_events e
          INNER JOIN (SELECT product_id::String pid, category::String c,
                             subcategory::String s, brand::String b FROM shop.catalog) c
                    ON e.product_id::String = c.pid
          {filtro}
          {union}
        ) GROUP BY u""", timeout=600)
    for f in filas_med:
        d = top.setdefault(f["u"], {"categorias": [], "subcategorias": [], "marcas": [],
                                    "precio_mediano": 0.0, "compras": 0})
        d["precio_mediano"] = float(f["med"] or 0)
        d["compras"] = int(f["compras"])
    return top


def _construir_perfil_producto(hasta: str | None) -> dict[str, dict]:
    """product_id -> {"ensenado": n, "comprado": n, "tasa_ips": tasa corregida por propensity}."""
    filtro = f" WHERE ts::DateTime64(3) < '{hasta}'" if hasta else ""
    sql = f"""
    SELECT p, count() AS ensenado, countIf(p = pc) AS comprado,
           round(if(sum(w) > 0, sum(wc) / sum(w), 0), 4) AS tasa_ips
    FROM (
      SELECT z.1 AS p, purchased::String AS pc,
             if(z.2::Float64 > 0, 1.0 / z.2::Float64, 0) AS w,
             if(z.2::Float64 > 0 AND z.1 = pc, 1.0 / z.2::Float64, 0) AS wc
      FROM (SELECT arrayJoin(arrayZip(shown, propensity)) AS z, purchased::String AS purchased
            FROM shop.history_sessions{filtro})
    )
    GROUP BY p"""
    return {f["p"]: {"ensenado": int(f["ensenado"]), "comprado": int(f["comprado"]),
                     "tasa_ips": float(f["tasa_ips"])} for f in query(sql, timeout=600)}


def construir_perfiles(hasta: str | None = None, forzar: bool = False) -> tuple[dict, dict]:
    """Calcula los perfiles de R6, los sube a team_29.scratch y los devuelve.

    Cada fila lleva `gen` (sello de versión) y `hasta` (el corte: "LIVE" si es None): el
    cargador solo lee filas de su mismo corte. Si lo último subido es de hace
    < PERFIL_GEN_MIN segundos, no recalcula (scratch no crece sin control) salvo `forzar=True`.
    """
    h = hasta or "LIVE"
    try:
        ultimo = query(f"SELECT max(gen) AS g FROM team_29.scratch "
                       f"WHERE tipo = '{PERFIL_COMPRADOR}' AND hasta::String = '{h}'")[0]["g"] or 0
    except RuntimeError:  # scratch aún sin estas columnas (primera vez)
        ultimo = 0
    if not forzar and time.time() - ultimo < PERFIL_GEN_MIN:
        return cargar_perfiles(hasta)

    gen = int(time.time())
    comprador = _construir_perfil_comprador(hasta)
    producto = _construir_perfil_producto(hasta)
    filas = [{"tipo": PERFIL_COMPRADOR, "hasta": h, "gen": gen, "user_id": u, **datos}
             for u, datos in comprador.items()]
    filas += [{"tipo": PERFIL_PRODUCTO, "hasta": h, "gen": gen, "product_id": p, **datos}
              for p, datos in producto.items()]
    for i in range(0, len(filas), 500):
        insertar("scratch", filas[i : i + 500])
    print(f"perfiles subidos a scratch: {len(comprador)} compradores + {len(producto)} productos (gen {gen})")
    return comprador, producto


def _por_gen(filas: list[dict], clave: str) -> dict[str, dict]:
    """De las filas de scratch, se queda con la de mayor `gen` por clave (la más reciente)."""
    mejor: dict[str, dict] = {}
    for f in filas:
        k = str(f[clave])
        if k not in mejor or int(f.get("gen") or 0) > int(mejor[k].get("gen") or 0):
            mejor[k] = f
    return mejor


def _como_lista(v) -> list[str]:
    """Las columnas Dynamic a veces traen la lista tal cual y a veces texto JSON."""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.startswith("["):
        return [str(x) for x in json.loads(v)]
    return []


def cargar_perfiles(hasta: str | None = None) -> tuple[dict, dict]:
    """Lee de team_29.scratch los perfiles de R6 de ESTE corte.

    Devuelve (perfil_comprador, perfil_producto):
    - user_id -> {"categorias": [3], "subcategorias": [3], "marcas": [3], "precio_mediano": €, "compras": n}
    - product_id -> {"ensenado": n, "comprado": n, "tasa_ips": tasa corregida}
    Si aún no se han construido (python -m src.coldstart los construye, o el servicio), devuelve ({}, {}).
    """
    h = hasta or "LIVE"  # "" en Dynamic se comportaba mal en el WHERE: marcador explícito
    try:
        filas = query(f"SELECT user_id::String AS user_id, gen, categorias, subcategorias, marcas, "
                      f"precio_mediano, compras FROM team_29.scratch "
                      f"WHERE tipo = '{PERFIL_COMPRADOR}' AND hasta::String = '{h}'")
    except RuntimeError:  # primera vez: scratch aún no tiene estas columnas
        filas = []
    comprador = {u: {"categorias": _como_lista(f.get("categorias")),
                     "subcategorias": _como_lista(f.get("subcategorias")),
                     "marcas": _como_lista(f.get("marcas")),
                     "precio_mediano": float(f.get("precio_mediano") or 0),
                     "compras": int(f.get("compras") or 0), "gen": int(f.get("gen") or 0)}
                 for u, f in _por_gen(filas, "user_id").items()}
    try:
        filas_p = query(f"SELECT product_id::String AS product_id, gen, ensenado, comprado, tasa_ips "
                        f"FROM team_29.scratch WHERE tipo = '{PERFIL_PRODUCTO}' AND hasta::String = '{h}'")
    except RuntimeError:
        filas_p = []
    producto = {p: {"ensenado": int(f.get("ensenado") or 0), "comprado": int(f.get("comprado") or 0),
                    "tasa_ips": float(f.get("tasa_ips") or 0), "gen": int(f.get("gen") or 0)}
                for p, f in _por_gen(filas_p, "product_id").items()}
    return comprador, producto


def main() -> None:
    """python -m src.coldstart: construye y sube los perfiles de R6 (en vivo, hasta=None)."""
    comprador, producto = construir_perfiles(None, forzar=True)
    print(f"perfil comprador: {len(comprador)} | perfil producto: {len(producto)}")


def embeber_nuevos() -> int:
    """Embebe los 'new_product' de catalog_updates que aún no están en scratch y los sube.

    Devuelve cuántos productos nuevos se embeberon. El servicio (Operador) la llama
    cada ~5 minutos para que los productos nuevos tengan vecinos en caliente.
    """
    nuevos = [
        f
        for f in query(
            "SELECT product_id::String pid, category, subcategory, brand, description, ts "
            "FROM shop.catalog_updates WHERE change = 'new_product' "
            "ORDER BY ts::DateTime64(3) ASC"  # un producto puede tener varias filas: gana la última
        )
    ]
    ultimo: dict[str, dict] = {}
    for f in nuevos:
        ultimo[f["pid"]] = f
    ya_estan = {
        f["product_id"]
        for f in query("SELECT product_id::String product_id FROM team_29.scratch WHERE tipo = 'emb_v1'")
    }
    pendientes = [f for pid, f in ultimo.items() if pid and pid not in ya_estan]
    if not pendientes:
        return 0

    filas_out = []
    for i in range(0, len(pendientes), MAX_TEXTOS):
        lote = pendientes[i : i + MAX_TEXTOS]
        vectores = pedir_embeddings([texto_producto(f) for f in lote])
        for f, v in zip(lote, vectores):
            vec = np.asarray(v, dtype=np.float32)
            norma = float(np.linalg.norm(vec)) or 1.0
            vec = vec / norma
            filas_out.append((f, vec))
        if i + MAX_TEXTOS < len(pendientes):
            time.sleep(PAUSA_S)
    insertar(
        "scratch",
        [
            {
                "tipo": "emb_v1",
                "product_id": f["pid"],
                "vec": base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii"),
            }
            for f, v in filas_out
        ],
    )
    return len(filas_out)


if __name__ == "__main__":
    main()
