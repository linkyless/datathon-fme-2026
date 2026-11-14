"""Catálogo, disponibilidad y más vendidos. Dueño: Operador.

Adaptado de starter/recommender.py y de la consulta 1 de starter/queries.sql.
ESTADO: escrito pero SIN PROBAR contra RawTree. Primera tarea del Operador: probarlo (ver docs/guias/operador.md).
"""
from __future__ import annotations

from src.rawtree import query

_CATALOGO = """
SELECT product_id,
       argMax(category, v)    AS category,
       argMax(subcategory, v) AS subcategory,
       argMax(brand, v)       AS brand,
       argMax(price_eur, v)   AS price_eur,
       argMax(is_active, v)   AS is_active
FROM (
    SELECT product_id::String AS product_id, category::String AS category, subcategory::String AS subcategory,
           brand::String AS brand, price_eur::Float64 AS price_eur, is_active::Bool AS is_active, '' AS v
    FROM shop.catalog
    UNION ALL
    SELECT product_id::String, category::String, subcategory::String, brand::String, price_eur::Float64,
           is_active::Bool, toString(ts)
    FROM shop.catalog_updates {filtro}
)
GROUP BY product_id"""

_AGOTADOS = """
SELECT product_id::String AS product_id
FROM shop.catalog_updates
WHERE change::String IN ('out_of_stock', 'back_in_stock') {filtro}
GROUP BY product_id
HAVING argMax(change::String, ts::DateTime64(3)) = 'out_of_stock'"""

_VENTAS = """
SELECT product_id::String AS product_id, count() AS n
FROM shop.history_events
WHERE event_type = 'purchase' {filtro}
GROUP BY product_id"""


def cargar_catalogo(hasta: str | None = None) -> dict[str, dict]:
    """product_id -> datos actuales del producto, con "agotado" (bool) añadido."""
    filtro_upd = f"WHERE ts < '{hasta}'" if hasta else ""
    cat = {f["product_id"]: dict(f) for f in query(_CATALOGO.format(filtro=filtro_upd))}
    filtro_stock = f"AND ts < '{hasta}'" if hasta else ""
    agotados = {f["product_id"] for f in query(_AGOTADOS.format(filtro=filtro_stock))}
    for pid, datos in cat.items():
        datos["agotado"] = pid in agotados
    return cat


def disponibles(cat: dict[str, dict]) -> set[str]:
    """Los que se pueden recomendar: activos y no agotados."""
    return {pid for pid, d in cat.items() if d.get("is_active") and not d.get("agotado")}


def cargar_populares(hasta: str | None = None) -> list[str]:
    """Más vendidos de siempre (el baseline oficial), de más a menos."""
    filtro = f"AND ts < '{hasta}'" if hasta else ""
    ventas = sorted(((int(f["n"]), f["product_id"]) for f in query(_VENTAS.format(filtro=filtro))), reverse=True)
    return [pid for _, pid in ventas]
