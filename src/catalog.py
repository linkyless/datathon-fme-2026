"""Catalog, availability and best sellers.

Adapted from starter/recommender.py and query 1 of starter/queries.sql.
"""
from __future__ import annotations

from src.rawtree import query

_CATALOG = """
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
    FROM shop.catalog_updates {where}
)
GROUP BY product_id"""

_OUT_OF_STOCK = """
SELECT product_id::String AS product_id
FROM shop.catalog_updates
WHERE change::String IN ('out_of_stock', 'back_in_stock') {where}
GROUP BY product_id
HAVING argMax(change::String, ts::DateTime64(3)) = 'out_of_stock'"""

_SALES = """
SELECT product_id::String AS product_id, count() AS n
FROM shop.history_events
WHERE event_type = 'purchase' {where}
GROUP BY product_id"""


def load_catalog(until: str | None = None) -> dict[str, dict]:
    """product_id -> current product data, with "out_of_stock" (bool) added."""
    where_upd = f"WHERE ts < '{until}'" if until else ""
    cat = {f["product_id"]: dict(f) for f in query(_CATALOG.format(where=where_upd))}
    where_stock = f"AND ts < '{until}'" if until else ""
    out = {f["product_id"] for f in query(_OUT_OF_STOCK.format(where=where_stock))}
    for pid, data in cat.items():
        data["out_of_stock"] = pid in out
    return cat


def load_out_of_stock(until: str | None = None) -> set[str]:
    """Only the products out of stock right now. Light query: the service repeats it every second."""
    where = f"AND ts < '{until}'" if until else ""
    return {f["product_id"] for f in query(_OUT_OF_STOCK.format(where=where), timeout=5)}


def available(cat: dict[str, dict], out_of_stock: set[str] | None = None) -> set[str]:
    """The ones that can be recommended: active and in stock (`out_of_stock` = most recent list, if given)."""
    if out_of_stock is None:
        return {pid for pid, d in cat.items() if d.get("is_active") and not d.get("out_of_stock")}
    return {pid for pid, d in cat.items() if d.get("is_active") and pid not in out_of_stock}


def load_popular(until: str | None = None) -> list[str]:
    """All-time best sellers (the official baseline), from most to least sold."""
    where = f"AND ts < '{until}'" if until else ""
    sales = sorted(((int(f["n"]), f["product_id"]) for f in query(_SALES.format(where=where))), reverse=True)
    return [pid for _, pid in sales]


def by_category(cat: dict[str, dict], popular: list[str]) -> dict[str, list[str]]:
    """Splits the best-seller list by category, keeping the order."""
    out: dict[str, list[str]] = {}
    for pid in popular:
        category = cat.get(pid, {}).get("category")
        if category:
            out.setdefault(str(category), []).append(pid)
    return out
