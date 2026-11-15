"""Tukey panel: decision dashboard for the main challenge. Owner: Data.

    python -m src.panel_tukey            # writes data/panel.html (open it in the browser)
    python -m src.panel_tukey --loop 60  # refreshes it every 60 s (for the demo)

One panel per question of the challenge; each panel ends in the decision it feeds (Tukey's criterion 1). All panels
read from the same tables with the same definitions as the evaluator and the service (criterion 3: coherence).
No new dependencies: the HTML is self-contained (hand-written SVG, no CDN).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.rawtree import query

DATA = Path(__file__).resolve().parent.parent / "data"
PANEL = DATA / "panel.html"

# Each panel: question -> indicator -> decision. The chart type follows the shape of the data
# (lines for time series, bars for categories: Tukey's criterion 2, fitness).
PANELS_SQL: dict[str, str] = {
    # 1) do we convert better? PER VERSION (A/B arms shown separately; joined with submissions)
    "conversion_hour": """
        SELECT toStartOfHour(f.ts::DateTime64(3)) AS h,
               if(s.mv = '', '(no response)', s.mv) AS version, count() AS n,
               round(100*countIf(f.clicked::String != '')/count(), 2) AS pct_click,
               round(100*countIf(f.purchased::String != '')/count(), 2) AS pct_purchase
        FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts,
                     clicked::String AS clicked, purchased::String AS purchased
              FROM team_29.feedback
              WHERE ts > now() - INTERVAL 6 HOUR AND request_id::String != '__init__') f
        LEFT JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
                   FROM team_29.submissions GROUP BY rid) s ON f.rid = s.rid
        GROUP BY h, version ORDER BY h, version
    """,
    # 2) are we losing requests? why? (last 60 min)
    "fallback_cause": """
        SELECT fallback_reason::String reason, count() n
        FROM team_29.feedback
        WHERE ts > now() - INTERVAL 60 MINUTE AND request_id::String != '__init__'
        GROUP BY reason ORDER BY n DESC
    """,
    # 3) where do people look and where do they buy? (historical carousel, last 3 days)
    "positions": """
        SELECT z.2 AS pos, count() imps,
               countIf(z.1 = c) clicks, countIf(z.1 = p) purchases
        FROM (SELECT arrayJoin(arrayZip(shown, range(1, 11))) AS z,
                     clicked::String AS c, purchased::String AS p
              FROM shop.history_sessions WHERE ts > now() - INTERVAL 3 DAY)
        GROUP BY pos ORDER BY pos
    """,
    # 4) how big is the cold world? (requests in 30-min slices, last 3 h)
    "cold": """
        SELECT toStartOfInterval(ts::DateTime64(3), INTERVAL 30 MINUTE) t,
               count() n,
               round(100*countIf(is_new_user::Bool = true)/count(), 1) pct_new,
               round(100*countIf(length(recent_views) = 0 AND recent_views IS NOT NULL)/count(), 1) pct_no_views
        FROM shop.requests
        WHERE ts > now() - INTERVAL 3 HOUR
        GROUP BY t ORDER BY t
    """,
    # 4b) cold vs warm conversion (feedback joined with requests, last 3 h)
    "cold_conversion_rate": """
        SELECT if(r.is_new = 1 OR r.is_empty = 1, 'cold', 'warm') segment, count() n,
               round(100*countIf(f.purchased::String != '')/count(), 2) pct_purchase,
               round(100*countIf(f.clicked::String != '')/count(), 2) pct_click
        FROM team_29.feedback f
        INNER JOIN (
            SELECT request_id::String rid,
                   if(is_new_user::Bool = true, 1, 0) AS is_new,
                   if(length(recent_views) = 0 AND recent_views IS NOT NULL, 1, 0) AS is_empty
            FROM shop.requests WHERE ts > now() - INTERVAL 3 HOUR
        ) r ON f.request_id::String = r.rid
        WHERE f.ts > now() - INTERVAL 3 HOUR AND f.request_id::String != '__init__'
        GROUP BY segment ORDER BY n DESC
    """,
    # 5) what's moving? (views per subcategory: last 2 h vs the 2 h before)
    "trends": """
        SELECT ifNull(if(c.sub = '', 'new_product', c.sub), 'new_product') sub,
               sum(v2.n) n_recent, sum(v0.n) n_before,
               round(sum(v2.n) / (sum(v0.n) + 1), 2) ratio
        FROM (SELECT product_id::String pid, count() n FROM shop.events
              WHERE event_type = 'view' AND ts > now() - INTERVAL 2 HOUR GROUP BY pid) v2
        LEFT JOIN (SELECT product_id::String pid, count() n FROM shop.events
              WHERE event_type = 'view' AND ts > now() - INTERVAL 4 HOUR AND ts <= now() - INTERVAL 2 HOUR GROUP BY pid) v0
              ON v2.pid = v0.pid
        LEFT JOIN (SELECT product_id::String pid, subcategory::String sub FROM shop.catalog) c
              ON v2.pid = c.pid
        GROUP BY sub ORDER BY ratio DESC LIMIT 12
    """,
    # 6) is repurchasing normal? (carousel purchases of the last 7 days of a product the user had already bought
    #    in the events of the last 60 days)
    "repurchases": """
        SELECT count() AS purchases, countIf(h.u IS NOT NULL AND h.first_ts < s.t) AS repurchases
        FROM (SELECT user_id::String AS u, purchased::String AS p, ts::DateTime64(3) AS t
              FROM shop.history_sessions
              WHERE ts > now() - INTERVAL 7 DAY AND purchased::String != '') s
        LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p,
                          min(ts::DateTime64(3)) AS first_ts
                   FROM shop.history_events WHERE event_type::String = 'purchase' GROUP BY u, p) h
              ON s.u = h.u AND s.p = h.p
    """,
    # 7) how does the shop change? (accumulated catalog_updates)
    "store": """
        SELECT change::String change_kind, count() n
        FROM shop.catalog_updates
        GROUP BY change_kind ORDER BY n DESC
    """,
}

# Reliability per slice with quantiles over Dynamic columns is delicate:
# p50/p95 per 10-minute slice with explicit casts.
PANELS_SQL["reliability"] = """
    SELECT toStartOfInterval(ts::DateTime64(3), INTERVAL 10 MINUTE) t, count() n,
           round(100*countIf(served_by::String = 'team')/count(), 1) pct_ours,
           round(quantile(0.5)(latency_ms::Float64), 0) lat_p50,
           round(quantile(0.95)(latency_ms::Float64), 0) lat_p95
    FROM team_29.feedback
    WHERE ts > now() - INTERVAL 60 MINUTE AND request_id::String != '__init__'
    GROUP BY t ORDER BY t
"""

# Decision text per panel (shown in the panel itself: Tukey's criterion 1).
DECISIONS = {
    "conversion_hour": "If our version doesn't beat the conversion from before it went live, roll it back or tweak it before ramping it up.",
    "reliability": "If p95 gets close to 3,000 ms or the served % drops: fix the service (timeout, more threads, fallback), not the model.",
    "fallback_cause": "'missing' = arrived late or we never saw it; 'invalid' = malformed answer. Each reason has a different fix.",
    "positions": "The drop by position is a viewing bias, not a product one: it feeds the IPS correction and the ranking order.",
    "cold": "If the cold world grows, raise the priority of context best sellers and of embedded new products.",
    "cold_conversion_rate": "Decides whether cold shoppers get context best sellers (with their own conversion) or the general ranking.",
    "trends": "Subcategories with ratio > 1: candidates for a trend input to the ranker and for exploration (slots 8-10).",
    "repurchases": "62 % of HISTORY purchases are repurchases, but LIVE there are none (organisers, 17:40). So: (1) repurchase rows are dropped from the offline test; (2) the A/B says: overall CF 1.06 vs C 1.02, cold C 1.73 vs CF 1.41. Small differences, keep measuring before fixing the filter.",
    "store": "How much the shop moves: sets the refresh cadence of the catalog and the re-embedding of new products.",
}

_FOOT = """
<div class="foot"><b>Sources and definitions</b> (coherence criterion): conversion and latency from
<code>team_29.feedback</code> (excluding request_id='__init__'; <i>purchase</i> = purchased != ''); the version of each
answer from a join with <code>team_29.submissions</code> (A/B arms are NOT mixed); carousel positions from the
historical <code>shop.history_sessions</code> (<i>imps</i> = impressions per position; <i>clicked/purchased</i> hold a
product_id); cold from <code>shop.requests</code>; trends from <code>shop.events</code> views, 2 h vs the previous 2 h;
the shop from <code>shop.catalog_updates</code>; repurchases from <code>history_sessions</code> of the last 7 days
against earlier purchases in <code>shop.history_events</code> (60 days: historical data, NOT the live stream).
All ts in UTC. Generated by <code>python -m src.panel_tukey</code>.
</div>"""

_CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
header{padding:18px 24px;border-bottom:1px solid #1e293b}
header h1{margin:0 0 4px;font-size:20px} header .sub{color:#94a3b8;font-size:13px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:16px;padding:16px 24px}
.panel{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:12px 14px}
.panel h2{margin:0 0 2px;font-size:15px;color:#f1f5f9}
.panel .question{margin:0 0 8px;font-size:12px;color:#94a3b8}
.panel .decision{margin:8px 0 0;font-size:12.5px;color:#fbbf24}
svg{display:block;width:100%;height:auto}
svg text{fill:#94a3b8;font-size:10px}
.legend{font-size:11px;color:#94a3b8;margin-top:4px}
.foot{padding:10px 24px;color:#64748b;font-size:11.5px;line-height:1.5}
.foot code{color:#94a3b8}
.kpi{font-size:26px;font-weight:600;color:#f1f5f9} .kpi small{font-size:12px;color:#94a3b8;font-weight:400}
.kpis{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 2px}
"""

_JS = """
const COL = {ours:'#2563eb', others:'#64748b', click:'#22d3ee', purchase:'#34d399', red:'#f87171',
             amber:'#fbbf24', axis:'#475569', text:'#94a3b8', mark:'#f97316',
             p1:'#3b82f6', p2:'#34d399', p3:'#fbbf24', p4:'#c084fc'};
function esc(s){return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function svgOpen(w){return '<svg viewBox="0 0 '+w+' 210" preserveAspectRatio="xMidYMid meet">'}

function lineChart(rows, series, opts){
  const W = 520, H = 190, L = 44, R = 8, T = 12, B = 26;
  if (!rows || rows.length < 1) return svgOpen(W)+'<text x="280" y="105" text-anchor="middle">no data yet</text></svg>';
  const xs = rows.map(r => r[opts.x]);
  const all = series.flatMap(s => rows.map(r => +r[s.y]));
  let mx = Math.max(...all), mn = Math.min(0, ...all);
  if (mx === mn) mx = mn + 1;
  const X = i => xs.length < 2 ? (L+W)/2 : L + i*(W-L-R)/(xs.length-1);
  const Y = v => T + (H-T-B)*(1-(v-mn)/(mx-mn));
  let out = svgOpen(W);
  for (let k=0;k<=4;k++){ const v = mn+(mx-mn)*k/4, yy = Y(v);
    out += `<line x1='${L}' y1='${yy}' x2='${W-R}' y2='${yy}' stroke='${COL.axis}' stroke-width='0.6'/>`;
    out += `<text x='${L-4}' y='${yy+3}' text-anchor='end'>${(+v).toFixed(opts.dec ?? 1)}</text>`; }
  const done = new Set();
  xs.forEach((x, i) => { const lab = String(x).slice(5, 16); if (done.has(lab)) return; done.add(lab);
    out += `<text x='${X(i)}' y='${H-8}' text-anchor='middle'>${esc(lab)}</text>`; });
  if (opts.mark){ const i = xs.findIndex(x => String(x) >= opts.mark);
    if (i > 0) out += `<line x1='${X(i)}' y1='${T}' x2='${X(i)}' y2='${H-B}' stroke='${COL.mark}' stroke-dasharray='4 3'/>`
      + `<text x='${X(i)}' y='${T+9}' fill='${COL.mark}'>our v</text>`; }
  for (const s of series){
    const pts = [], circ = [];
    rows.forEach((r, i) => { const y = r[s.y];
      if (y === null || y === undefined) return;
      pts.push(X(i).toFixed(1) + ',' + Y(+y).toFixed(1));
      circ.push(`<circle cx='${X(i)}' cy='${Y(+y)}' r='2.2' fill='${COL[s.color || 'ours']}'/>`); });
    if (pts.length > 1)
      out += `<polyline fill='none' stroke='${COL[s.color || 'ours']}' stroke-width='1.8' points='${pts.join(' ')}'/>`;
    out += circ.join('');
  }
  return out + '</svg>';
}

function barChart(rows, opts){
  const W = 520, H = 190, L = opts.labelsLeft ?? 120, T = 10, B = 18, R = 10;
  const mx = Math.max(...rows.map(r => Math.max(r.a, r.b ?? 0)), 1);
  const bw = (H-T-B)/rows.length - 3;
  let out = svgOpen(W);
  rows.forEach((r, i) => {
    const y = T + i*((H-T-B)/rows.length) + 1.5;
    const w1 = (W-L-R)*(r.a/mx);
    out += `<rect x='${L}' y='${y}' width='${w1}' height='${bw}' rx='2' fill='${COL[opts.colorA || 'ours']}'/>`;
    const extra = opts.second && r[opts.second] !== undefined ? ` <tspan fill='${COL.amber}'>(${r[opts.second]})</tspan>` : '';
    out += `<text x='${L+w1+4}' y='${y+bw-1}' fill='${COL.text}'>${r.a.toLocaleString('en-GB')}${opts.suf ?? ''}${extra}</text>`;
    if (r.b !== undefined){ const w2 = (W-L-R)*((r.b ?? 0)/mx);
      out += `<rect x='${L}' y='${y+bw+1.5}' width='${w2}' height='${Math.max(bw-2,1)}' rx='2' fill='${COL.others}'/>`;
      out += `<text x='${L+w2+4}' y='${y+2*bw}' fill='${COL.text}'>${(r.b ?? 0).toLocaleString('en-GB')}${opts.suf ?? ''}</text>`; }
    out += `<text x='${L-6}' y='${y+bw}' text-anchor='end'>${esc(r.label)}</text>`;
  });
  return out + '</svg>';
}

function kpi(v, sub){ return `<div class='kpi'>${esc(v)} <small>${esc(sub)}</small></div>` }

window.PANELS.forEach(p => {
  const el = document.getElementById('p_' + p.id);
  let html = `<h2>${esc(p.title)}</h2><p class='question'>${esc(p.question)}</p>`;
  const rows = p.rows || [];
  if (p.kind === 'line') html += lineChart(rows, p.series, p.opts || {});
  else if (p.kind === 'bars') html += barChart(rows, p.opts || {});
  else if (p.kind === 'kpi'){
    html += `<div class='kpis'>${p.kpis.map(k => kpi(k[0], k[1])).join('')}</div>`;
    if (rows.length && p.opts2) html += lineChart(rows, p.series, p.opts2);
  }
  html += `<div class='legend'>${p.legend || ''}</div><p class='decision'>Decision: ${esc(p.decision)}</p>`;
  el.innerHTML = html;
});
"""


def _rows(name: str, sql: str) -> list[dict]:
    try:
        return query(sql)
    except Exception as e:  # one panel must not take down the whole dashboard
        print(f"panel {name}: ERROR {e}", file=sys.stderr)
        return []


def _regenerate() -> None:
    payload = {k: _rows(k, sql) for k, sql in PANELS_SQL.items()}
    conv = payload["conversion_hour"]
    cold = {f["segment"]: f for f in payload["cold_conversion_rate"]} if payload["cold_conversion_rate"] else {}
    reliab = payload["reliability"]
    fb = payload["fallback_cause"]

    # conversion PER VERSION (A/B arms are different decisions and aren't mixed)
    by_version: dict[str, list[dict]] = {}
    for r in conv:
        by_version.setdefault(r["version"], []).append(r)
    hours = sorted({str(r["h"]) for r in conv})
    top = sorted(by_version, key=lambda v: -sum(r["n"] for r in by_version[v]))[:4]
    rows_conv = []
    for h in hours:
        row = {"h": h}
        for i, v in enumerate(top):
            item = next((r for r in by_version[v] if str(r["h"]) == h), None)
            row[f"pc{i}"] = item["pct_purchase"] if item else None
        rows_conv.append(row)
    series_conv = [{"y": f"pc{i}", "color": f"p{(i % 4) + 1}", "name": v} for i, v in enumerate(top)]

    rec = payload["repurchases"][0] if payload["repurchases"] else None
    pct_rec = round(100 * rec["repurchases"] / rec["purchases"], 1) if rec else None
    kpis_rec = [(f"{pct_rec} %", "% of purchases that are REPURCHASES"),
                (rec["purchases"], "carousel purchases in 7 days")] if rec else []

    def _pct(rs: list[dict], fld: str) -> float | None:
        n = sum(r["n"] for r in rs)
        return round(sum(r[fld] * r["n"] for r in rs) / n, 2) if n else None

    kpis_conv = [(f"{v}: {_pct(by_version[v], 'pct_purchase')} %", "% purchases 6 h") for v in top]
    # the hour's reliability is weighted by requests (the max over slices isn't the hour's %).
    # pct_ours comes in % (85-100): turned into a proportion before weighting.
    n_hour = sum(r["n"] for r in reliab) or None
    served_hour = round(100 * sum(r["n"] * (r["pct_ours"] / 100) for r in reliab) / n_hour, 1) if n_hour else None
    p95_hour = round(sum((r["lat_p95"] or 0) * r["n"] for r in reliab) / n_hour, 0) if n_hour else None
    kpis_reliab = [("p95 last hour", p95_hour, "ms"),
                   ("served last hour", served_hour, "%")]

    panels = [
        {"id": "conv", "kind": "line", "title": "Purchases per hour and per VERSION (A/B)",
         "question": "Which version converts better? (feedback joined with submissions, last 6 h)",
         "series": series_conv, "opts": {"x": "h", "dec": 2},
         "legend": "one line per model_version: " + ", ".join(top),
         "decision": DECISIONS["conversion_hour"], "rows": rows_conv},
        {"id": "kpi", "kind": "kpi",
         "title": "Decision summary per version",
         "question": "At a glance: which version converts better, and at what reliability cost?",
         "kpis": kpis_conv + kpis_reliab,
         "decision": "A change only stays if it improves this within 30-60 min live; otherwise it's rolled back."},
        {"id": "reliab", "kind": "line", "title": "Response reliability",
         "question": "Are we losing requests? (last 60 min, 10-min slices)",
         "series": [{"y": "lat_p95", "color": "red"}, {"y": "lat_p50", "color": "others"}],
         "opts": {"x": "t", "dec": 0},
         "legend": f"served by us in the hour (weighted by requests): {served_hour} %, p50 and p95 latency (ms), 3,000 ms = late",
         "decision": DECISIONS["reliability"], "rows": reliab},
        {"id": "fb", "kind": "bars", "title": "Fallback reasons (60 min)",
         "question": "If a request was lost, why?",
         "rows": [{"label": r["reason"], "a": r["n"]} for r in fb],
         "opts": {"labelsLeft": 90, "colorA": "red"},
         "legend": "fallbacks in the last hour",
         "decision": DECISIONS["fallback_cause"]},
        {"id": "pos", "kind": "bars", "title": "Clicks and purchases per position (history, 3 days)",
         "question": "Where do people look and where do they buy? (old carousel, 10 slots)",
         "rows": [{"label": "pos " + str(r["pos"]), "a": r["clicks"], "b": r["purchases"]}
                  for r in payload["positions"]],
         "opts": {"labelsLeft": 60, "colorA": "click"},
         "legend": "blue = clicks in that position, grey = purchases, impressions per position: "
                   + str(next((r["imps"] for r in payload["positions"]), "-")),
         "decision": DECISIONS["positions"]},
        {"id": "cold", "kind": "line", "title": "Cold world, request by request (3 h)",
         "question": "How many shoppers arrive with no context? (requests)",
         "series": [{"y": "pct_new", "color": "amber"}, {"y": "pct_no_views", "color": "others"}],
         "opts": {"x": "t", "dec": 1},
         "legend": "% of requests with is_new_user=true, % with empty recent_views",
         "decision": DECISIONS["cold"], "rows": payload["cold"]},
        {"id": "repurchases", "kind": "kpi", "title": "Repurchases: the shopper buys the same product again",
         "question": "How many carousel purchases are of something the user had ALREADY bought? (7 days)",
         "kpis": kpis_rec,
         "legend": "carousel purchases (history_sessions) of a product the same user had bought before (60 days)",
         "decision": DECISIONS["repurchases"]},
        {"id": "cold_conv", "kind": "kpi", "title": "Cold vs warm conversion (3 h)",
         "question": "Do cold shoppers need something different? (feedback joined with requests on request_id)",
         "kpis": [("cold: " + str(cold.get("cold", {}).get("pct_purchase", "-")) + " %", "% purchases cold"),
                  ("warm: " + str(cold.get("warm", {}).get("pct_purchase", "-")) + " %", "% purchases warm")],
         "legend": "cold = new user or no views; conversion over "
                   + str(sum(r["n"] for r in payload["cold_conversion_rate"])) + " requests",
         "decision": DECISIONS["cold_conversion_rate"]},
        {"id": "trend", "kind": "bars", "title": "Trends: views in the last 2 h vs the previous 2 h",
         "question": "What's moving right now? (shop.events)",
         "rows": [{"label": r["sub"], "a": r["n_recent"], "b": r["n_before"],
                   "ratio": r["ratio"]} for r in payload["trends"]],
         "opts": {"labelsLeft": 130, "colorA": "purchase", "second": "ratio"},
         "legend": "green = views last 2 h, grey = previous 2 h, amber number = recent/before ratio",
         "decision": DECISIONS["trends"]},
        {"id": "cat", "kind": "bars", "title": "The shop moves (catalog_updates)",
         "question": "How many new products, stock-outs and price changes are there?",
         "rows": [{"label": r["change_kind"], "a": r["n"]} for r in payload["store"]],
         "opts": {"labelsLeft": 110, "colorA": "amber"},
         "legend": "accumulated since the start of the event; sets the refresh and re-embedding cadence",
         "decision": DECISIONS["store"]},
    ]

    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Tukey panel - mental_descent()</title><style>" + _CSS + "</style></head><body>"
        "<header><h1>Tukey panel - mental_descent() - real-time recommender</h1>"
        "<div class='sub'>One panel = one question of the challenge -> one decision. Generated "
        + time.strftime("%H:%M:%S") + " (local time, data in UTC), <code>python -m src.panel_tukey</code></div>"
        "</header><main>"
        + "".join("<div class='panel' id='p_%s'></div>" % p["id"] for p in panels)
        + "</main>" + _FOOT
        + "<script>window.PANELS = " + json.dumps(panels, default=str, ensure_ascii=False) + "</script>"
        + "<script>" + _JS + "</script></body></html>"
    )
    DATA.mkdir(exist_ok=True)
    PANEL.write_text(html, encoding="utf-8")
    print(f"panel ready: {PANEL} ({PANEL.stat().st_size//1024} kB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Builds the decision dashboard (Tukey).")
    parser.add_argument("--loop", type=int, default=0, help="regenerate every N seconds (demo)")
    args = parser.parse_args()
    while True:
        _regenerate()
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
