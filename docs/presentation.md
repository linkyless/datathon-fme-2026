# Presentation notes: mental_descent(), RawTree real-time recommendation

**Status: Sun ~04:30. DRAFT for the 08:00 checkpoint.**
Team rule: only numbers from `docs/EXPERIMENTS.md`, from `team_29.feedback` or from queries that ran go in, nothing
made up. Gaps are marked **[TODO]**.

The presentation has two threads, interleaved:

1. **The main challenge**: a real-time recommender competing against the baseline.
2. **Tukey (side challenge)**: our **decision dashboard**, the tool that tells us *which decision to take* (move up a
   step, roll back, explore). Live demo: it's both our engine room and the side challenge.

---

## 1. The problem in one sentence

An online shop (~5,000 products) has to choose, for each shopper that arrives (~3/s, day and night), the **10
products** to show in its recommendation row in **under 3 s**; a hidden model decides whether the shopper buys, and 70 %
of the score is beating the all-time best sellers baseline.

## 2. The real story: from the baseline to a model that learns from the stream

We didn't start from a theory: we started by **measuring every step** and only moving up when it won. Our day:

| Time | Live version | What it taught us |
|---|---|---|
| 12:27 | starter baseline | all-time best sellers in stock (0.61 % in its window) |
| 14:08 | **e-samen-v0** | best sellers of the last viewed category; 0 duplicates during the handover |
| 16:51-19:07 | v1-A/B/C/D/E/T/M | co-visits, revisits, trends, regression: **each idea, one measured arm**; co-visits as a rule: out (clicks -41 %, real); R5 learned the history's biases: out |
| 18:51-19:07 | A6 -> **A7** | LightGBM tree on what was viewed (R6) + without the piece that hurt cold shoppers |
| 20:47-23:47 | AP2 -> **AP3** | **the learner**: logistic regression trained ONLY on our live feedback (not on the carousel); AP2 beat C by +1.20 pts [CI +0.56, +1.87] |
| 21:43-23:16 | EXP1/EXP1x | exploring slots 9-10: we measured its cost (-0.25/-0.35 where it explores) and switched it off |
| 00:05-... | **quarters AP3/AP4/AP5/AP6** | the same model with 4 different orderings, to be measured tonight |

The ladder it fits into (every piece is reusable; nothing gets thrown away):

```
N0  Live baseline, never down                                   -> reliability (10 pts)
N1  Session co-visits (as a SIGNAL, not a rule)                 -> conversion (50 pts)
N2  Trends (30-min lift), recency, live stock                   -> learning from the stream
N3  Cold start: new shopper (context/profiles), new product (embeddings) -> 10 cold pts
N4  The learner: weights retrained ONLY on our feedback (AP2 -> AP3)
N5  R6 LightGBM "choice within the carousel" (lambdarank) + a booster for cold shoppers
```

## 3. The Tukey dashboard: from each question to a decision

Tukey's criterion 1 is "it helps decide". Each panel starts from ONE question of the challenge and ends in ONE
decision we can take today:

| Challenge question | Indicator (panel) | Decision it feeds |
|---|---|---|
| Do we convert better? Which version wins? | conversion per version and slice (feedback joined with our answers by request_id) | move up a step or **roll back** (rule: 30-60 min live; if it doesn't improve, out) |
| Are we losing requests? Why? | % served, fallbacks by reason, p50/p95 latency | robustness: timeout, handover, precomputed fallback filler |
| Where do people look and where do they buy? | clicks and purchases per position 1-10 (old carousel) | position curve -> IPS correction and ordering (top = purchase probability, not revenue) |
| What drives a purchase? | conversion by the shopper's cart/viewed/category affinity | which signals go into the learner and with what weight (note: R5 learned this wrong without this panel) |
| How big is the cold world? | % is_new_user, % without recent_views; cold vs warm conversion | priority of the cold specialist and how much exploration is allowed |
| What's moving? | views 2 h vs previous 2 h per subcategory | whether the trend goes into the ranking and with what eta |
| How does the shop change? | new products, price changes, stock-outs | refresh cadence of the catalog and re-embedding of new products |

> Rules note (Sat ~16:45): a stock-out takes up to ~2 s to show up in `catalog_updates`, so a product that went out of
> stock less than 5 s ago no longer voids the answer (that slot doesn't sell, but the rest of the list counts). It
> changes how we read "invalid" in the panel and eases the pressure on the live stock refresh, but the goal stays the
> same: read stock at the right cadence.

The whole panel reads from the SAME tables the evaluator and the service use (`team_29.feedback`, `shop.events`,
`shop.catalog_updates`) -> **Tukey's criterion 3 (coherence)**: what you see is exactly the number we decide with; no
metric has two definitions.

**Prototype done**: `python -m src.panel_tukey` -> `data/panel.html` (self-contained, no server; `--loop 60` refreshes
it every minute for the demo). **[TODO: screenshot for the slide + fresh numbers 30 min before presenting]**

## 4. How we validate before taking risks, and the two episodes that prove it

**The rule that saved us twice: trust measured feedback > the documentation > historical data.**

**Episode 1: repurchases.** The official hint said "whoever already bought a product doesn't want it again"; the
history said the opposite (61.6 % of the test purchases were repurchases); the organisers' message at 17:40 basically
said the hidden model may agree **with neither** ("trust the feedback loop more than the docs, and the docs more than a
CSV"). The feedback of the WHOLE day settled it: out of 5,240 times we showed something bought today, 1 purchase; out
of 35,746 of something bought in the history, 4. **Already bought products are never shown** (x0.12 in the
regression). The bought filter is right.

**Episode 2: future leakage.** Our first "shopper's favourite category" seemed to multiply purchases by 8-11. It was
**leakage**: it was measured with today's activity AFTER the answer, including the purchase itself. Without leakage
(earlier history only): x2.2. The leaky version hurt (-21 % on cold shoppers); the fixed one, already in A7, stopped
hurting. The learner also rebuilds each shopper's state by **replaying the day in order, event by event, with a 3 s
delay**: it never sees the future.

Validation tools:
- The history is **biased** (the old carousel only showed popular stuff): propensity correction (IPS/SNIPS) and
  position correction (slot 2 is worth 0.68x of the 1st once calibrated, not 0.45x).
- **Temporal split** (train on the first 70 % of the day, validate on the last 30 %) and **bootstrap confidence
  intervals** (200-400 resamples): "up" only when the CI doesn't include zero.
- **Every idea, one live A/B arm** compared over the SAME minutes (16 for co-visits, 12 for revisits, 42 for the
  filter, 15 for exploration...).
- And a new rule of the day: **measure which signal organic shoppers prefer** (recall@10 on organic purchases: last
  viewed subcategory 20.5 %; category affinity 9.2 %; co-purchase 14.6 %; embedding neighbours 5.2-9.9 %, discarded as
  a candidate source).

**[TODO: offline evaluator results per version, from Evaluation]**

## 5. What we've already measured from the stream (done, with numbers)

**The attention curve by position** (historical carousel, 96,825 impressions per position over 3 days): clicks drop
from 1.08 % in position 1 to 0.16 % in position 10, and purchases from 0.48 % to 0.04 %. Order matters ~7x.

| pos | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| % click | 1.08 | 0.58 | 0.42 | 0.32 | 0.28 | 0.25 | 0.23 | 0.21 | 0.18 | 0.16 |
| % purchase | 0.48 | 0.23 | 0.16 | 0.12 | 0.10 | 0.09 | 0.07 | 0.06 | 0.06 | 0.04 |

**What the simulator rewards** (our feedback of the day, 776,800 products shown, 652 purchases, slot 1): in TODAY's
cart, not bought **2.5 %**; in a history cart, not bought **1.18 %**; no previous relationship **0.22 %**. And the
shopper's affinity with the category scales purchases from 0.01 % to **0.83 %**. That table is exactly what feeds the
learner's 11 signals.

Other measured numbers:

| What | Number | Source |
|---|---|---|
| New shoppers (last hour) | 29.79 % | `shop.requests` |
| "Cold" != "no views": 83 % of cold requests do have views | 1.25 | `shop.requests` (Evaluation) |
| Catalog / available | 5,100 / 5,064 | `src/catalog.py` |
| New / out of stock / back in stock / price changes | 100 / 144 / 108 / 1,241 | `shop.catalog_updates` |
| Our service latency (p50/p95) | 773 / 1,091 ms (baseline: 1,238 / 2,232) | `team_29.feedback` |
| The learner: AUC 0.885 (position only: 0.718); the bought product ranked 1st in 40.8 % of answers with a purchase | +14.1 % error improvement vs position only | `src/learner.py` |
| The learner beats C live | 2.03 % vs 0.83 % [+0.56, +1.87] | live A/B, feedback |
| Embedding neighbours in the same subcategory | 100 % of the list by design | `src/coldstart.py` |

**[TODO: final results table per version on the morning of the final]**

## 6. Use of RawTree (why this piece decides the challenge)

- **Queries that respect the database**: `Dynamic` columns with explicit casts, ALWAYS a `ts` filter, one statement per
  request. We documented what breaks (`docs/data.md`): an empty table has no columns, `until` inferred as Date,
  comparing without casting.
- **No heavy queries in the loop** (the starter's unfiltered query got to 10 s): precompute in SQL and serve from
  memory. `recommend()` in <1 ms; the learner, 1.4 ms p50.
- **`team_29.scratch` = the team's memory**: 5,100 embeddings (4096 dims, normalised, float16+base64) and
  shopper/product profiles shared between scripts and the service, with no files to sync.
- **`catalog_updates` as the source of new products**: new products aren't in `shop.catalog`; we embed them from there
  (`embed_new()`) so that a product from 5 minutes ago already has neighbours.
- **`team_29.feedback` as the observation room**: it's the table that retrains the learner every few hours and also
  feeds the dashboard. RawTree isn't just storage: it's the channel through which the stream talks to us, and through
  which we learned faster than from any CSV.

## 7. What we'd do with more time (honest, prioritised)

**What we deliberately did NOT do** (the honest slide; the jury appreciates it and it's 30 % of the score):
- **No session neural networks** (GRU4Rec/SASRec): in the Ludewig/Jannach comparison (2020) neighbour methods compete,
  and they cost 8+ h we didn't have; a LightGBM with the learner's signals gave the same error as the regression
  (0.00658 vs 0.00657): no need to complicate it.
- **No per-product bandits or Thompson**: the final has ~21,600 requests and ~430 purchases, not enough signal for
  5,000 arms; and we MEASURED exploration in slots 9-10 live: it costs 0.15-0.35 points where it explores. That's why we
  only learn shared parameters and retrain in batch.
- **No "more data = better" without a CI**: we tried 22 history signals on top of AP3 and only 4-6 passed the interval
  (previous exposure +0.91 %, purchase affinity +0.32 %, 14-day views +0.23 %, cart x2 +0.21 %; together +1.66 %). The
  rest stayed out even if they sound good.

With more time, by value:
1. **Cold specialist**: cold isn't "no views": 83 % of cold requests have views and there are new signals for them (the
   live rate one, AP8, is already specified for Evaluation).
2. **Two-headed blend** (R6 tree + learner, trained on different data: carousel vs feedback): their errors offset each
   other; offline validation first, arm later.
3. **Reloading models without restarting the service** (weights are only read at start-up today): retrain every hour
   live without touching traffic.
4. **Alerts on the panel**: automatic alerts (conversion drop, fallback rise) to react during the final without
   watching by hand.

---

### Appendix: demo plan (Sunday afternoon)

- 1 min: problem + the story of one day (slide 2: each step, its measurement).
- 2 min: live dashboard on real `feedback` (slide 3): conversion per version in the last hour and the decision we took
  with it (including the real case of rolling back exploration).
- 1 min: a product created today -> description neighbours in `scratch` (cold start demo).
- 1 min: how we validate (slide 4, with the two episodes: repurchases and leakage).
- 1 min: what we measured from the stream (slide 5: position curve + what the simulator rewards).
- Close: use of RawTree (slide 6) + the honest slide (slide 7).

**Review: 08:00 checkpoint (frozen).** Owner: Data.
Sources for the [TODO]s: `docs/EXPERIMENTS.md` (offline and live) and `team_29.feedback`.
