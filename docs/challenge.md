# The challenge: RawTree real-time recommendation

Official sources: https://datathon.rawtree.dev/ and the starter kit at https://github.com/rawtreedb/datathon-fme-2026

## The problem
An online shop with ~5,000 products in 10 categories and 90 subcategories. The home page and every product page have
a row of 10 recommendations. Today it shows the all-time best sellers. About 3 shoppers arrive every second, day and
night. For each one we have **3 seconds** to pick 10 ordered products. A hidden model simulates whether they click and
whether they buy. Our system has to run on its own, with nobody pressing buttons.

## The tables
| Table | What it holds | Size |
|---|---|---|
| `shop.catalog` | product_id, category, subcategory, brand, price_eur, description, created_at, is_active | ~5k |
| `shop.history_sessions` | 60 days of the old carousel: context, recent_views, `shown` (10), `propensity` (10), clicked, purchased, revenue_eur | ~2M |
| `shop.history_events` | 60 days of activity outside the carousel: ts, event_type (view/add_to_cart/purchase), user_id, session_id, product_id, price_eur, device, region | ~12M |
| `shop.requests` | Live: request_id, ts, user_id, is_new_user, context (device, region, page), recent_views [{product_id, ts}] | ~3/s |
| `shop.events` | Live, same format as history_events: real-time trends | ~50/s |
| `shop.catalog_updates` | Live: full product + ts + change (new_product, price_change, out_of_stock, back_in_stock) | |
| `team_NN.feedback` | Ours only: request_id, ts, shown, served_by (team/fallback), fallback_reason (missing/late/invalid), latency_ms, clicked, purchased, revenue_eur | |
| `team_NN.scratch` | Free table of ours (features, embeddings). Schemaless JSON inserts | |
| `team_NN.submissions` | Where we write our answers | |

Notes:
- The current catalog = `shop.catalog` with the latest update of each product on top (query 1 in `starter/queries.sql`).
- The old carousel was "best sellers + some randomness": the history is **biased towards popular products**.
  `propensity` = the probability the old system had of showing that product in that position. It's used to correct
  the bias (IPS).
- In `feedback`, `clicked`/`purchased` are `""` when nothing happened. The first row (`request_id = "__init__"`) is a
  placeholder: ignore it.

## What we send
```json
[{"request_id": "r_01JD8K3...", "items": ["p_91aa", "...10 in total..."], "model_version": "v3-coview"}]
```
- Exactly 10 distinct, active IDs. If one is out of stock, the WHOLE answer is invalid.
- Within 3 s of `ts` (the organisers' clock checks every 0.5 s). Late or invalid means the fallback is shown.
- One answer per request. Send in batches (every ~0.5 s), not one call per request.
- Order matters: shoppers look at the top slots much more.
- Reference: the official baseline polls every 0.3 s and answers in ~1 s (95 % in < 1.5 s).

## Schedule (Barcelona time; the data is in UTC)
- Sat 10:00 to Sun 10:00: practice with a public leaderboard every 10 min. Doesn't count.
- **Sun 10:00 to 12:00: final round, with NEW data** (new shoppers, products and trends). Hidden leaderboard: only a
  green/red light (green if > 90 % of answers were valid and on time in the last 5 min).
- Code can change during the final, but blind: we only see our own `feedback`.
- Sun 12:00: automatic final ranking. Sun afternoon: presentation + demo.

## Scoring
Final = 70 % from the stream + up to 30 from the jury.

| Part | Criterion | Points |
|---|---|---|
| Stream (70) | Conversion: expected purchases per 100 requests | 50 |
| | Cold start: the same, only on cold requests | 10 |
| | Reliability: % of valid, on-time answers | 10 |
| Jury (30) | Sound method validated offline before going live | 10 |
| | Learning from the stream: feedback, exploration, trends, prices, cold start | 10 |
| | Use of RawTree: features in SQL, embeddings, how the pipeline reads and writes | 5 |
| | Communication: a product manager should understand it | 5 |

- `score = 100 * (us - baseline) / (oracle - baseline)`, clipped to [0, 100].
- Baseline (0): **there's an official contradiction**. The README and the code in `starter/recommender.py` say
  "all-time best sellers in stock". The opening slides say "best sellers of the category of the last viewed product".
  The starter code does the former. The fallback is the README one.
- Oracle (100) = what the hidden model would pick.
- There's an organisers' "reference" on the leaderboard. It can be beaten.
- Cold request: `is_new_user = true`, or `recent_views` includes a product added during the event (`new_product`).
- What's scored is the purchase PROBABILITY, not the actual purchase. Real purchases matter a little: someone who
  already bought a product usually doesn't want it again.
- Tie-break: expected revenue per request in the final.

## What changes in the final ("The store moves" slide)
New shoppers (many with no history), new products (description only), trends (they show up early in `shop.events`),
price changes (in `catalog_updates`) and stock-outs (an inactive product voids the answer).

## Side challenge: "Best agentic use of RawTree"
Separate prize, open to every team: an AI agent that uses RawTree to store, analyse and act on data, where the agent
decides what to ingest, what to ask and what to do with the answers. You hand in a repo with a README and a short demo.
"A small project with a great story wins". Info at the RawTree booth. NOT a priority until the recommender is stable.

## Rules
- AI agents are allowed. Whatever we can't explain may be penalised.
- Keys and feedback are private. Recommendations must come from our code.
- The system runs unattended on a machine that doesn't go to sleep.
- The best teams share their repo with the jury: keep it readable.
- Official hint: "teams that generalise beat teams that memorise".
