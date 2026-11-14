"""Co-visitas: "quien vio X vio/compró también Y". Dueño: Ranking.

Idea (como en OTTO, docs/investigacion.md punto 5): dos productos están relacionados si aparecen en la misma
sesión. Para el par (X -> Y) sumamos, por cada sesión donde salen los dos, un peso según lo que el comprador
hizo con Y: solo verlo 1, añadirlo al carrito 6, comprarlo 3. Se puede dividir por la popularidad de Y elevada
a EXPONENTE_POPULARIDAD para que lo que sale en todas partes no gane siempre (ver abajo por qué está a 0).

Detalles medidos el sáb 14/11:
- Cada sesión cuenta UNA vez por par (si vio Y tres veces no suma tres): se usa el mayor peso de Y en la sesión.
- "Solo pares a menos de 24 h": la sesión más larga de shop.history_events dura 954 s, así que se cumple
  siempre y no hace falta filtrarlo en la consulta.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.rawtree import query

VECINOS = 20
"""Cuántos productos relacionados guardamos por producto."""

PESO_VISTA, PESO_CARRITO, PESO_COMPRA = 1, 6, 3
EXPONENTE_POPULARIDAD = 0.0
"""0 = sin dividir por popularidad. El deep research proponía 0.5, pero sobre las 3.530 compras del carrusel
posteriores al 2026-11-07 (chequeo de Leonardo, sáb 14/11) las compras dentro de nuestros 10 fueron: exponente 0
-> 14,76 %, 0.25 -> 12,83 %, 0.5 -> 11,47 % (con IPS: 2,18 / 2,05 / 1,89). Al modelo oculto le gusta lo popular."""

_DATA = Path(__file__).resolve().parent.parent / "data"

# `sp` = una fila por (sesión, producto) con el mayor peso que tuvo ese producto en esa sesión.
# Después se cruza `sp` consigo misma por sesión para sacar los pares, y con `pop` (en cuántas sesiones sale
# cada producto) para normalizar.
_SQL = """
WITH sp AS (
    SELECT session_id::String AS s, product_id::String AS p,
           max(multiIf(event_type::String = 'add_to_cart', {carrito},
                       event_type::String = 'purchase', {compra}, {vista})) AS w
    FROM shop.history_events
    {filtro}
    GROUP BY s, p
)
SELECT pares.p AS p, pares.q AS q, pares.bruto / pow(pop.sesiones, {exponente}) AS peso
FROM (
    SELECT a.p AS p, b.p AS q, sum(b.w) AS bruto
    FROM sp AS a INNER JOIN sp AS b ON a.s = b.s
    WHERE a.p != b.p
    GROUP BY p, q
) AS pares
INNER JOIN (SELECT p, count() AS sesiones FROM sp GROUP BY p) AS pop ON pares.q = pop.p
ORDER BY peso DESC
LIMIT {vecinos} BY p"""


def _archivo(hasta: str | None) -> Path:
    return _DATA / f"covisitas_{hasta or 'todo'}_exp{EXPONENTE_POPULARIDAD:g}.json"


def _consultar(hasta: str | None) -> dict[str, list[tuple[str, float]]]:
    filtro = f"WHERE ts::DateTime64(3) < '{hasta}'" if hasta else ""
    sql = _SQL.format(filtro=filtro, vecinos=VECINOS, exponente=EXPONENTE_POPULARIDAD,
                      vista=PESO_VISTA, carrito=PESO_CARRITO, compra=PESO_COMPRA)
    salida: dict[str, list[tuple[str, float]]] = {}
    for fila in query(sql, timeout=600):  # llegan ordenadas de mayor a menor peso
        salida.setdefault(fila["p"], []).append((fila["q"], float(fila["peso"])))
    return salida


def cargar_covisitas(hasta: str | None = None) -> dict[str, list[tuple[str, float]]]:
    """product_id -> [(otro_product_id, peso), ...] ordenado de mayor a menor peso (máx. VECINOS por producto).

    `hasta` ('YYYY-MM-DD'): usar solo eventos con ts < hasta. None = todo (en vivo).
    La consulta es pesada: el resultado se guarda en data/covisitas_<hasta>_exp<exponente>.json y se reutiliza.
    Para recalcular, borra ese archivo.
    """
    if hasta is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", hasta):
        raise ValueError(f"hasta debe ser 'YYYY-MM-DD', no {hasta!r}")
    archivo = _archivo(hasta)
    if archivo.exists():
        guardado = json.loads(archivo.read_text(encoding="utf-8"))
        return {p: [(q, float(w)) for q, w in lista] for p, lista in guardado.items()}
    covisitas = _consultar(hasta)
    if covisitas:  # no guardar un resultado vacío: sería un fallo, no un dato
        _DATA.mkdir(exist_ok=True)
        archivo.write_text(json.dumps(covisitas), encoding="utf-8")
    return covisitas
