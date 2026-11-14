"""La función que elige los 10 productos. Dueño: Ranking.

    recommend(peticion, estado) -> lista de 10 product_id

`peticion` es una fila de shop.requests como diccionario:
    {"request_id", "ts", "user_id", "is_new_user", "context": {"device", "region", "page"},
     "recent_views": [{"product_id", "ts"}, ...]}
En la evaluación offline llega una fila de shop.history_sessions con las mismas claves (puede faltar alguna:
usa siempre .get()).

Reglas: NUNCA hace consultas a RawTree (solo lee `estado`), tarda < 5 ms y SIEMPRE devuelve 10 IDs distintos de
`estado.disponibles`. Si una pieza del Estado está vacía, se salta y se usa la siguiente.

ESTADO: versión inicial que ya combina las piezas en cuanto existan. Mientras covisitas, comprados,
populares_contexto y vecinos estén vacíos, se comporta como el baseline DE LAS DIAPOSITIVAS (más vendidos de la
categoría de lo último visto) y, si no hay vistas, como el del starter (más vendidos de siempre).
"""
from __future__ import annotations

from collections import Counter

from src.estado import Estado

NOMBRE = "v0-categoria"
"""Va en model_version (el Operador le añade el hash del commit). Formato: v<N>-<qué hace>, p. ej. v1-covisitas."""

DECAIMIENTO = 0.7
"""Peso de cada vista hacia atrás: la última vale 1, la anterior 0.7, la otra 0.49..."""


def recommend(peticion: dict, estado: Estado) -> list[str]:
    ya_comprado = estado.comprados.get(str(peticion.get("user_id", "")), set())

    def vale(pid: str) -> bool:
        return pid in estado.disponibles and pid not in ya_comprado

    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    puntos: Counter[str] = Counter()

    # 1) Co-visitas de lo que ha visto (lo más reciente pesa más).
    for k, pid in enumerate(reversed(vistos)):
        for pos, (otro, peso) in enumerate(estado.covisitas.get(pid, [])):
            puntos[otro] += (DECAIMIENTO ** k) * peso / (pos + 1)

    # 2) Productos nuevos (sin co-visitas): usar los de descripción parecida.
    for k, pid in enumerate(reversed(vistos)):
        if pid not in estado.covisitas:
            for pos, otro in enumerate(estado.vecinos.get(pid, [])):
                puntos[otro] += 0.5 * (DECAIMIENTO ** k) / (pos + 1)

    elegidos = [pid for pid, _ in puntos.most_common() if vale(pid)][:10]

    # 3) Relleno, en este orden: más vendidos de la categoría de lo último que vio (el baseline de las
    #    diapositivas), populares de su tipo de comprador, populares de siempre y cualquiera disponible.
    #    (Se asume que recent_views va de más antiguo a más reciente, como en el cuaderno oficial.)
    ultima_categoria = str(estado.catalogo.get(vistos[-1], {}).get("category", "")) if vistos else ""
    de_su_categoria = estado.populares_categoria.get(ultima_categoria, [])
    ctx = peticion.get("context") or {}
    por_contexto = estado.populares_contexto.get((str(ctx.get("device", "")), str(ctx.get("region", ""))), [])
    for fuente in (de_su_categoria, por_contexto, estado.populares, sorted(estado.disponibles)):
        for pid in fuente:
            if len(elegidos) == 10:
                return elegidos
            if pid not in elegidos and vale(pid):
                elegidos.append(pid)
    # Si incluso así faltan (el usuario lo compró casi todo), relajar la regla de "ya comprado".
    for pid in estado.populares + sorted(estado.disponibles):
        if len(elegidos) == 10:
            break
        if pid not in elegidos and pid in estado.disponibles:
            elegidos.append(pid)
    return elegidos
