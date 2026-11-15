"""Co-visitation: "whoever viewed X also viewed/bought Y". Owner: Ranking.

Idea (as in OTTO, docs/research.md point 5): two products are related if they show up in the same session. For the
pair (X -> Y) we add, for every session where both appear, a weight based on what the shopper did with Y: just viewed
it 1, added it to the cart 6, bought it 3. It can be divided by Y's popularity raised to POPULARITY_EXPONENT so that
what shows up everywhere doesn't always win (see below why it's at 0).

Details measured on Sat 14/11:
- Each session counts ONCE per pair (viewing Y three times doesn't add three): Y's highest weight in the session is used.
- "Only pairs less than 24 h apart": the longest session in shop.history_events lasts 954 s, so it always holds and
  there's no need to filter it in the query.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.rawtree import query

NEIGHBOURS = 20
"""How many related products we keep per product."""

VIEW_WEIGHT, CART_WEIGHT, PURCHASE_WEIGHT = 1, 6, 3
POPULARITY_EXPONENT = 0.0
"""0 = no division by popularity. The deep research suggested 0.5, but on the 3,530 carousel purchases after
2026-11-07 (checked on Sat 14/11) the share of purchases inside our 10 was: exponent 0 -> 14.76 %, 0.25 -> 12.83 %,
0.5 -> 11.47 % (with IPS: 2.18 / 2.05 / 1.89). The hidden model likes popular stuff."""

_DATA = Path(__file__).resolve().parent.parent / "data"

# `sp` = one row per (session, product) with the highest weight that product had in that session.
# Then `sp` is joined with itself by session to get the pairs, and with `pop` (in how many sessions each product
# shows up) to normalise.
_SQL = """
WITH sp AS (
    SELECT session_id::String AS s, product_id::String AS p,
           max(multiIf(event_type::String = 'add_to_cart', {cart},
                       event_type::String = 'purchase', {purchase}, {view})) AS w
    FROM shop.history_events
    {where_extra}
    GROUP BY s, p
)
SELECT pairs.p AS p, pairs.q AS q, pairs.raw / pow(pop.sessions, {exponent}) AS weight
FROM (
    SELECT a.p AS p, b.p AS q, sum(b.w) AS raw
    FROM sp AS a INNER JOIN sp AS b ON a.s = b.s
    WHERE a.p != b.p
    GROUP BY p, q
) AS pairs
INNER JOIN (SELECT p, count() AS sessions FROM sp GROUP BY p) AS pop ON pairs.q = pop.p
ORDER BY weight DESC
LIMIT {neighbours} BY p"""


def _cache_file(until: str | None) -> Path:
    """Cache file name, with a SIGNATURE of every parameter that changes the result.

    If the weights, NEIGHBOURS or the exponent change, the name changes and the old cache is ignored automatically
    (it used to depend only on `until` and the exponent: changing the weights left the service reading stale
    co-visits without noticing).
    """
    signature = (f"exp{POPULARITY_EXPONENT:g}_v{NEIGHBOURS}"
                 f"_weights{VIEW_WEIGHT}-{CART_WEIGHT}-{PURCHASE_WEIGHT}")
    return _DATA / f"covisits_{until or 'all'}_{signature}.json"


def _run_query(until: str | None) -> dict[str, list[tuple[str, float]]]:
    where_extra = f"WHERE ts::DateTime64(3) < '{until}'" if until else ""
    sql = _SQL.format(where_extra=where_extra, neighbours=NEIGHBOURS, exponent=POPULARITY_EXPONENT,
                      view=VIEW_WEIGHT, cart=CART_WEIGHT, purchase=PURCHASE_WEIGHT)
    out: dict[str, list[tuple[str, float]]] = {}
    for row in query(sql, timeout=600):  # they come sorted by weight, highest first
        out.setdefault(row["p"], []).append((row["q"], float(row["weight"])))
    return out


def load_covisits(until: str | None = None) -> dict[str, list[tuple[str, float]]]:
    """product_id -> [(other_product_id, weight), ...] highest weight first (at most NEIGHBOURS per product).

    `until` ('YYYY-MM-DD'): only use events with ts < until. None = everything (live).
    The query is heavy: the result is saved to data/covisits_...json and reused.
    The file name includes the parameters (weights, neighbours, exponent), so changing them invalidates the cache on
    its own. To recompute with the same parameters, delete that file.
    """
    if until is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
        raise ValueError(f"until must be 'YYYY-MM-DD', not {until!r}")
    cache_file = _cache_file(until)
    if cache_file.exists():
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        return {p: [(q, float(w)) for q, w in items_list] for p, items_list in saved.items()}
    covisits = _run_query(until)
    if covisits:  # don't save an empty result: that'd be a failure, not data
        _DATA.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(covisits), encoding="utf-8")
    return covisits
