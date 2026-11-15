"""The function that picks the 10 products. Owner: Ranking.

    recommend(request, state) -> list of 10 product_ids

`request` is a row of shop.requests as a dict:
    {"request_id", "ts", "user_id", "is_new_user", "context": {"device", "region", "page"},
     "recent_views": [{"product_id", "ts"}, ...]}
In offline evaluation it gets a row of shop.history_sessions with the same keys (some may be missing: always use
.get()).

Rules: it NEVER queries RawTree (it only reads `state`), takes < 5 ms and ALWAYS returns 10 distinct IDs from
`state.available_ids`. If a piece of the State is empty, it's skipped and the next one is used.

STATUS: first version that already combines the pieces as soon as they exist. While covisits, bought,
popular_by_context and neighbours are empty, it behaves like the baseline FROM THE SLIDES (best sellers of the
last viewed category) and, without views, like the starter's (all-time best sellers).
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import chain

import numpy as np

from src import scoring, tree
from src.state import State

NAME = "e-samen-v1"
"""Goes into model_version, which SHOWS UP ON THE PUBLIC LEADERBOARD: only a code name + version (e-samen-v1,
e-samen-v2...), never what it does (other teams would read it). What each version is goes in docs/EXPERIMENTS.md."""

DECAY = 0.7
"""Weight of each view going back: the last one is worth 1, the previous 0.7, the one before 0.49..."""

REVISITS = 0.0
"""How much we push products the shopper ALREADY viewed in this session (and didn't buy). It's a multiplier on the
best co-visit score: with 1.0 the last viewed ties with the best candidate and the previous one lands right behind;
0 turns it off.
OFF by default until Evaluation measures it (candidate for e-samen-v2: params={"revisits": 0.3}).
Check on 3,530 carousel purchases after 2026-11-07: purchases inside our 10 without revisits 14.76 %; with 0.1 ->
15.81 %; 0.3 -> 15.84 %; 1.0 -> 15.67 %.
Why (measured on shop.history_sessions): an already viewed product got bought in 1.96 of every 100 slots of the old
carousel vs 0.12 for unviewed ones (not corrected for position)."""

SUB_AFFINITY = 0.0
BRAND_AFFINITY = 0.0
"""Fine-grained affinity: a candidate sharing the subcategory with something viewed multiplies its points by
(1 + SUB_AFFINITY); if it shares the brand, by (1 + BRAND_AFFINITY). OFF by default until measured."""

NORMALIZE_ROW = False
"""If True, the co-visit weights of each viewed product are divided by the top one in its list (the best is worth 1).
That way every view contributes on the same scale and description neighbours (new products) can be mixed in."""

COHERENT_PRICE = 0.0
"""If the candidate's price is between half and double the mean price of what was viewed, its points are multiplied
by (1 + COHERENT_PRICE). OFF by default."""

PRICE_LAMBDA = 0.0
"""Revenue tie-break: points * (1 + PRICE_LAMBDA * log(price)). The official tie-break is expected revenue.
The deep research suggests 0.05-0.1. OFF by default."""

TREND = 0.0
"""R4. Exponent eta of the trend push: points * lift^eta, with lift from src/trends.py (1 = normal, 3 = three times
the usual activity in the last half hour). The research suggests 0.3-0.5.
Applied to co-visit candidates AND to the order of the filler lists (category best sellers and all-time best
sellers), which is what v0 and shoppers without views get. OFF by default.
Live check (Sat ~17:00): products with lift >= 1.3 keep going in the NEXT half hour at 1.14x their normal pace
(median; mean 1.32) and those with lift < 0.8 at 0.84x (mean 0.91): the trend holds."""

REORDERED_FILL = 30
"""With trends on, how many top slots of each filler list get reordered."""

CANDIDATES_PER_SOURCE = 40
"""R5. With use_model, how many candidates are taken from each source before the model ranks them."""

COLD_HISTORY = 0.0
"""Cold with history (Sat ~18:00 idea): for a shopper WITHOUT views but with previous purchases, seed the co-visits of
what they bought and push what was already bought (value x the best candidate). OFF until measured.
Measured live (leaderboard API, Sat 17:03-18:03): on cold requests, C (no bought filter) 1.73 % vs CF (with filter)
1.41 %, so repurchases seem to help a little ONLY on cold ones; but M (bought products on top for everybody)
0.47 %, so pushing them hard sinks conversion."""

PARAMS: dict = {"decay": DECAY, "revisits": REVISITS, "sub_affinity": SUB_AFFINITY,
                "brand_affinity": BRAND_AFFINITY, "normalize_row": NORMALIZE_ROW,
                "coherent_price": COHERENT_PRICE, "price_lambda": PRICE_LAMBDA,
                "use_neighbours": False, "use_context": False, "trend": TREND,
                "use_model": False, "filter_bought": True, "cold_history": COLD_HISTORY,
                "use_tree": False}
"""Default parameters. The A/B variants (src/service.py) pass another dict with the same keys: that way variants get
tried live without touching this file. Every new key is added here with its value.
- use_tree (R6): the 10 are picked by the LightGBM in src/tree.py among the products of the last viewed category
  (no views: the 300 best by quality and popularity). Needs data/tree.txt and data/tree.json (python -m src.tree)
  and lightgbm; if anything is missing it's ignored with a console warning. OFF.
- filter_bought: drop what was already bought. Live (API, 1 h, ~2,800 requests/arm) CF (WITH filter) 1.06 % vs C
  (no filter) 1.02 %: overall a tie. Left on (it's what ran live with the most traffic). Note: CF = WITH filter.
- use_model (R5): the 10 are picked by the logistic regression in src/scoring.py instead of the sum of points.
  Needs data/scoring.json (python -m src.scoring); if it's missing it's ignored (with a console warning). Already
  bought products aren't candidates and, with filter_bought, get removed (the first version put them 1st: it learned
  the history's repurchases, which don't exist live). OFF.
- use_neighbours / use_context: cold-start pieces (D3, D4). OFF until Evaluation or an A/B says they help: without a
  switch, a plain service restart would change the live model without measuring it."""


def _all(state: State):
    """Every available product in a fixed order. It's a generator: it only gets sorted if it's actually reached
    (almost never), because sorting 5,000 IDs was 95 % of recommend()'s time."""
    yield from sorted(state.available_ids)


def _price(state: State, pid: str) -> float:
    try:
        return float(state.catalog_info.get(pid, {}).get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


def _by_trend(items_list: list[str], lift: dict[str, float], eta: float) -> list[str]:
    """Reorders the first REORDERED_FILL of a best-seller list. Each slot is worth 1/log2(slot + 2) (1st = 1,
    2nd = 0.63, 3rd = 0.5...) times lift^eta: a product on the rise moves up a few slots, but a normal one doesn't
    sink. The rest of the list stays the same behind."""
    head = items_list[:REORDERED_FILL]
    points = {pid: lift.get(pid, 1.0) ** eta / math.log2(pos + 2) for pos, pid in enumerate(head)}
    return sorted(head, key=points.__getitem__, reverse=True) + items_list[REORDERED_FILL:]


def _with_model(request: dict, state: State, viewed: list[str], points: Counter, lift: dict[str, float],
                eta: float, is_ok) -> list[str] | None:
    """R5. Gathers candidates (their category's best sellers, all-time best sellers, co-visits and what they viewed),
    scores them with the logistic regression and returns the best 10. `is_ok` = available (and not bought if
    filter_bought). None if there's no model."""
    model = scoring.saved_model()
    if model is None:
        return None
    n = CANDIDATES_PER_SOURCE
    last_category = str(state.catalog_info.get(viewed[-1], {}).get("category", "")) if viewed else ""
    sources = (state.popular_by_category.get(last_category, [])[:n], state.popular[:n],
               [pid for pid, _ in points.most_common(n)], list(reversed(viewed)))
    candidates = list(dict.fromkeys(pid for pid in chain(*sources) if is_ok(pid)))
    if not candidates:
        return []
    s = scoring.score_candidates(request, candidates, state, model)
    if eta > 0 and lift:  # trends: points * lift^eta  ==  score + eta*log(lift)
        s = s + eta * np.log([lift.get(pid, 1.0) for pid in candidates])
    return [candidates[i] for i in np.argsort(-s, kind="stable")[:10]]


def recommend(request: dict, state: State, params: dict | None = None) -> list[str]:
    p = {**PARAMS, **(params or {})}
    # filter_bought (R2): drop what they already bought. ON: the organisers confirm there are no repurchases live
    # (the history's 61.6 % repurchases is an artefact) and live CF (v0 + filter) is ahead of C.
    already_bought = (state.bought.get(str(request.get("user_id", "")), set())
                      if p["filter_bought"] else set())

    def is_ok(pid: str) -> bool:
        return pid in state.available_ids and pid not in already_bought

    viewed = [v["product_id"] for v in (request.get("recent_views") or []) if v.get("product_id")]
    points: Counter[str] = Counter()

    # 1) co-visits of what they viewed (most recent weighs more)
    for k, pid in enumerate(reversed(viewed)):
        items_list = state.covisits.get(pid, [])
        scale = items_list[0][1] if (p["normalize_row"] and items_list and items_list[0][1] > 0) else 1.0
        for pos, (other, weight) in enumerate(items_list):
            points[other] += (p["decay"] ** k) * (weight / scale) / (pos + 1)

    # 1b) "cold" shopper (no views) but with a purchase history: recommend what they ALREADY bought (repurchase, which
    #     the hidden model rewards) and the co-visits of their bought products. Only 6.3 % of shoppers without views
    #     are truly new; for those this does nothing.
    if p["cold_history"] > 0 and not viewed:
        bought_u = sorted(state.bought.get(str(request.get("user_id", "")), set()))[:10]
        for pid in bought_u:
            n_bought = max(len(bought_u), 1)
            for pos, (other, weight) in enumerate(state.covisits.get(pid, [])):
                points[other] += weight / ((pos + 1) * n_bought)
        if points:  # each bought product gets pushed 1.2x the best seeded candidate: repurchase rules
            cap = max(points.values())
            for pid in bought_u:
                if pid in state.available_ids:
                    points[pid] += p["cold_history"] * cap

    # 2) new products (no co-visits): use the ones with a similar description
    for k, pid in enumerate(reversed(viewed) if p["use_neighbours"] else []):
        # "new" = not in the history's co-visits. If co-visits are empty we can't tell what's new, so neighbours
        # aren't used (otherwise they'd apply to EVERYTHING and bury the rest: measured by Evaluation).
        if state.covisits and pid not in state.covisits:
            for pos, other in enumerate(state.neighbours.get(pid, [])):
                points[other] += 0.5 * (p["decay"] ** k) / (pos + 1)

    # 3) fine-grained affinity: same subcategory or brand as something viewed
    if (p["sub_affinity"] > 0 or p["brand_affinity"] > 0) and viewed:
        subs = {state.catalog_info.get(v, {}).get("subcategory") for v in viewed} - {None, ""}
        brands = {state.catalog_info.get(v, {}).get("brand") for v in viewed} - {None, ""}
        for pid in points:
            payload = state.catalog_info.get(pid, {})
            factor = 1.0
            if payload.get("subcategory") in subs:
                factor += p["sub_affinity"]
            if payload.get("brand") in brands:
                factor += p["brand_affinity"]
            points[pid] *= factor

    # 3b) price: coherent with what was viewed, plus the revenue tie-break
    if (p["coherent_price"] > 0 or p["price_lambda"] > 0) and points:
        viewed_prices = [_price(state, v) for v in viewed]
        viewed_prices = [x for x in viewed_prices if x > 0]
        mid_price = sum(viewed_prices) / len(viewed_prices) if viewed_prices else 0.0
        for pid in points:
            price = _price(state, pid)
            if price <= 0:
                continue
            if p["coherent_price"] > 0 and mid_price > 0 and mid_price / 2 <= price <= mid_price * 2:
                points[pid] *= 1 + p["coherent_price"]
            if p["price_lambda"] > 0:
                points[pid] *= 1 + p["price_lambda"] * math.log(max(price, 1.0))

    # 3c) trends (R4): what's moving now goes up; what has stopped goes down a bit.
    #     `trends` is filled by the service; getattr makes it work even if the State doesn't have the field yet.
    lift = getattr(state, "trends", None) or {}
    with_trend = p["trend"] > 0 and bool(lift)
    if with_trend:
        for pid in points:
            points[pid] *= lift.get(pid, 1.0) ** p["trend"]

    # 4) revisits: what they already looked at and didn't buy. Each product counts once, by its most recent view.
    if p["revisits"] > 0 and viewed:
        cap = max(points.values(), default=1.0)
        counted: set[str] = set()
        for k, pid in enumerate(reversed(viewed)):
            if pid not in counted:
                counted.add(pid)
                points[pid] += p["revisits"] * (p["decay"] ** k) * cap

    chosen = None
    if p["use_tree"] and (tree_model := tree.saved_model()) is not None:
        cand = tree.candidates(request, state, tree_model, is_ok)
        if cand:
            s = tree.score_candidates(request, cand, state, tree_model)
            chosen = [cand[i] for i in np.argsort(-s, kind="stable")[:10]]
    if chosen is None and p["use_model"]:
        chosen = _with_model(request, state, viewed, points, lift, p["trend"], is_ok)
    if chosen is None:
        chosen = [pid for pid, _ in points.most_common() if is_ok(pid)][:10]

    # 5) filler, in this order: best sellers of the last viewed category (the baseline from the slides), best sellers
    #    for their kind of shopper, all-time best sellers and anything available.
    #    (recent_views is assumed to go from oldest to newest, as in the official notebook.)
    last_category = str(state.catalog_info.get(viewed[-1], {}).get("category", "")) if viewed else ""
    from_their_category = state.popular_by_category.get(last_category, [])
    ctx = request.get("context") or {}
    by_context = (state.popular_by_context.get((str(ctx.get("device", "")), str(ctx.get("region", ""))), [])
                  if p["use_context"] else [])
    popular = state.popular
    if with_trend:
        from_their_category = _by_trend(from_their_category, lift, p["trend"])
        popular = _by_trend(popular, lift, p["trend"])
    for source in (from_their_category, by_context, popular, _all(state)):
        for pid in source:
            if len(chosen) == 10:
                return chosen
            if pid not in chosen and is_ok(pid):
                chosen.append(pid)
    # if there still aren't enough (the user bought almost everything), relax the "already bought" rule
    for pid in chain(state.popular, _all(state)):
        if len(chosen) == 10:
            break
        if pid not in chosen and pid in state.available_ids:
            chosen.append(pid)
    return chosen
