# How to win a real-time recommendation datathon: a prioritised plan with evidence

**Short answer: don't go straight for the "big model".** First build, in about 6 hours, a session-aware and robust
policy. It has three parts: candidates from co-visitation and from the same subcategory, an explicit score
P(purchase | product, context) that takes price into account, and a list sorted from highest to lowest with already
bought products excluded. Validate it with a position-based off-policy estimator, calibrated against the real
leaderboard points. Only then add a LightGBM ranker with attribute features, never IDs, and online learning of a few
parameters. The leaderboard numbers suggest the hidden model strongly rewards fine-grained affinity with the session
(subcategory, brand, price range) and penalises price much less than it seems. The latter is an inference, not a
confirmed fact.

## TL;DR

- **Priority 1 (~6 h, biggest gain):** session candidates and co-visits weighted by event type and recency (OTTO's
  winning pattern), scored with subcategory, brand and price affinity. Exclude what was bought and sort by estimated
  P(purchase), because attention drops with position. In short sessions simple neighbour methods compete with neural
  networks (Ludewig, Mauro, Latifi and Jannach, 2019/2020).
- **Priority 2 (~4 h, essential for the jury and to avoid flying blind):** an offline evaluator. Combine a position
  model (PBM) estimated from the log with item-position IPS/SNIPS, bootstrap intervals and a calibration check: does it
  rank "baseline 0.66 %" and "category best sellers 0.79 %" like the leaderboard? Whole-list IPS has huge variance;
  Li et al. (KDD 2018) show click-model estimators are more efficient if the model holds.
- **Priority 3 (~6-8 h, later):** a LightGBM ranker trained on the event log with features that generalise, plus
  online learning of a few parameters (EWMA trends, price weight, stock) with discounted Thompson sampling and a little
  exploration in the low slots. The final will have only about 21,600 requests (~450 purchases at 2 % conversion), too
  few to learn product by product: learn shared parameters, not 5,000 arms.

## Key findings

### 1. What the leaderboard numbers tell us (own analysis)

| Policy | Conversion | EUR/request | EUR per purchase (= EUR/request / conversion) |
|---|---|---|---|
| Baseline (best sellers) | 0.66 % | 0.147 | ~22 |
| Best sellers of the last viewed category | 0.79 % | - | - |
| v2-HYDRA-explora | 0.85 % | 0.32 | ~38 |
| Organisers' reference | 2.09 % | 0.93 | ~45 |

Interpretation. This is **inference**, not direct evidence from the simulator:

- **Price isn't a strong brake for the hidden model.** The reference triples conversion while showing products with an
  average ticket of about 45 EUR, twice the baseline's. If the simulator punished price hard, you couldn't triple
  conversion with products twice as expensive. Most likely there's a moderate price sensitivity, maybe varying by
  device or region, or price interacts with affinity: "expensive but what I'm looking at" converts.
- **Category alone explains little** (+20 %), while the reference gets +217 %. The affinity the hidden model rewards
  is finer: probably subcategory (90 of them), brand and a price range consistent with `recent_views`. The reference
  might also use the viewed products themselves (revisits), the strongest signal in OTTO.
- **The bandit with exploration (HYDRA) raises revenue more than conversion.** It fits a simulator where learning
  "what sells" online helps a bit, but without a session model you don't get far.
- **Straight gradient boosting failed** for two typical reasons: learning the log's popularity bias (which the old
  carousel created) and latency (61 % of answers on time). It's the exact illustration of "generalise, don't memorise".

**Quick test to confirm the hypotheses** (1 hour of practice): serve A/B variants by `user_id` hash: (a) same
subcategory as the last view, sorted by popularity; (b) the same, sorted by descending price within the top 30; (c)
revisits: products viewed in the session and not bought go first. With about 10,800 requests/hour, each arm gets about
3,600. At 1-2 % conversion (36-72 purchases per arm), the standard error of the difference is about 0.3 points: you'll
detect effects of 1.5x or more, not nuances. For nuances, use **clicks** as an intermediate signal, they're much more
frequent.

### 2. How simulators model clicks and purchases (question A1)

- **Position-based model (PBM).** P(click | user u, item i, rank r) = A(u,i) x E(r): the item's attractiveness times
  the probability of examining the position. SARDINE (Deffayet et al., 2023) uses it literally as its click model. In
  their experiments a PBM learned from the log "correctly identifies the biases" and almost reaches the oracle, while a
  naive per-document CTR model barely improves on the logging policy.\[1\] **Implication:** under PBM the optimal list
  is simply sorting by A(u,i) from highest to lowest, because the product of two sequences is maximised by pairing
  largest with largest (rearrangement inequality). Diversity doesn't help *by itself*.
- **Cascade** (Kveton et al., ICML 2015, "Cascading Bandits"). The user scans the list top to bottom and stops at the
  first attractive item.\[2\] **Implication:** the value of the list is 1 - prod(1 - A_i). Putting ten near-clones of
  the same product wastes slots if the user has a different intent, so diversity helps when the intent is uncertain.
- **Multinomial logit (MNL) and conditional logit.** That's what RecSim ships (Ie et al., 2019): the user picks at most
  one item from the list with probability proportional to exp(utility), against a "pick nothing" option. RecSim also
  includes an "exponential cascade model" to capture position bias.\[3\]\[4\] **Implication:** P(purchase from the list)
  = sum v_i / (v_0 + sum v_j), which grows with every v_i, so maximising conversion is again picking the highest
  utilities. When optimising revenue under MNL it sometimes pays to *drop* cheap products (assortment problem), which
  fits the revenue tie-break.
- **RecoGym** (Rohde et al., 2018) separates "organic" behaviour (what the user views on their own) from the response to
  recommendations ("bandit"), with different embeddings. When they diverge, purely organic methods fail (BLOB,
  2020).\[5\]\[6\] **Implication for us:** co-visits from the event log (organic) need not match what the simulator buys
  from the carousel. They have to be validated against the carousel log and the live feedback.

**Practical conclusion:** in all three models the priority is putting the highest P(purchase) items first. The
differences show up in the tail of the list (slots 6-10): that's where diversifying (cascade/MNL) and exploring make
sense, because it's cheap (low E(r)).

### 3. Inferring the hidden model from the log (A2)

- **Position curve E(r).** Since the carousel had "some randomness", the same product showed up in different
  positions. Compare its click/purchase rate across positions weighting by 1/propensity ("intervention harvesting",
  Agarwal et al., WSDM 2019, "Estimating Position Bias without Intrusive Interventions"). Alternative: regression EM
  (Wang et al., WSDM 2018), which estimates E(r) and relevance together without randomisation and in their personal
  search evaluation beat pairwise-randomisation estimates in almost every position.\[7\]\[8\] For 10 positions and 2M
  impressions, a simple numpy EM (PBM: click = examination x attractiveness) converges in minutes.
- **Price sensitivity per device/region.** Fit a logistic regression of purchase per impression with features
  [position (one-hot), log(price), log(price) x device, log(price) x region, same subcategory as the last viewed, same
  brand, |log price - mean log price of the session|], weighted by 1/propensity. The coefficients are interpretable
  and easy to explain to the jury.
- **Category affinity from views.** Compute the purchase rate conditioned on "the product shown shares
  subcategory/brand/price range with k of the recent views".
- **Live:** refit the regression every 30 minutes on your own feedback. Your data has no propensities if the policy is
  deterministic; that's why your policy should have a small, *logged* randomisation (see F).

### 4. Generalising vs memorising (A3)

**Solid evidence:** DropoutNet (Volkovs, Yu and Poutanen, NeurIPS 2017)\[9\] shows that a model trained by randomly
"switching off" the preference inputs (the ID-based ones) learns to rely on content. Without losing accuracy on known
users and products ("warm start"), it improves on "cold start" (users or products with no history).\[10\] The principle
for us: **everything that goes into the ranker must be computable for a new product**. Subcategory, brand, price
bucket, embedding similarity with the views and age are fine. The product ID as a categorical feature isn't.

**Reasoned intuition:** the final will have new products and users and different trends. Attribute-level transitions
(subcategory->subcategory, brand->brand, price bucket->price bucket) are estimated with far more data per cell than
product->product ones (90x90 vs 5,000x5,000) and apply as-is to new products. Use both: ID co-visits when there's
support and a fallback to attribute transitions.

### 5. Session-based candidates (B4-B5)

- **Evidence: simple methods compete.** Ludewig, Mauro, Latifi and Jannach (UMUAI, 2020/arXiv:1910.12781) compared
  twelve algorithms, six of them neural, on eight datasets, and conclude that "in most cases... simple heuristic
  methods based on nearest-neighbors schemes are preferable". On the e-commerce datasets RETAIL, DIGI and ZALANDO the
  neighbour methods win (SKNN, V-SKNN, STAN); only on RSC15 does a neural model (NARM) slightly beat VSTAN (MAP@20 of
  0.0357 vs 0.0350), and SR-GNN takes about 10,000 times longer to train than VSTAN. STAN (Garg et al., SIGIR 2019) adds
  three weights to SKNN: the item's position in the current session, the recency of neighbour sessions and the
  position in the neighbour session. The `session-rec` library (GitHub rn5l/session-rec) has Python implementations of
  sknn, vsknn, stan and vstan.
- **Evidence: OTTO (Kaggle, 2022-23).** Chris Deotte's public notebook ("Candidate ReRank Model", LB 0.575) uses three
  co-visitation matrices: (1) "Carts-Orders", weighted by type with click:cart:order weights of 1:6:3 and a 24 h
  window; (2) "Buy2Buy" (cart/order -> cart/order, 14-day window, weight 1); (3) "Clicks", time-weighted with a linear
  weight from 1 to 4 by date and a 24 h window.\[11\]\[12\] It truncates each session to its 30 most recent events.\[11\]
  To rerank, it weights each session event with `np.logspace(0.1, 1, n, base=2) - 1` (more weight to recent ones)
  times the type weight.\[11\] The winners turned this into two stages: the 1st (mrkmakr) generated 1,200 candidates
  per session (revisits, type- and time-weighted co-visits and neural retrieval) and reranked them with LightGBM
  Ranker; according to lukan217's blog summary (February 2023), citing Kaggle discussion #384022, it scored 0.604 with a
  single model and 0.605 with an ensemble of 9 LightGBMs with different hyperparameters. The 3rd, the team of Chris
  Deotte, Giba and Theo, scored 0.60437 public and 0.60382 private (Benedikt Schifferer's repo,
  bschifferer/Kaggle-Otto-Comp). A silver solution used 12 h windows (click->click) and 24 h (click->cart/purchase,
  cart->cart) and added **every item already viewed in the session** as a candidate.\[13\]\[14\] Important: OTTO's
  metric weights 0.1 clicks, 0.3 carts and 0.6 orders, so the purchase-oriented matrices were the decisive ones, as
  here.\[15\]\[16\]
- **Recommendation for combining signals (B5):** score of candidate c given session s =
  sum over views v of s of w_rec(v) * [alpha*CV_view->view(v,c) + beta*CV_view->purchase(v,c) + gamma*CV_cart->purchase(v,c)],
  with w_rec(v) = 2^(-(n-pos(v))/h) (exponential decay; start with h ~ 3 views) or Deotte's logspace. Normalise each
  matrix per row (dividing by c's popularity raised to ~0.5) so you don't always recommend the popular stuff. Start
  with alpha:beta:gamma = 1:6:3 by analogy with OTTO and tune it with the offline evaluator. **That view->purchase
  should weigh more than view->view is OTTO evidence; the exact values here are intuition to be validated.**

### 6. Reranking with a model (C6-C8)

- **Architecture (evidence from OTTO and RecSys 2015):** the RecSys Challenge 2015 winner (YOOCHOOSE; Romov and
  Sokolov, Yandex) used two-stage classification, massive use of categorical features, gradient boosting and
  thresholds optimised directly for the metric.\[17\] In OTTO almost every gold used candidates + a GBDT ranker
  (LightGBM `lambdarank`, CatBoost `YetiRank`/`QueryCrossEntropy`).\[13\]\[18\]\[19\] A bronze solution describes the
  ranker relying mostly on an exponentially decayed recency score of the session items.\[20\]
- **Features that matter most, per the OTTO writeups:** (1) is the candidate in the session? and its recency/type; (2)
  aggregated co-visit scores and their rank; (3) candidate popularity over several time windows; (4) embedding
  similarity with the last view and with the session mean.\[13\]\[19\]\[21\] Here you'd add (5) price relative to the
  session's, (6) subcategory/brand/price bucket match, (7) device/region/page and (8) new product (yes/no).
- **Minimum viable version (~5-6 h):** train on the **event log** (12M), not the carousel. Cut each session at instant
  t: the prefix gives the features and the label is "does c get bought in the rest of the session?". Take 50 candidates
  per session, binary LightGBM (easier to calibrate than lambdarank) with 20-30 features and 1-2M rows. Inference: 50
  candidates x 30 features is trivial (<10 ms).
- **Biased log (C7):** Joachims, Swaminathan and Schnabel (WSDM 2017) formalise propensity-weighted ERM: each observed
  click is weighted by 1/P(examination at its position). That gives an unbiased estimate of the risk even though the
  data is biased, and their Propensity SVM-Rank beat both the production ranker and the naive SVM-Rank in a real
  experiment.\[22\]\[23\] **For us:** if you train on the carousel log, weight positive rows by 1/E(r), with E(r)
  estimated as in point 3, and clip weights (<= 10). If you train on the organic event log, the bias is different
  (what the shop used to show): mitigate it by normalising popularity.
- **Objective (C8):** the score is sum_k E(k)*P(purchase_k) and the tie-break is revenue. Sort by
  **p_hat(purchase | item, context)** and apply the tie-break only when probabilities are close: score = p_hat *
  (1 + lambda*log(price)) with a small lambda (0.05-0.1), validated offline. Predict P(purchase) directly if there's
  enough data. If not, decompose into P(click) x P(purchase | click): clicks are much more frequent and less noisy,
  especially online. Don't optimise pure expected revenue: it would raise the ticket at the cost of conversion, which
  is what scores.

### 7. Reliable offline evaluation (D9-D10)

- **Why the official example finds few matches:** whole-list or slot-1 IPS requires your list to match the one shown.
  With 5,000 products that almost never happens and the variance explodes. Swaminathan et al. (NeurIPS 2017) propose
  the **pseudoinverse (PI) estimator** for slates, unbiased if the reward is additive per slot, and show its weighted
  version dominates wIPS in all their experimental conditions.\[24\]\[25\] Vlassis et al. (2021) improve it with control
  variates.\[26\] Li et al. (KDD 2018, arXiv:1804.10488) build estimators based on **click models** (per item,
  per item-position, PBM, cascade) that are statistically more efficient, as long as the click model holds.\[27\]
- **Concrete recommendation with ~100,000 impressions:**
  1. **Main estimator, item-position PBM-IPS:** V_hat(pi) = (1/N) sum over impressions sum_k E(k)*theta_hat(pi_k(x), x).
     Here theta_hat(i,x) is the estimated purchase attractiveness of item i in context x, obtained from the log with
     1/propensity weights and Bayesian smoothing towards its subcategory mean (Beta prior). It's a "direct method"
     guided by the click model: small bias if PBM is reasonable, low variance.
  2. **Check, clipped per-slot IPS/SNIPS:** for each slot k, weight the purchases when pi matches the item shown in k,
     with a clipped 1/propensity(k) weight. SNIPS (normalised) reduces variance at the cost of a small bias. It's
     implemented in Open Bandit Pipeline (`obp`, ZOZO), which includes IPW, SNIPW, DR, DR with shrinkage, IIPS for
     slates and Cascade-DR.\[28\]\[29\]
  3. **Doubly robust** = (1) + IPS correction of the residuals. Worth it if you have time left.
  4. **Confidence intervals** with a bootstrap per *session* (not per impression): 200 resamples.
- **Checking that the offline metric predicts the online one (D10):** you get free calibration points. Evaluate
  offline (a) all-time best sellers, (b) best sellers of the last viewed category, and every version of yours deployed
  during practice. If the estimator doesn't rank (b) above (a) and ~20 % higher, it isn't reliable. Use a **temporal
  split** (train days 1-50, evaluate days 51-60) and a **cold segment** (sessions of users with no history and products
  created in the last days) to simulate the final. Report both numbers to the jury.

### 8. Cold start (E11-E12)

- **New products:** qwen3 embeddings are cheap. At 60 requests/min x 32 texts, embedding 5,000 descriptions takes about
  2.6 minutes. Store the normalised vector (or reduced to 256 dimensions with PCA). For a new product: (1) its 20
  nearest neighbours by cosine *within its subcategory*; (2) **inherit their co-visits** as a similarity-weighted average
  of those neighbours' co-visit rows and columns; (3) conversion prior = mean of its neighbours, shrunk towards its
  subcategory's. That's how DropoutNet or CB2CF "place" a cold item in the collaborative space, without a neural network.
  Add optimistic exploration for new products (see F), since their uncertainty is higher.
- **New users with no views:** *recent* popularity (EWMA of purchases, not historical) per (device, region, page), with
  hierarchical smoothing towards the global one. Once there are 1-2 views, the session score of point 5 quickly takes
  over.
- **Safety rule:** precompute fallback lists per context (device x region x page = few combinations) and refresh them
  every minute.

### 9. Learning from the stream (F13-F15)

- **Actual signal volume:** 3 requests/s x 7,200 s = 21,600 requests in the final. At 2 % that's about 430 purchases,
  spread over ~5,000 products: fewer than 0.1 purchases per product. **A per-product bandit can't learn in the final.**
  What can be learned: shared parameters (trend multipliers per subcategory, price weight, stock-out effect) and, above
  all, **clicks**. During practice (~237,600 requests in 22 h) you can tune.
- **What to use:**
  - **Trends:** EWMA of views/carts/purchases per product and per subcategory from *the whole* stream of your feedback
    and the log, with a 15-30 minute half-life. Lift = EWMA_short / (EWMA_long + prior). Multiply the score by
    lift^eta with eta ~ 0.3-0.5 and clip.
  - **Discounted Thompson sampling** (Raj and Kalyani, 2017) over a few "arms": variants of lambda (price), eta (trend)
    or candidate mixes. The discount gamma forgets old data and keeps exploration alive under change.\[30\]\[31\] It fits
    on one slide (Beta(alpha,beta) shrinking with gamma ~ 0.999 per request).
  - **Cascading bandits** (Kveton et al., 2015) is the theoretical basis for learning list order from clicks. Their
    algorithms work surprisingly well even when the user model isn't exactly cascade,\[32\] but learning 5,000
    attractiveness values with 430 purchases is still not viable. Use it as an argument for the jury, not as the main
    implementation.
- **Exploration:** 1-2 of the last slots (8-10) with "uncertain" candidates (new products, emerging trends), picked by
  Thompson sampling from their posterior. Under PBM the cost is E(9)*delta_theta, small. Log the probability each
  explored item was picked with: that way you can do IPS on your own log and explain it to the jury.
- **Merit across positions (F14):** update an item's theta with "effective examinations". An impression at position
  k counts as E(k) observations and a purchase as 1. That's the unbiased estimator under PBM. Use clicks as a dense
  signal with a prior of P(purchase | click) per subcategory.
- **Price and stock (F15):** always filter at serving time with the latest stock table. When a price changes,
  recompute the relative price features, not the model. If a product goes out of stock, its co-visits pass to its
  embedding neighbours in the same subcategory.

### 10. Real-time engineering (G16-G17)

- **Pattern:** the hot path (the answer to each request) **never queries the database synchronously**. A background
  thread refreshes in-memory dictionaries from RawTree/ClickHouse every 30-60 s: stock/active, prices, trend EWMAs, top-N
  co-visits per product and context fallbacks. Each request does dictionary lookups, scores <= 200 candidates,
  deduplicates with a `set`, filters active and in-stock and fills up to 10 with the fallback. With an internal 1 s
  timeout, return the precomputed fallback, which is always valid. Measure p99 latency and the on-time % (target:
  100 %; the GBM team at 61 % shows the cost of not doing so).
- **SQL that scores with the jury (incremental aggregates in ClickHouse):** `AggregatingMergeTree` tables fed by
  materialized views with `countState`/`sumState` per (product, 5-min window). ClickHouse materialized views are INSERT
  triggers that keep real-time aggregates with no scheduled jobs.\[33\] `argMax(price, ts)` gives the current price and
  `argMax(in_stock, ts)` the current stock.\[34\]\[35\] Co-visits are built with `groupArray` + `arrayJoin` per session,
  in one query. For the top-N per row, `ORDER BY ... LIMIT N BY item`. For windows, `windowFunnel` or
  `ts > now() - INTERVAL 30 MINUTE` filters. Check which functions RawTree supports, since it's a derivative and not
  literal ClickHouse.

### 11. Prior art (H18)

- **CLEF NewsREEL (2014-2017):** live news recommendation with a time limit. The 2014 report (Kille et al., "Overview
  of CLEF NEWSREEL 2014") notes that "most systems carried a fall-back solution in terms of most-popular and/or
  most-recent strategies" and that "the baseline is hard to beat", partly attributing it to the technical constraints
  (13 active participants and a 100 ms response limit). Late or invalid answers counted as requests without a click,
  as here.\[36\] In 2015 the best CTRs per period were 0.79 % (irit-imt, 44 clicks in 5,597 requests) and 0.86 % (abc,
  56 clicks in 6,483 requests), according to the NewsREEL 2015 Overview.
- **RecSys 2015 (YOOCHOOSE):** two-stage gradient boosting with massive categorical features won (Romov and Sokolov).
  According to Ben-Shimon et al. ("RecSys Challenge 2015 and the YOOCHOOSE Dataset", RecSys '15), 850 teams from 49
  countries competed with 5,437 solutions, and the winner scored 63,102 points, "approximately 50% of the maximum
  score".
- **OTTO 2023:** co-visits, candidates and a GBDT ranker. The 1st used 1,200 candidates and neural retrieval, but the
  backbone was the same as the silver teams'.\[37\]
- **Diginetica (CIKM Cup 2016) and Retail Rocket:** common benchmarks in the Ludewig/Jannach studies, where SKNN/STAN
  compete with GRU4Rec/SR-GNN. The CIKM Cup winners' details weren't verified for this report.

### 12. Typical mistakes (H19)

1. **Temporal leakage:** building co-visits or popularity with data after the validation cutoff.
2. **Memorising the log:** product IDs as a feature and historical popularity as the dominant feature. What you learn
   that way is the old policy, not the user.
3. **Overfitting to practice:** products, prices and trends change in the final. Every parameter tuned live needs a
   prior and a time discount.
4. **Recommending what was already bought:** always filter it (the hint says so).
5. **Latency and validity:** duplicates, inactive or out-of-stock products, fewer than 10 items. Validate every answer
   before sending it.
6. **Evaluating with whole-list IPS** and concluding "we can't measure": use position-based estimators.

## Prioritised table of techniques (expected gain / effort)

| # | Technique | Expected gain here | Hours | Risk | Basis |
|---|---|---|---|---|---|
| 1 | Robust service: in-memory cache, context fallback, validation, timeout | Avoids losing up to 100 % of a request | 2-3 | Low | NewsREEL; the team at 61 % on time |
| 2 | Exclude bought + stock/active filters | Small but safe | 0.5 | Very low | Official hint |
| 3 | Session candidates: revisits + same subcategory/brand + type- and recency-weighted co-visits | **Very high** (from +20 % to 2x) | 4-5 | Low | OTTO, Ludewig/Jannach, STAN |
| 4 | Position curve E(k) from the log (harvesting/EM) | High (enables 5, 6 and 9) | 2 | Low | Wang 2018; Agarwal 2019 |
| 5 | Offline evaluator PBM-IPS + SNIPS + bootstrap + leaderboard calibration | High (decides everything else; 30 % of the jury) | 3-4 | Medium | Li 2018; Swaminathan 2017; obp |
| 6 | Explicit score: affinity x price (weighted logistic regression) | High | 2-3 | Medium | Joachims 2017 (IPS); own inference |
| 7 | Product cold start with embeddings and inherited co-visits | Medium-high in the final | 2-3 | Low | DropoutNet (principle) |
| 8 | Recent popularity per context (EWMA) for new users | Medium | 1-2 | Low | NewsREEL |
| 9 | EWMA trends + lift | Medium | 1-2 | Low-medium | Intuition + NewsREEL |
| 10 | LightGBM ranker with attribute features | Medium-high if 3-6 work | 5-6 | **Medium-high** (the GBM teams failed) | OTTO, RecSys 2015 |
| 11 | Discounted Thompson over a few hyperparameters + exploration in slots 8-10 | Medium; high for the jury | 2-3 | Medium | Raj and Kalyani 2017; Kveton 2015 |
| 12 | Revenue tie-break (lambda*log price) | Small (tie-break only) | 0.5 | Low | Metric |
| 13 | Doubly robust / full PI | Small on top of 5 | 2-3 | Medium | Swaminathan 2017; obp |
| 14 | Neural models (GRU4Rec, SASRec, SR-GNN) | Low with this budget | 8+ | High | Ludewig et al. 2020 |
| 15 | Per-product bandit (LinUCB/full cascading) | Low in the final (~430 purchases) | 4+ | High | Own calculation |

## Technique cards

**T3 - Session candidates with co-visits.** *What it is:* count how often two products appear together in the same
session, within a short interval, with more weight if the second event was a purchase. To recommend, add up the rows
of the viewed products, with more weight on recent ones. It's "people who looked at this bought that". *Why it fits:*
small catalog, short sessions and 12M events: a dense, reliable table. *References:* Deotte's notebook on Kaggle OTTO
(1:6:3 weights, 24 h and 14-day windows); Ludewig et al. arXiv:1910.12781; STAN (Garg et al., SIGIR 2019). *Python:*
`polars`/`pandas` or SQL in ClickHouse; `session-rec` to try V-SKNN/STAN. *Validation:* Recall@10 of purchases on the
temporal split and the estimator of point 7. *Mistakes:* not normalising by popularity; windows too long; counting
duplicate pairs from the same session.

**T4 - Position curve.** *What it is:* the probability that the user looks at slot k. Estimated by comparing the same
product in different slots, corrected with the propensity. *Why it fits:* you have propensities and randomness in the
log, the ideal case. *References:* Wang et al. WSDM 2018 (regression EM); Agarwal et al. WSDM 2019. *Python:* numpy (a
30-line EM) or `statsmodels` GLM. *Validation:* the curve should be monotonic and stable across halves of the log.
*Mistakes:* confusing the drop by position with the drop by quality, which is exactly what the propensity corrects.

**T5 - Offline evaluator.** *What it is:* a function that, given a policy, estimates its purchases per 100 requests from
the old data. *Why it fits:* it's the only way to decide without spending practice time, and the jury asks for it.
*References:* Li et al. KDD 2018 (arXiv:1804.10488); Swaminathan et al. NeurIPS 2017; Open Bandit Pipeline
(arXiv:2008.07146). *Python:* numpy + `obp`. *Validation:* calibrate with the baseline (0.66 %) and the category
(0.79 %). *Mistakes:* unclipped propensities; bootstrap per impression instead of per session.

**T6/T10 - Score and ranker.** *What it is:* a model that gives each candidate a purchase probability from
interpretable features. *Why it fits:* it combines affinity, price and context, the most likely structure of the
simulator. *References:* Joachims et al. WSDM 2017; Romov and Sokolov, RecSys Challenge 2015; OTTO writeups. *Python:*
`scikit-learn` LogisticRegression first and `lightgbm` later. *Validation:* AUC on the temporal split + the T5 estimator
+ the cold segment. *Mistakes:* ID features; training with future popularity; forgetting latency (load the model once
and predict in batches).

**T7 - Cold start with embeddings.** See point 8. *Mistakes:* exceeding the shared 60 rpm limit (queue and cache);
comparing unnormalised embeddings.

**T11 - Discounted Thompson.** *What it is:* each variant has a Beta distribution of its success rate; on each request
you sample and use the winning variant; old data is slowly forgotten. *Why it fits:* it learns online with few arms and
adapts to change. *References:* Raj and Kalyani, arXiv:1707.09727; Kveton et al. ICML 2015. *Validation:* simulate
offline on the log that the rule converges. *Mistakes:* too many arms for the available signal; rewarding with
purchases when clicks would give 10x more signal.

## Hourly plan (4 people, ~20 h)

| Hours | P1 - Infra/service | P2 - Candidates | P3 - Offline evaluation | P4 - Online/jury |
|---|---|---|---|---|
| 0-3 | Service with cache, context fallback, validator and latency metrics; deploy baseline + bought exclusion | Co-visits in SQL (view->view, view->purchase, cart->purchase) with windows and weights | Load the carousel log; estimate E(k) | Incremental tables in ClickHouse (EWMA, stock, current price); presentation template |
| 3-6 | Plug in P2's policy (v1) and deploy it | Policy v1: revisits + co-visits + subcategory; normalisation | PBM-IPS + SNIPS + bootstrap evaluator; calibration with baseline/category | A/B test of hidden-model hypotheses (subcategory, price, revisit) |
| 6-10 | Description embeddings (queue under the rpm limit); neighbours and inherited co-visits | Weighted logistic regression (affinity x price x context) | Evaluate v1, v2 and variants; temporal split and cold segment | EWMA trends and lift; discounted Thompson over lambda and eta |
| 10-16 | Harden the service: p99, load tests at 10 req/s, database outage drills | LightGBM with attribute features; compare with the logistic regression | Decide with data whether the ranker improves (non-overlapping CIs) | Exploration in slots 8-10 with logged propensities; feedback panel |
| 16-19 | Code freeze; safe mode by default | Fine-tune weights with the evaluator | Validation report for the jury | Rehearse the explanation; final checklist |
| 19-20 | Final deployment and monitoring | Reserve | Reserve | Reserve |

**Decision rule:** every change goes through the offline evaluator and then 30-60 minutes live. If the online
confidence interval doesn't improve, it's rolled back. The big ranker **reuses** everything before it: the candidates
(T3), the position curve (T4), the regression features (T6), the embeddings (T7) and the evaluator (T5). That's why
starting simple isn't wasted time: it's stage 1 of the big model.

## Caveats

- **Solid evidence:** that neighbour methods compete on sessions (Ludewig/Jannach); the co-visits + GBDT pattern of
  OTTO and RecSys 2015; IPS for learning under position bias (Joachims 2017); regression EM (Wang 2018); click-model and
  PI estimators (Li 2018, Swaminathan 2017); the user models of RecSim, SARDINE and RecoGym; NewsREEL (popular and recent
  is hard to beat).
- **Own intuition or inference:** the reading of the leaderboard numbers (price barely penalised, fine affinity); the
  concrete weights (alpha:beta:gamma, h, lambda, eta, half-lives); the estimate that the final has too few purchases to
  learn per product; the hourly plan.
- **Second-hand data:** the details of Deotte's notebook and OTTO's 1st place score come from copies on third-party
  blogs; the exact top-N per matrix isn't verified. The Diginetica/CIKM Cup and Retail Rocket winners weren't verified.
- **RawTree isn't literal ClickHouse:** confirm the syntax (`AggregatingMergeTree`, `argMax`, `LIMIT BY`) before
  designing around it.
- **The simulator may not follow any of the models in the literature.** That's why calibrating the evaluator with the
  real leaderboard points and early A/B tests are the most valuable part of the whole plan.

## Sources

1. [SARDINE: Simulator for Automated Recommendation in Dynamic and Interactive Environments](https://doi.org/10.1145/3656481)
2. [Cascading Bandits: Learning to Rank in the Cascade Model](https://proceedings.mlr.press/v37/kveton15.html)
3. [RecSim: A Configurable Simulation Platform for Recommender Systems](https://arxiv.org/html/1909.04847v2)
4. [SlateQ: Reinforcement Learning in slate recommender problems](https://medium.com/analytics-vidhya/slateq-a-scalable-algorithm-for-slate-recommendation-problems-735a1c24458c)
5. [BLOB: A Probabilistic Model for Recommendation that Combines Organic and Bandit Signals \[Quick Review\]](https://liner.com/review/blob-probabilistic-model-for-recommendation-that-combines-organic-and-bandit)
6. [From Clicks to Conversions: Recommendation for long-term reward](https://arxiv.org/pdf/2009.00497)
7. [Estimating Position Bias without Intrusive Interventions](https://arxiv.org/html/1812.05161)
8. [Position Bias Estimation for Unbiased Learning to Rank in Personal Search · Issue #2 · usaito/CFML-papers · GitHub](https://github.com/usaito/CFML-papers/issues/2)
9. [Multi-modal Adaptive Mixture of Experts for Cold-start Recommendation](https://arxiv.org/pdf/2508.08042)
10. [DropoutNet: Addressing Cold Start in Recommender Systems - rotten-fruits](https://scrapbox.io/rotten-fruits/DropoutNet:_Addressing_Cold_Start_in_Recommender_Systems)
11. [Kaggle OTTO competition review (in Chinese)](https://gladdduck.github.io/2024/03/14/%E5%AE%9E%E4%B9%A0-Kaggle-OTTO%E6%AF%94%E8%B5%9B%E5%9B%9E%E9%A1%BE/index.html)
12. [OTTO: easy-to-use co-visitation matrix function](https://www.kaggle.com/code/mizuny/otto-easy-to-use-co-visitation-matrix-function)
13. [OTTO](https://www.kaggle.com/c/otto-recommender-system/discussion/383130)
14. [GitHub - nicolaivicol/otto-recommender: Solution to kaggle competition OTTO](https://github.com/nicolaivicol/otto-recommender)
15. [GitHub - wenlinxiao/otto-recsys: A real-world e-commerce dataset for session-based recommender systems research.](https://github.com/wenlinxiao/otto-recsys)
16. [OTTO](https://www.kaggle.com/competitions/otto-recommender-system)
17. [RecSys Challenge 2015: ensemble learning with categorical features](https://publications.hse.ru/en/chapters/175896819)
18. [Building a Recommender System using Machine Learning](https://www.leoniemonigatti.com/blog/recommender-system-ml.html)
19. [ex-27th place solution](https://www.kaggle.com/competitions/otto-recommender-system/writeups/dreamteam-ex-27th-place-solution)
20. [Bronze Medal Solution on Kaggle OTTO: Multi-Objective Recommender System](https://ajisamudra.medium.com/bronze-medal-solution-on-kaggle-otto-multi-objective-recommender-system-d0720aed4712)
21. [6th place solution (single model LB 0.603)](https://www.kaggle.com/c/otto-recommender-system/discussion/384120)
22. [Unbiased Learning-to-Rank with Biased Feedback](https://www.alphaxiv.org/abs/1608.04468)
23. [Google Scholar](https://scholar.google.com/scholar_lookup?title=Unbiased+learning-to-rank+with+biased+feedback&amp=&author=T.+Joachims&amp=&author=A.+Swaminathan&amp=&author=T.+Schnabel&amp=&publication_year=2017&amp=&pages=781-789&amp=&doi=10.1145%2F3018661.3018699)
24. [Off-policy evaluation for slate recommendation, Adith Swaminathan](http://papers.neurips.cc/paper/6954-off-policy-evaluation-for-slate-recommendation.pdf)
25. [Reviews: Off-policy evaluation for slate recommendation](https://proceedings.neurips.cc/paper/2017/file/5352696a9ca3397beb79f116f3a33991-Reviews.html)
26. [Control Variates for Slate Off-Policy Evaluation](https://arxiv.org/pdf/2106.07914)
27. [Offline Evaluation of Ranking Policies with Click Models](https://arxiv.org/abs/1804.10488)
28. [GitHub - st-tech/zr-obp: Open Bandit Pipeline: a python library for bandit algorithms and off-policy evaluation](https://github.com/st-tech/zr-obp)
29. [Open Bandit Dataset and Pipeline: Towards Realistic](https://arxiv.org/pdf/2008.07146)
30. [Change Detection Based Thompson Sampling Algorithm For Non-Stationary Bandits](https://repository.iiitd.edu.in/xmlui/bitstream/handle/123456789/868/MT18164_Kunwar%20Zaid.pdf?sequence=1&isAllowed=y)
31. [Taming Non-stationary Bandits: A Bayesian Approach](https://arxiv.org/html/1707.09727)
32. [Cascading Bandits: Learning to Rank in the Cascade Model](https://mlanthology.org/icml/2015/kveton2015icml-cascading/)
33. [ClickHouse Materialized Views: INSERT Triggers, AggregatingMergeTree & Refreshable MVs](https://systeminternals.dev/clickhouse/materialized-views/)
34. [ClickHouse Newsletter June 2022: Materialized, but still real-time](https://clickhouse.com/blog/clickhouse-newsletter-june-2022-materialized-but-still-real-time)
35. [How to Use -ArgMin and -ArgMax Combinators in ClickHouse](https://oneuptime.com/blog/post/2026-03-31-clickhouse-combinator-argmin-argmax/view)
36. [NEWSREEL 2017, CLEF NEWSREEL](https://www.newsreelchallenge.org/previous-campaigns/newsreel-2017/)
37. [Kaggle Otto recommender competition top solutions (in Chinese)](https://lukan217.github.io/2023/02/12/Kaggle%20Otto%E6%8E%A8%E8%8D%90%E7%B3%BB%E7%BB%9F%E6%AF%94%E8%B5%9BTOP%E6%96%B9%E6%A1%88%E6%80%BB%E7%BB%93/)
