"""Live panel: our own leaderboard for the final (where the public one is hidden). Owner: Evaluation.

    python -m src.panel                  # last 3 hours in 15-minute slices
    python -m src.panel --hours 1 --minutes 5

Joins team_29.feedback (what happened with each request) with team_29.submissions (which version answered) on
request_id. Read-only: it can run alongside the service without bothering it. Times in Barcelona time.
"""
from __future__ import annotations

import argparse

from src.rawtree import TEAM, query

# "(no response)" = requests we sent nothing for: the shopper saw the fallback (reason "missing").
# "late" = we did send, but feedback says "missing": measured on Sat 16:25, "late" never shows up; whatever arrives
# after 3 s is logged as "missing" even though it's in submissions.
_PANEL = """
SELECT {group_rows}
       if(s.mv = '', '(no response)', s.mv)                  AS version,
       count()                                               AS requests,
       100 * countIf(f.served_by = 'team') / count()         AS pct_ours,
       countIf(f.fallback_reason = 'late'
               OR (f.fallback_reason = 'missing' AND s.mv != '')) AS late,
       countIf(f.fallback_reason = 'invalid')                AS invalid,
       100 * countIf(f.clicked != '') / count()              AS clicks_100,
       100 * countIf(f.purchased != '') / count()            AS purchases_100,
       sum(f.revenue_eur) / count()                          AS revenue_per_request,
       quantile(0.95)(f.latency_ms)                          AS p95_ms
FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts, served_by::String AS served_by,
             fallback_reason::String AS fallback_reason, clicked::String AS clicked,
             purchased::String AS purchased, revenue_eur::Float64 AS revenue_eur,
             latency_ms::Nullable(Float64) AS latency_ms
      FROM {team}.feedback
      WHERE ts > now() - INTERVAL {hours} HOUR AND request_id != '__init__') AS f
LEFT JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
           FROM {team}.submissions GROUP BY rid) AS s ON f.rid = s.rid
GROUP BY {grps}
ORDER BY {grps}"""

COLUMNS = [  # (column, title, format)
    ("requests", "requests", "{:>10}"), ("pct_ours", "% ours", "{:>10.1f}"),
    ("late", "late", "{:>7}"), ("invalid", "invalid", "{:>10}"),
    ("clicks_100", "clicks/100", "{:>11.2f}"), ("purchases_100", "purch/100", "{:>12.2f}"),
    ("revenue_per_request", "EUR/req", "{:>11.3f}"), ("p95_ms", "p95 ms", "{:>8.0f}"),
]


def panel(hours: int, minutes: int | None) -> list[dict]:
    """Rows per version; with `minutes`, also per slice of that size (Barcelona time)."""
    if minutes:
        group_rows = (f"toStartOfInterval(toTimeZone(f.ts, 'Europe/Madrid'), INTERVAL {int(minutes)} MINUTE) "
                      "AS slice_start,")
        grps = "slice_start, version"
    else:
        group_rows, grps = "", "version"
    return query(_PANEL.format(group_rows=group_rows, grps=grps, team=TEAM, hours=int(hours)))


def show(rows: list[dict], with_slice: bool) -> None:
    header = (f"{'slice':<7}" if with_slice else "") + f"{'version':<22}" + "".join(
        f"{title:>{len(fmt.format(0))}}" for _, title, fmt in COLUMNS)
    print(header)
    previous = None
    for f in rows:
        slice_ = str(f.get("slice_start", ""))[11:16]  # 'YYYY-MM-DD HH:MM:SS' -> 'HH:MM'
        start = (f"{slice_ if slice_ != previous else '':<7}" if with_slice else "")
        previous = slice_
        cells = "".join(fmt.format(f[col]) if f[col] is not None else f"{'-':>{len(fmt.format(0))}}"
                        for col, _, fmt in COLUMNS)
        print(start + f"{f['version']:<22}" + cells)


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--hours", type=int, default=3, help="how many hours back (default 3)")
    args.add_argument("--minutes", type=int, default=15, help="slice size in minutes (default 15)")
    a = args.parse_args()

    print(f"Last {a.hours} h, {a.minutes}-minute slices (Barcelona time)\n")
    show(panel(a.hours, a.minutes), with_slice=True)
    print(f"\nTotal for the last {a.hours} h per version\n")
    show(panel(a.hours, None), with_slice=False)


if __name__ == "__main__":
    main()
