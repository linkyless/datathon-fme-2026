# mental_descent(): RawTree real-time recommender

Datathon FME 2026, RawTree challenge. A recommender that answers every visit to a simulated online shop with an
ordered list of 10 products in under 3 seconds, at around 3 visits per second, unattended. A hidden shopper model
decides whether each list leads to a click and a purchase.

The final system (**AP7**) is an on-policy learner: a logistic regression over 12 shopper-product features, trained
exclusively on the feedback of our own live responses and served from an in-memory state that is updated event by
event from the shop's stream.

## Results

Expected purchases per 100 requests, as reported by the organisers' leaderboard.

| Window | Us (AP7) | Organisers' reference | Baseline (all-time best sellers) |
|---|---|---|---|
| **Final round** (Sun 10:00-12:00 CET), all requests | **1.66** | 2.14 | 0.75 |
| Final round, cold requests | **1.99** | 2.20 | 1.18 |
| Practice, last hour (Sun 06:26-07:26 CET), all requests | **1.73** | 1.83 | 0.53 |
| Practice, last hour, cold requests | **2.07** | 1.59 | 0.75 |

On-time valid responses: 99.65 % in the final round, 99.99 % in the last practice hour. Cold requests are those from
new shoppers or whose recent views include a product added during the event.

## The task

For every new row of `shop.requests` the service must insert into `team_29.submissions` a list of exactly 10
distinct, active, in-stock `product_id`s within 3 s of the request timestamp. Late, invalid or missing answers are
replaced by the organisers' fallback (all-time best sellers) and count against reliability. Scoring is
`100 * (us - baseline) / (oracle - baseline)` on conversion (50 pts), cold-start conversion (10) and reliability
(10), plus 30 jury points.

Data available through RawTree (a ClickHouse-based analytical store for JSON):

| Table | Content | Use |
|---|---|---|
| `shop.catalog`, `shop.catalog_updates` | ~5,000 products, live price and stock changes | availability and catalog |
| `shop.history_sessions`, `shop.history_events` | 60 days of an old carousel (~2M impressions) and off-carousel activity (~12M events) | shopper memory, product priors |
| `shop.requests` | live requests (~3/s) with recent views and context | what we answer |
| `shop.events` | live views, add-to-cart and purchases (~50/s) | in-memory state |
| `team_29.feedback` | what happened to each of our responses | training data and monitoring |

## Architecture

```
                 +--------------------------- src/service.py ---------------------------+
shop.requests -->| poll (SQL, last 15 s) -> World + learner -> validate -> send queue    |--> team_29.submissions
shop.events ---->| World.catch_up() every 1.5 s   (in-memory shopper and product state)  |    (3 sender threads,
catalog_updates >| refresh: stock 1 s, catalog 15 s, best sellers 10 min                 |     batched inserts)
                 +-----------------------------------------------------------------------+
team_29.feedback --> src/learner.py (replay + logistic regression) --> data/learner_<name>.json --> service
```

The service is a single Python process with independent threads:

1. **Polling.** Queries `shop.requests` for the last 15 s, excluding request ids already present in
   `team_29.submissions`, so a restart never answers a request twice. The age of each request is computed on the
   server (`age_ms`), not with the local clock, which was measured to drift by several seconds. Each new request is
   scored, validated and pushed to a queue; polling never blocks on writes.
2. **Sending.** Three workers drain the queue and insert batches into `team_29.submissions`.
3. **Refresh.** Stock every second, catalog every 15 s, best-seller lists every 10 min. Every structure is replaced
   whole, so the polling thread always reads a consistent snapshot. A failed refresh keeps the previous version.
4. **World.** A separate thread applies new `shop.events` to the in-memory state every 1.5 s.

Every list goes through `src/validate.py` (10 distinct ids, active and in stock at that moment). If the model raises or
returns an invalid list, the request is answered with the available best sellers instead. Shoppers are assigned to
A/B arms with `zlib.crc32(user_id)`, which is stable across restarts; the arm is encoded in `model_version`.

Measured during the final round: request age when first seen p50 390 ms, p95 657 ms; insert p95 414 ms.

## The model

### State (`World`)

What is known about each shopper and each product at a given instant: today's carts, carts and purchases in the
60-day history, views in the current session, category and subcategory affinity, and per-product purchase rates.
It is built from the history plus today's events at start-up (~25 s) and then fed event by event. The same class is
used for training and for serving, so features are computed identically in both.

### Candidate generation

For each request: the shopper's carts (today and historical, not bought), recently viewed products, the best sellers
of the category (40) and subcategory (20) of the last viewed product, the best sellers of the shopper's top 3
categories and top 5 subcategories by affinity, and the 40 global best sellers. Anything the shopper has already
bought is removed. Best-seller lists are recomputed every 120 s.

### Scoring

A logistic regression on 12 features, `P(purchase) = sigmoid(b + w . x)`. The 10 candidates with the highest
probability are returned in that order. Weights of the model that ran in the final round (`aphc`, trained on
219,695 responses and 2,711 purchases):

| Feature | Definition | Weight | exp(w) |
|---|---|---|---|
| `aff_cat` | sqrt of the share of the shopper's views in the product's category | 3.592 | |
| `viewed_session` | in the request's recent views, not bought | 1.803 | 6.1 |
| `cart_today` | added to cart today, not bought | 1.799 | 6.0 |
| `rate` | log purchases per view, smoothed (50 pseudo-views) | 1.586 | |
| `cart_old` | added to cart in the history, never bought | 1.529 | 4.6 |
| `aff_sub` | same as `aff_cat`, for the subcategory | 1.031 | |
| `new_user` | new shopper (level only) | 0.787 | |
| `same_cat` | same category as the last viewed product | 0.763 | 2.1 |
| `same_sub` | same subcategory as the last viewed product, for products unrelated to the shopper | 0.296 | 1.3 |
| `pop` | log share of shop purchases going to the product | 0.274 | |
| `no_views` | request has no recent views (level only) | -0.671 | |
| `bought` | already bought (always filtered out at serving time) | -1.777 | 0.17 |

For binary features exp(w) reads as an odds multiplier. `new_user` and `no_views` shift every candidate of a request
equally and do not change the ranking.

### Training

Training replays the day in timestamp order. Before each of our past responses, only the events that happened before
that request (minus a 3 s lag, matching the live delay of the state) are applied to the `World`; then the features of
the 10 products shown are recorded together with the outcome. Leakage from the future is impossible by construction.

- Label: purchase. Using clicks as a partial label did not improve validation.
- The list position is a feature during training, to absorb position bias, and is dropped at serving time.
- History counts are down-weighted (0.1 per event) so that per-product rates follow today's shop.
- L2 regularisation 1.0. Validation on the most recent 30 % of responses, out of time.

Validation of `aphc`: log loss 0.01067 against 0.01191 for a position-only model (-10.4 %), on 65,909 held-out
responses with 1,110 purchases. The gain saturates early: 13.9 % with 15,235 training responses and 14.1 % with 60,940.
Retraining is incremental in data: each run downloads only the new feedback and events and refits on everything.

## Design decisions

**From the historical log to on-policy feedback.** The first models were trained on the 60-day carousel log: a
logistic regression (R5) and a LightGBM ranker (R6, served as A6/A7), both with inverse propensity weighting. The log
is a weak training signal for this task: the old carousel showed mostly popular products, so most shopper-product
relationships were never exposed, and 62 % of logged purchases were repurchases, a pattern absent from the live
simulator. On Saturday at 20:47 the training source was switched to our own feedback. With the same features and a
deliberately simple model, the first learner (AP2) beat the best rule-based arm with 2.03 vs 0.83 real purchases per
100 requests (95 % CI of the difference [+0.56, +1.87]).

**Logistic regression over trees.** A LightGBM model on the same 12 features reached the same validation log loss
(0.00658 vs 0.00657), and gradient-boosted trees stacked on top of the choice model added little that was not already
captured by existing features. The regression was kept: equal accuracy, millisecond scoring and weights that can be
read and audited one by one.

**Embeddings.** Product descriptions were embedded with `qwen3-embedding` to find similar products, mainly for new
products. Measured as recall@10 on organic purchases, embedding neighbours reached 5.2 % (9.9 % when ranked by sales)
against 20.5 % for the best sellers of the last viewed subcategory, and 80 % of the neighbours were in the same
subcategory anyway. The signal is covered more cheaply by `aff_sub` and `same_sub`, so embeddings were not used in AP7.

**Evaluated and not kept.** Co-visitation as a candidate rule, revisit boosting, 30-minute trends, price and promotion
features, exploration of slots 9-10 with new and long-tail products, alternative orderings of the same scores
(AP4-AP6), per-segment models for new shoppers (APO/APH) and a conditional-logit choice model (AP8). See the table
below.

## Online experimentation

Every change went live as an A/B arm before replacing the control. Shoppers are split by `crc32(user_id)`, so a
shopper always sees the same arm, and `src/panel.py` joins `team_29.feedback` with `team_29.submissions` by
`request_id` to report purchases, clicks, latency and fallback rate per arm and per time slice.

| When (CET) | Test | Result (purchases / 100) | Decision |
|---|---|---|---|
| Sat 16:51 | rule C vs C + co-visitation | clicks -41 % | rule C |
| Sat 17:06 | rule C vs C + revisits | 0.88 vs 0.88 | rule C |
| Sat 17:17 | rule C vs R5 (regression on the historical log) / C + trends | 1.00 vs 0.37 (short window) / 1.00 vs 0.93 | rule C |
| Sat 20:50 | AP2 learner vs rule C | 2.03 vs 0.83 | learner |
| Sat 22:00 | AP3 vs AP3 exploring slots 9-10 | about -0.3 in exploring requests | no exploration |
| Sat 23:47 | four orderings of the same scores (AP3-AP6) | 1.86 vs 1.56 / 1.51 / 1.33 | model order |
| Sun 04:54 | AP7 vs hybrid by shopper type (APOH2) | 1.75 vs 1.58 | AP7 |
| Sun 06:15 | AP7 vs choice model AP8 | 1.77 vs 1.69 | AP7 (tie) |

With ~2,500 requests per arm and an hour of traffic, differences below 0.2-0.3 purchases per 100 cannot be separated
from noise; close results were treated as ties and the simpler arm was kept.

Offline evaluation (`src/evaluate.py`) reproduces the organisers' notebook on the last week of the history with
IPS-weighted metrics and paired bootstrap intervals, and calibrates offline metrics against the public leaderboard by
rank correlation.

## Repository

```
src/service.py      live service (AP7 by default; --arms replays any A/B of the event)
src/learner.py      World, candidate generation, training and recommend() (AP2-AP8)
src/catalog.py      catalog, availability and best sellers
src/validate.py     response validator (+ self-tests)
src/rawtree.py      minimal RawTree client (query / insert)
src/panel.py        live per-arm monitoring from team_29.feedback
src/evaluate.py     offline evaluation (IPS, bootstrap, leaderboard calibration)
src/local_test.py   offline smoke test
src/ranking.py, state.py, covisits.py, users.py, trends.py, coldstart.py, embeddings.py, scoring.py,
choice.py, tree.py, panel_tukey.py   earlier versions (rules, co-visitation, R5, LightGBM, choice model v2)
starter/            organisers' starter kit, unmodified
docs/               challenge notes, RawTree notes, experiment and decision logs
```

## Running it

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                # TEAM_ID and RAWTREE_API_KEY
git config core.hooksPath .githooks                 # blocks commits containing keys or data
python -m src.validate && python -m src.local_test  # offline checks
python -m src.learner --name aphc --without o1,o2,h,x   # train AP7 (~2 min, data goes to data/, never committed)
python -m src.service --no-send                     # full pipeline without sending
python -m src.service                               # live (one instance at a time)
```

## Model versions

Every version is tagged and still runs (`python -m src.service --arms <set>`, see `ARM_SETS`).

| Version | Description | Code | Tag |
|---|---|---|---|
| v0 / C | best sellers of the last viewed category | `service.py`, `ranking.py` | `version/v0` |
| v1 | + session co-visitation | `ranking.py`, `covisits.py` | `version/v1` |
| CF | v0 without already-bought products | `ranking.py`, `users.py` | `version/cf` |
| R5 | logistic regression on the historical carousel (IPS) | `scoring.py` | `version/r5` |
| v2 | conditional logit on the historical carousel | `choice.py` | `version/v2` |
| A6 / A7 | LightGBM ranker on the historical carousel | `tree.py` | `version/a6`, `version/a7` |
| AP2 / AP3 | on-policy learner | `learner.py` | `version/ap2`, `version/ap3` |
| AP4-AP6 | alternative orderings of AP3 scores | | `version/ap4-ap6` |
| **AP7** | learner + `same_sub`: **final** | `learner.py` (`aphc`) | `version/ap7` |
| APO / APH | organic history features, new-shopper weights, hybrids | `learner.py` | `version/ap8` |
| AP8 | choice model ("which of the 10 was chosen") | `learner.py --choice` | `version/ap8` |

Tags point at the code exactly as it ran live, so module names and comments there may differ from `main`.
