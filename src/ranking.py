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
from itertools import chain

import numpy as np

from src import puntuacion
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

TENDENCIA = 0.0
"""R4. Exponente η del empujón por tendencia: puntos × lift^η, con lift de src/tendencias.py (1 = normal,
3 = el triple de actividad que de costumbre en la última media hora). La investigación propone 0.3-0.5.
Se aplica a los candidatos de co-visitas Y al orden de las listas de relleno (populares de la categoría y
populares de siempre), que es lo que reciben la v0 y los compradores sin vistas. APAGADO por defecto.
Chequeo de Leonardo en vivo (sáb ~17:00): los productos con lift >= 1.3 siguen la media hora SIGUIENTE a
1,14× su ritmo normal (mediana; media 1,32) y los de lift < 0.8 a 0,84× (media 0,91): la tendencia se mantiene."""

RELLENO_REORDENADO = 30
"""Con tendencia encendida, cuántos primeros puestos de cada lista de relleno se reordenan."""

CANDIDATOS_POR_FUENTE = 40
"""R5. Con usar_modelo, cuántos candidatos se toman de cada fuente antes de que el modelo los ordene."""

FRIO_HISTORIAL = 1.2
"""Frio con historial (sáb ~18:00, medido): el 93,7 % de los compradores SIN vistas ya compró antes, y el
100 % de las compras frías del test son RECOMPRAS de algo que ya habían comprado (el modelo oculto lo premia:
anuncio de los organizadores de las 17:40). Con este parámetro, a un comprador sin vistas se le siembran las
co-visitas de sus productos comprados y cada producto comprado se empuja 1,2× el mejor candidato sembrado,
para que las recompras encabecen la lista (los equipos punteros convierten 3× más en el segmento frío).
Ajustable en el A/B (params={"frio_historial": 0.0} lo apaga)."""

PARAMS: dict = {"decaimiento": DECAIMIENTO, "revisitas": REVISITAS, "afin_sub": AFIN_SUB,
                "afin_marca": AFIN_MARCA, "normalizar_fila": NORMALIZAR_FILA,
                "precio_coherente": PRECIO_COHERENTE, "lambda_precio": LAMBDA_PRECIO,
                "usar_vecinos": False, "usar_contexto": False, "tendencia": TENDENCIA,
                "usar_modelo": False, "filtrar_comprados": False, "frio_historial": FRIO_HISTORIAL}
"""filtrar_comprados = False desde el sáb 17:40: medido tres veces (offline 0,86 -> 0,32 con el filtro;
en vivo CF 1,19 vs C 1,02; y los organizadores confirmaron que el modelo oculto premia recomprar).
"""
"""usar_modelo (R5): los 10 los elige la regresión logística de src/puntuacion.py en vez de la suma de puntos.
Necesita data/puntuacion.json (python -m src.puntuacion); si no existe, se ignora. En este modo NO se quita lo ya
comprado: el modelo aprendió que haberlo comprado antes SUBE la probabilidad de compra (×1,83). APAGADO."""
"""Parámetros por defecto. Las variantes A/B (src/servicio.py, Operador) pasan otro diccionario con las mismas
claves: así se prueban variantes en vivo sin tocar este archivo. Cada clave nueva se añade aquí con su valor.
usar_vecinos / usar_contexto: piezas de Artai (D3, D4). APAGADAS hasta que Evaluación o un A/B digan que suben:
sin interruptor, un simple reinicio del servicio cambiaría el modelo en vivo sin medirlo."""


def _todos(estado: Estado):
    """Todos los disponibles en orden fijo. Es un generador: solo se ordena si de verdad se llega a usar
    (casi nunca), porque ordenar 5.000 IDs era el 95 % del tiempo de recommend()."""
    yield from sorted(estado.disponibles)


def _precio(estado: Estado, pid: str) -> float:
    try:
        return float(estado.catalogo.get(pid, {}).get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


def _por_tendencia(lista: list[str], lift: dict[str, float], eta: float) -> list[str]:
    """Reordena los primeros RELLENO_REORDENADO de una lista de populares. Cada puesto vale 1/log2(puesto + 2)
    (el 1º vale 1, el 2º 0,63, el 3º 0,5...) multiplicado por lift^η: un producto en plena subida adelanta unos
    pocos puestos, pero uno normal no se hunde. El resto de la lista queda igual detrás."""
    cabeza = lista[:RELLENO_REORDENADO]
    puntos = {pid: lift.get(pid, 1.0) ** eta / math.log2(pos + 2) for pos, pid in enumerate(cabeza)}
    return sorted(cabeza, key=puntos.__getitem__, reverse=True) + lista[RELLENO_REORDENADO:]


def _con_modelo(peticion: dict, estado: Estado, vistos: list[str], puntos: Counter, lift: dict[str, float],
                eta: float) -> list[str] | None:
    """R5. Junta candidatos (populares de su categoría, populares de siempre, co-visitas, lo que ha visto y lo que
    ya compró), los puntúa con la regresión logística y devuelve los 10 mejores. None si no hay modelo."""
    modelo = puntuacion.modelo_guardado()
    if modelo is None:
        return None
    n = CANDIDATOS_POR_FUENTE
    ultima_categoria = str(estado.catalogo.get(vistos[-1], {}).get("category", "")) if vistos else ""
    comprados = estado.comprados.get(str(peticion.get("user_id", "")), set())
    fuentes = (estado.populares_categoria.get(ultima_categoria, [])[:n], estado.populares[:n],
               [pid for pid, _ in puntos.most_common(n)], list(reversed(vistos)), sorted(comprados)[:n])
    candidatos = list(dict.fromkeys(pid for pid in chain(*fuentes) if pid in estado.disponibles))
    if not candidatos:
        return []
    s = puntuacion.puntuar(peticion, candidatos, estado, modelo)
    if eta > 0 and lift:  # tendencias: puntos × lift^η  ==  puntuación + η·log(lift)
        s = s + eta * np.log([lift.get(pid, 1.0) for pid in candidatos])
    return [candidatos[i] for i in np.argsort(-s, kind="stable")[:10]]


def recommend(peticion: dict, estado: Estado, params: dict | None = None) -> list[str]:
    p = {**PARAMS, **(params or {})}
    # filtrar_comprados (R2): quitar lo que ya compró. En el historial la gente recompra (Evaluación: el 61,6 % de
    # las compras del test son de algo ya comprado; con el filtro, purchase_hit 0.86 -> 0.32). Sigue en True por
    # defecto SOLO porque src/prueba_local.py (línea 54) lo exige; se apagará cuando el Operador adapte esa prueba.
    ya_comprado = (estado.comprados.get(str(peticion.get("user_id", "")), set())
                   if p["filtrar_comprados"] else set())

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

    # 1b) Comprador "frío" (sin vistas) pero con historial de compra: recomendarle lo que YA compró
    #     (recompra, que el modelo oculto premia) y las co-visitas de sus productos comprados. Solo el
    #     6,3 % de los compradores sin vistas son verdaderamente nuevos; para esos, esto no hace nada.
    if p["frio_historial"] > 0 and not vistos:
        comprados_u = sorted(estado.comprados.get(str(peticion.get("user_id", "")), set()))[:10]
        for pid in comprados_u:
            n_comprados = max(len(comprados_u), 1)
            for pos, (otro, peso) in enumerate(estado.covisitas.get(pid, [])):
                puntos[otro] += peso / ((pos + 1) * n_comprados)
        if puntos:  # cada producto comprado se empuja 1,2× el mejor candidato sembrado: la recompra manda
            tope = max(puntos.values())
            for pid in comprados_u:
                if pid in estado.disponibles:
                    puntos[pid] += p["frio_historial"] * tope

    # 2) Productos nuevos (sin co-visitas): usar los de descripción parecida.
    for k, pid in enumerate(reversed(vistos) if p["usar_vecinos"] else []):
        # "Nuevo" = no aparece en las co-visitas del historial. Si las co-visitas están vacías no sabemos qué es
        # nuevo, así que no se usan vecinos (si no, se aplicarían a TODO y taparían el resto: medido por Alex).
        if estado.covisitas and pid not in estado.covisitas:
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

    # 3c) Tendencias (R4): lo que se está moviendo ahora sube; lo que se ha parado baja un poco.
    #     `tendencias` lo rellena el servicio; con getattr funciona aunque el Estado aún no tenga el campo.
    lift = getattr(estado, "tendencias", None) or {}
    con_tendencia = p["tendencia"] > 0 and bool(lift)
    if con_tendencia:
        for pid in puntos:
            puntos[pid] *= lift.get(pid, 1.0) ** p["tendencia"]

    # 4) Re-visitas: lo que ya ha mirado y no ha comprado. Cada producto cuenta una vez, por su vista más reciente.
    if p["revisitas"] > 0 and vistos:
        tope = max(puntos.values(), default=1.0)
        contados: set[str] = set()
        for k, pid in enumerate(reversed(vistos)):
            if pid not in contados:
                contados.add(pid)
                puntos[pid] += p["revisitas"] * (p["decaimiento"] ** k) * tope

    elegidos = None
    if p["usar_modelo"]:
        elegidos = _con_modelo(peticion, estado, vistos, puntos, lift, p["tendencia"])
    if elegidos is None:
        elegidos = [pid for pid, _ in puntos.most_common() if vale(pid)][:10]

    # 5) Relleno, en este orden: más vendidos de la categoría de lo último que vio (el baseline de las
    #    diapositivas), populares de su tipo de comprador, populares de siempre y cualquiera disponible.
    #    (Se asume que recent_views va de más antiguo a más reciente, como en el cuaderno oficial.)
    ultima_categoria = str(estado.catalogo.get(vistos[-1], {}).get("category", "")) if vistos else ""
    de_su_categoria = estado.populares_categoria.get(ultima_categoria, [])
    ctx = peticion.get("context") or {}
    por_contexto = (estado.populares_contexto.get((str(ctx.get("device", "")), str(ctx.get("region", ""))), [])
                    if p["usar_contexto"] else [])
    populares = estado.populares
    if con_tendencia:
        de_su_categoria = _por_tendencia(de_su_categoria, lift, p["tendencia"])
        populares = _por_tendencia(populares, lift, p["tendencia"])
    for fuente in (de_su_categoria, por_contexto, populares, _todos(estado)):
        for pid in fuente:
            if len(elegidos) == 10:
                return elegidos
            if pid not in elegidos and vale(pid):
                elegidos.append(pid)
    # Si incluso así faltan (el usuario lo compró casi todo), relajar la regla de "ya comprado".
    for pid in chain(estado.populares, _todos(estado)):
        if len(elegidos) == 10:
            break
        if pid not in elegidos and pid in estado.disponibles:
            elegidos.append(pid)
    return elegidos
