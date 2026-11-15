"""What is moving in the shop RIGHT NOW. Owner: Ranking.

    load_trends(until) -> {product_id: lift}

"Lift" = how much activity a product has in the last SHORT_WINDOW minutes compared with its normal pace over the
previous LONG_WINDOW minutes. 1.0 = as usual; 2.0 = twice the usual; 0.5 = half.
That way a trend that starts during the final shows up within minutes, even if the product wasn't a best seller.

Activity = views*1 + carts*6 + purchases*3 (same weights as co-visitation, docs/research.md point 5).
So that a product with 2 views doesn't jump to lift 3 by chance we add a PRIOR (the same "imaginary" activity on both
sides), which pulls the lift towards 1 when there's little data. At the end it's clipped to [LIFT_MIN, LIFT_MAX].
Idea from point 9 of docs/research.md (lift = short / (long + prior)).
"""
from __future__ import annotations

import re

from src.rawtree import query

SHORT_WINDOW = 30
"""Minutes that count as "now"."""
LONG_WINDOW = 180
"""Minutes BEFORE the short window that count as "normal"."""
PRIOR = 10.0
"""Imaginary activity (in short-window units) added on top and bottom: with little signal, lift ~ 1."""
LIFT_MIN, LIFT_MAX = 0.5, 3.0

_SQL = """
SELECT product_id::String AS product_id,
       sumIf(weight, t >= end_ts - INTERVAL {short} MINUTE) AS short_count,
       sumIf(weight, t <  end_ts - INTERVAL {short} MINUTE) AS long_count
FROM (
    SELECT product_id, ts::DateTime64(3) AS t, {end_ts} AS end_ts,
           multiIf(event_type::String = 'add_to_cart', 6, event_type::String = 'purchase', 3, 1) AS weight
    FROM {table}
    WHERE ts::DateTime64(3) >= {end_ts} - INTERVAL {total} MINUTE AND ts::DateTime64(3) < {end_ts}
)
GROUP BY product_id"""


def compute_lift(short_count: float, long_count: float) -> float:
    """Current pace / normal pace, with the prior, clipped. `long_count` is rescaled to the short window."""
    normal = long_count * SHORT_WINDOW / LONG_WINDOW
    lift = (short_count + PRIOR) / (normal + PRIOR)
    return min(max(lift, LIFT_MIN), LIFT_MAX)


def load_trends(until: str | None = None) -> dict[str, float]:
    """product_id -> lift. Only products that deviate from normal are kept (lift != 1).

    `until` ('YYYY-MM-DD'): the windows end on that date and are read from shop.history_events (offline eval).
    None = live: they end now and are read from shop.events. Refresh every 1-2 minutes (takes < 1 s).
    """
    if until is None:
        table, end_ts = "shop.events", "now64(3)"
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
            raise ValueError(f"until must be 'YYYY-MM-DD', not {until!r}")
        table, end_ts = "shop.history_events", f"toDateTime64('{until}', 3)"
    rows = query(_SQL.format(table=table, end_ts=end_ts, short=SHORT_WINDOW, total=SHORT_WINDOW + LONG_WINDOW),
                 timeout=60)
    out = {}
    for f in rows:
        lift = compute_lift(float(f["short_count"] or 0), float(f["long_count"] or 0))
        if lift != 1.0:
            out[f["product_id"]] = lift
    return out
