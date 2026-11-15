"""What we know about each shopper. Owner: Ranking.

For now just one thing: what each user already bought, so `recommend()` can filter it out.
Heads up (Evaluation's measurement, Sat 14/11): 61.6 % of the purchases in the test slice are products the user
HAD ALREADY bought, so the official hint ("whoever already bought it doesn't want it again") does NOT hold there,
and the filter sank purchase_hit from 0.86 to 0.32. That's why the filter is a parameter (a switch in the service /
A/B variants) and not a fixed rule: when it's off this field adds no score, and refreshing it only lets us turn it on
and measure it.
"""
from __future__ import annotations

import re

from src.rawtree import query

LIVE_HOURS = 24
"""Live, how many hours of shop.events we look back to add recent purchases."""

# One row per shopper with the (deduplicated) list of what they bought.
_SQL = """
SELECT user_id::String AS user_id, groupUniqArray(product_id::String) AS product_ids
FROM {table}
WHERE event_type::String = 'purchase' {where_extra}
GROUP BY user_id"""


def _purchases(table: str, where_extra: str) -> dict[str, set[str]]:
    rows = query(_SQL.format(table=table, where_extra=where_extra), timeout=300)
    return {f["user_id"]: set(f["product_ids"]) for f in rows}


def load_bought(until: str | None = None) -> dict[str, set[str]]:
    """user_id -> products they already bought. We don't recommend them again.

    `until` ('YYYY-MM-DD'): only use purchases with ts < until. None = history + live purchases
    (shop.events from the last LIVE_HOURS hours).
    """
    if until is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
            raise ValueError(f"until must be 'YYYY-MM-DD', not {until!r}")
        return _purchases("shop.history_events", f"AND ts::DateTime64(3) < '{until}'")
    bought = _purchases("shop.history_events", "")
    live = _purchases("shop.events", f"AND ts::DateTime64(3) > now() - INTERVAL {LIVE_HOURS} HOUR")
    for user, product_ids in live.items():
        bought.setdefault(user, set()).update(product_ids)
    return bought
