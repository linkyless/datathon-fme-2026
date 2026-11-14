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

import math
from collections import Counter

from src.estado import Estado

NOMBRE = "e-samen-v1"
"""Va en model_version, que SALE EN EL LEADERBOARD PÚBLICO: solo nombre en clave + versión (e-samen-v1, e-samen-v2...),
nunca lo que hace (los demás equipos lo leerían). Qué es cada versión se apunta en docs/EXPERIMENTOS.md."""

DECAIMIENTO = 0.7
"""Peso de cada vista hacia atrás: la última vale 1, la anterior 0.7, la otra 0.49..."""

REVISITAS = 0.0
"""Cuánto empujamos los productos que el comprador YA ha visto en esta sesión (y no ha comprado). Es un
multiplicador sobre la mejor puntuación de co-visitas: con 1.0, lo último que vio empata con el mejor candidato
y lo anterior queda justo detrás; con 0 se apaga.
APAGADO por defecto hasta que Evaluación lo mida (candidata a e-samen-v2: params={"revisitas": 0.3}).
Chequeo de Leonardo (3.530 compras del carrusel tras el 2026-11-07): compras en nuestros 10 sin re-visitas
14,76 %; con 0.1 -> 15,81 %; 0.3 -> 15,84 %; 1.0 -> 15,67 %.
Motivo (medido en shop.history_sessions): un producto ya visto se compró en 1,96 de cada 100 huecos del carrusel
antiguo frente a 0,12 los no vistos (sin corregir por posición)."""

AFIN_SUB = 0.0
AFIN_MARCA = 0.0
"""Afinidad fina: un candidato que comparte subcategoría con algo visto multiplica sus puntos por (1 + AFIN_SUB);
si comparte marca, por (1 + AFIN_MARCA). APAGADOS por defecto hasta medirlos."""

NORMALIZAR_FILA = False
"""Si True, los pesos de co-visita de cada producto visto se dividen por el mayor de su lista (el mejor vale 1).
Así cada vista aporta en la misma escala y los vecinos por descripción (productos nuevos) se pueden mezclar."""

PRECIO_COHERENTE = 0.0
"""Si el precio del candidato está entre la mitad y el doble del precio medio de lo visto, sus puntos se
multiplican por (1 + PRECIO_COHERENTE). APAGADO por defecto."""

LAMBDA_PRECIO = 0.0
"""Desempate por ingreso: puntos × (1 + LAMBDA_PRECIO · log(precio)). El desempate oficial es el ingreso esperado.
El deep research propone 0.05-0.1. APAGADO por defecto."""

PARAMS: dict = {"decaimiento": DECAIMIENTO, "revisitas": REVISITAS, "afin_sub": AFIN_SUB,
                "afin_marca": AFIN_MARCA, "normalizar_fila": NORMALIZAR_FILA,
                "precio_coherente": PRECIO_COHERENTE, "lambda_precio": LAMBDA_PRECIO,
                "usar_vecinos": False, "usar_contexto": False}
"""Parámetros por defecto. Las variantes A/B (src/servicio.py, Operador) pasan otro diccionario con las mismas
claves: así se prueban variantes en vivo sin tocar este archivo. Cada clave nueva se añade aquí con su valor.
usar_vecinos / usar_contexto: piezas de Artai (D3, D4). APAGADAS hasta que Evaluación o un A/B digan que suben:
sin interruptor, un simple reinicio del servicio cambiaría el modelo en vivo sin medirlo."""


def _precio(estado: Estado, pid: str) -> float:
    try:
        return float(estado.catalogo.get(pid, {}).get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


def recommend(peticion: dict, estado: Estado, params: dict | None = None) -> list[str]:
    p = {**PARAMS, **(params or {})}
    ya_comprado = estado.comprados.get(str(peticion.get("user_id", "")), set())

    def vale(pid: str) -> bool:
        return pid in estado.disponibles and pid not in ya_comprado

    vistos = [v["product_id"] for v in (peticion.get("recent_views") or []) if v.get("product_id")]
    puntos: Counter[str] = Counter()

    # 1) Co-visitas de lo que ha visto (lo más reciente pesa más).
    for k, pid in enumerate(reversed(vistos)):
        lista = estado.covisitas.get(pid, [])
        escala = lista[0][1] if (p["normalizar_fila"] and lista and lista[0][1] > 0) else 1.0
        for pos, (otro, peso) in enumerate(lista):
            puntos[otro] += (p["decaimiento"] ** k) * (peso / escala) / (pos + 1)

    # 2) Productos nuevos (sin co-visitas): usar los de descripción parecida.
    for k, pid in enumerate(reversed(vistos) if p["usar_vecinos"] else []):
        if pid not in estado.covisitas:
            for pos, otro in enumerate(estado.vecinos.get(pid, [])):
                puntos[otro] += 0.5 * (p["decaimiento"] ** k) / (pos + 1)

    # 3) Afinidad fina: misma subcategoría o marca que algo de lo visto.
    if (p["afin_sub"] > 0 or p["afin_marca"] > 0) and vistos:
        subs = {estado.catalogo.get(v, {}).get("subcategory") for v in vistos} - {None, ""}
        marcas = {estado.catalogo.get(v, {}).get("brand") for v in vistos} - {None, ""}
        for pid in puntos:
            datos = estado.catalogo.get(pid, {})
            factor = 1.0
            if datos.get("subcategory") in subs:
                factor += p["afin_sub"]
            if datos.get("brand") in marcas:
                factor += p["afin_marca"]
            puntos[pid] *= factor

    # 3b) Precio: coherente con lo visto y desempate por ingreso.
    if (p["precio_coherente"] > 0 or p["lambda_precio"] > 0) and puntos:
        precios_vistos = [_precio(estado, v) for v in vistos]
        precios_vistos = [x for x in precios_vistos if x > 0]
        medio = sum(precios_vistos) / len(precios_vistos) if precios_vistos else 0.0
        for pid in puntos:
            precio = _precio(estado, pid)
            if precio <= 0:
                continue
            if p["precio_coherente"] > 0 and medio > 0 and medio / 2 <= precio <= medio * 2:
                puntos[pid] *= 1 + p["precio_coherente"]
            if p["lambda_precio"] > 0:
                puntos[pid] *= 1 + p["lambda_precio"] * math.log(max(precio, 1.0))

    # 4) Re-visitas: lo que ya ha mirado y no ha comprado. Cada producto cuenta una vez, por su vista más reciente.
    if p["revisitas"] > 0 and vistos:
        tope = max(puntos.values(), default=1.0)
        contados: set[str] = set()
        for k, pid in enumerate(reversed(vistos)):
            if pid not in contados:
                contados.add(pid)
                puntos[pid] += p["revisitas"] * (p["decaimiento"] ** k) * tope

    elegidos = [pid for pid, _ in puntos.most_common() if vale(pid)][:10]

    # 5) Relleno, en este orden: más vendidos de la categoría de lo último que vio (el baseline de las
    #    diapositivas), populares de su tipo de comprador, populares de siempre y cualquiera disponible.
    #    (Se asume que recent_views va de más antiguo a más reciente, como en el cuaderno oficial.)
    ultima_categoria = str(estado.catalogo.get(vistos[-1], {}).get("category", "")) if vistos else ""
    de_su_categoria = estado.populares_categoria.get(ultima_categoria, [])
    ctx = peticion.get("context") or {}
    por_contexto = (estado.populares_contexto.get((str(ctx.get("device", "")), str(ctx.get("region", ""))), [])
                    if p["usar_contexto"] else [])
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
