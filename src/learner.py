"""e-samen · Learner: a model that learns from OUR live feedback. This is the model behind AP7, the final version.

    python -m src.learner                      # download what's new (feedback, requests, events), retrain, validate, save
    python -m src.learner --dry-run 3000       # also rehearse recommend() on real requests (sends NOTHING)
    python -m src.learner --no-download        # retrain with what is already in data/learner/
    python -m src.learner --name ap3           # save to data/learner_ap3.json WITHOUT overwriting data/learner.json (A/B)
    python -m src.learner --name ap7c --without same_sub   # same training without one signal (clean A/B control)

Why (measured on Sat 14/11 with 77,680 of our responses): what moves purchases in the simulator is not which popular
product goes first, but the previous relationship of THAT shopper with THAT product and with its category:
  - in today's cart, not bought ×5.7 · in an old cart, never bought ×2.2 · already bought ×0.05
  - viewed in this session ×2.4 (more if it is also a good seller)
  - share of their views that fall in that category: from 0.01 % (almost never views it) to 0.83 % (over 40 %) in slot 1
The old carousel history cannot teach any of this (it only showed popular products), so the WEIGHTS here come only
from `team_29.feedback`. The history is only used as memory of what each shopper did (carts, purchases, which
categories they view) and weighs little in the per-product numbers (HISTORY_WEIGHT).

How it works, in three pieces:
  1. `World`: what is known about each shopper and each product at a given instant. Fed event by event
     (`world.event(...)`) from shop.events. It is the SAME class for training and for recommending.
  2. Training = replaying the day in order: before each of our responses only the events BEFORE that request are
     applied (no future leakage) and the signals of the 10 products shown are recorded. With that, a logistic
     regression (a single neuron) learns how much each signal is worth; the list position enters as a signal to
     discount it and is ignored when recommending.
  3. `recommend(request, world, model, available)`: gathers candidates (their carts, what they viewed, the best of
     their categories and of the last viewed product), scores them and returns 10 valid ones. Never shows what was
     already bought.

It improves by itself: each run downloads only the new feedback and events and retrains with everything so far.

Wiring it into the live service (src/service.py does exactly this):
    world = learner.World.up_to_date(catalog)            # at startup: history + today's events (~1 min)
    world.catch_up()                                     # in the refresh thread, every 1-2 s (one light query)
    world.catalog = catalog                              # every time the service reloads the catalog
    model = learner.Model.load("aphc")                   # and again after each retraining
    items = learner.recommend(request, world, model, available)

Model versions (all of them still load and run; numbers in docs/EXPERIMENTS.md):
  AP2/AP3 (Sat evening)  the per-product model with 11 signals, retrained as feedback grew.
  AP7 (Sat night)        + `same_sub`. The final model is AP7 retrained at 03:40 on Sunday (file "aphc").
      python -m src.learner --name ap7                               # with the signal (learned from feedback: ×1.39)
      python -m src.learner --no-download --name ap7c --without same_sub   # control: same data, without it
      learner.recommend(request, world, Model.load("ap7"), available, force={"same_sub": 3.0})   # strong arm
  APO/APH (Sun early)    groups "o1", "o2", "h" of GROUPS: organic history numbers, new-user weights, hybrid.
  AP8 (Sun 06:00)        CHOICE model: learns "which of the 10 shown was chosen" (see GROUPS["x"]):
      python -m src.learner --name ap8 --choice --without o1,o2
      python -m src.learner --no-download --name ap8c --without o1,o2,h,x   # per-product control (= AP7)
  Live A/B AP7 vs AP8 (Sun 06:15-07:26): a tie, so AP7 stays.
Models saved by the Spanish version of this file (data/aprendiz_<name>.json) are loaded too: see LEGACY_SIGNALS.

Exploration (`recommend(..., explore=True)`): in half of the requests (EXPLORE_FRACTION, chosen by request_id) slots
9 and 10 stop being the model's and show a NEW product and one from OUTSIDE the best-of lists, both from a category
that shopper cares about. Why: we only learn about what we show; of new products we had shown 1,698 and seen 4
purchases all Saturday. Since the exploring half is chosen by request_id, it can be rebuilt later without logging
anything (`should_explore`) and the other half of the same arm is the comparison group. Measured live: exploring
cost ~0.3 purchases/100 in the requests that explore, so it is OFF in the final service.
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import random
import threading
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data" / "learner"
MODEL_FILE = DATA.parent / "learner.json"

LIVE_START_MS = 1794646800000
"""Sat 14/11 09:00 UTC (10:00 in Barcelona): first event in shop.events."""
HISTORY_WEIGHT = 0.1
"""How much each view/purchase from the 60-day history counts against one from today in the per-product numbers.
With 0.1 the whole history weighs like ~6 days, so new products are not penalised for long."""
PSEUDO_VIEWS = 50.0
"""Smoothing of the purchase rate: a product with few views moves towards the mean rate instead of exploding."""
L2 = 1.0
CLICK_WEIGHT = 0.0
"""Learn from PURCHASES only (that is what scores). Tried counting each click as 0.3 purchases: validation is the
same (logloss 0.00660 vs 0.00659) and the weights stop reading as "× purchase probability"."""
WINDOW_MS = 20 * 60 * 1000
DELAY_MS = 3000
"""During training the World is assumed to lag 3 s behind the request, as it does live (measured: 2.7 s with an event
poll every 1.5 s). So what is learned is what will really be known when answering."""
N_CATEGORY, N_SUBCATEGORY, N_GLOBAL = 40, 20, 40
"""Filler candidates: the best of each category / subcategory / of the whole shop."""
TOP_CATEGORIES, TOP_SUBCATEGORIES = 3, 5
LISTS_TTL_S = 120
"""How often the best-of lists per category are recomputed (the per-product numbers keep changing)."""
EXPLORE_FRACTION = 0.5
"""Share of requests that explore when `explore=True` is asked. The other share is the comparison group."""
SLOT_NEW, SLOT_REST = 8, 9
"""Slots 9 and 10 (counting from 0): the least valuable ones (×0.37 of the first), so exploring there is cheap."""
EXPLORE_TRIES = 30

NEW_USER_SIGNALS = ["cart_today", "viewed_session", "aff_cat", "aff_sub", "same_cat", "pop", "rate", "same_sub"]
"""APO1: signals with a different weight for new shoppers (is_new_user). For a new shopper the weight is the signal's
plus its `n_` one. cart_old and bought are missing: a new shopper has no history."""
PSEUDO_TYPE = 500.0
"""Smoothing of the rate of a product type or a brand (in history views): with few views it moves to the mean."""

VIEW, CART, PURCHASE = 0, 1, 2
_EVENT_TYPE = {"view": VIEW, "add_to_cart": CART, "purchase": PURCHASE}

SIGNALS = [
    ("cart_today", "added to the cart today and not bought"),
    ("cart_old", "added to the cart in the history and never bought"),
    ("viewed_session", "in recent_views (and not bought)"),
    ("bought", "already bought (today or in the history). Always removed when recommending"),
    ("aff_cat", "square root of the share of their views that fall in the product's category"),
    ("aff_sub", "same with the subcategory"),
    ("same_cat", "same category as the last viewed product"),
    ("pop", "log of the share of the shop's purchases that go to the product"),
    ("rate", "log of purchases per view of the product (smoothed)"),
    ("no_views", "the request has no recent_views (home). Does not change the order: adjusts the level"),
    ("new_user", "new shopper (is_new_user). Does not change the order: adjusts the level"),
    ("same_sub", "same subcategory as the last viewed product, for products that are NOT theirs (no cart, not viewed)"),
    ("type_rate", "ORGANIC: log of purchases per view of products of its TYPE (subcategory and price band)"),
    ("type_pop", "ORGANIC: log of what an average product of its type sells"),
    ("brand_rate", "ORGANIC: log of purchases per view of its brand"),
] + [("n_" + n, f"NEW shoppers only: correction of the weight of {n}") for n in NEW_USER_SIGNALS] + [
    ("cobuy_today", "TODAY: of those who viewed what this shopper viewed, how much did they end up buying this product? (shop.events)"),
    ("cf_hist", "HISTORY: of those who bought what this shopper bought, how much did they also buy this product? (60 days)"),
    ("taste_hist", "HISTORY: personal taste (factorisation of 60 days of carts and purchases): how well it fits THAT shopper"),
    ("ctx_dev_sub", "CONTEXT: how much more or less its subcategory is bought from that device (log, 60 days of purchases)"),
    ("session_cat_share", "share of recent_views that belongs to the product's category"),
    ("new_product", "NEW product: no activity in the 60-day history (its own numbers say nothing yet)"),
    ("price_rel_sub", "log of the price divided by the median price of its subcategory"),
    ("views_today_u", "log(1 + times this shopper has viewed the product today)"),
    ("co_any", "cobuy_today > 0: someone who viewed today what this shopper viewed ended up buying the product (yes/no)"),
    ("cf_any", "cf_hist > 0: someone who bought what this shopper bought also bought the product (yes/no)"),
    ("co_sqrt", "square root of cobuy_today: lets that signal rise fast at first and then flatten"),
    ("cf_sqrt", "square root of cf_hist: same"),
]
"""Few on purpose. Tried and removed on Sat 14/11 because they did not improve validation (each moved the error by
less than 0.1 %): is the last viewed product, viewed today outside the session, device, and "we know something about
the shopper" (replaced by `new_user`).
`same_sub` was added on Saturday night (AP7): in the history purchases, the best sellers of the last viewed
subcategory hit 20.5 % (recall@10 from `python -m src.evaluate --recall`) vs 9.2 % for category affinity; in our
carousel the effect is smaller (×1.47 on unrelated products; nothing on theirs).
New signals ALWAYS go at the end of the list: a model saved before a signal existed does not have it and still loads,
with weight 0 for it (so AP3 keeps giving exactly the same lists)."""
NAMES = [n for n, _ in SIGNALS]
_I = {n: i for i, n in enumerate(NAMES)}
GROUPS = {"o1": ["n_" + n for n in NEW_USER_SIGNALS], "o2": ["type_rate", "type_pop", "brand_rate"]}
"""APO (Sun early morning; O = organic data). Two ideas, each a group of signals, for an A/B with a shared control:
  o1 = own weights for new shoppers (the model predicted 3.24 purchases/100 for new shoppers and 2.67 came; for
       known ones 1.29 and 1.75 came: the signals are not worth the same in both groups).
  o2 = what is known about the product TYPE in the 60 days of shop.history_events (purchases per view and average
       sales of its subcategory and price band, and of its brand). A new product has no own numbers, but it has a type.
       The numbers come from the history; how much they WEIGH is decided, as always, by our feedback.
    python -m src.learner --name apoc --without o1,o2                  # control
    python -m src.learner --no-download --name apo1 --without o2       # control + o1
    python -m src.learner --no-download --name apo2 --without o1       # control + o2"""
GROUPS["h"] = ["cobuy_today", "cf_hist"]
"""APH1 (Sun early morning) = HYBRID model: two specialists and a split by shopper type.
  - `cobuy_today` learns from TODAY's shop, event by event: it works for new shoppers and new products.
  - `cf_hist` uses each known shopper's 60 days of purchases (item-to-item collaborative filtering).
  - Also TWO regressions are trained, one only with responses to new shoppers and one with known ones
    (`--only new` / `--only known`); the service uses one or the other depending on `is_new_user`.
    python -m src.learner --name aphc --without o1,o2,h                                 # control (= AP7 retrained)
    python -m src.learner --no-download --name aph1_n --without o1,o2 --only new        # new-shopper specialist
    python -m src.learner --no-download --name aph1_c --without o1,o2 --only known      # known-shopper specialist"""
N_COBUY, N_CF, CF_NEIGHBORS, TODAY_SOURCES = 15, 20, 30, 8
"""Candidates contributed by each new source, neighbours stored per product and how many viewed products are used."""
PSEUDO_COBUY = 20.0
GROUPS["x"] = ["taste_hist", "ctx_dev_sub", "session_cat_share", "new_product", "price_rel_sub", "views_today_u",
               "co_any", "cf_any", "co_sqrt", "cf_sqrt"]
"""AP8 (Sun 06:00) = CHOICE model (`--choice`). Two changes over the hybrid:
  1. How it learns. Until then: "is this shown product bought, yes or no?" (one row per product). That mixes two
     things in the weights: which PRODUCT is better and which REQUEST was going to buy anyway (whoever has a cart
     today buys more, whatever you show). Now: "of the 10 we showed, which one was chosen?" (conditional logit, only
     lists with a purchase or a click). What is equal for the 10 cancels out and only what helps to ORDER remains.
     Result: their own products weigh less (cart today ×6 -> ×2.4) and the choice measure improves (EXPERIMENTS, Sun 06:05).
  2. New signals (group `x`), each checked by removing or adding it: personal taste from factorising the history,
     context (device × subcategory), session share in that category, new product, price vs the typical one in its
     subcategory and how often they viewed it today. The last four (`co_*`, `cf_*`) come from trying trees (LightGBM)
     on top of the model: what they found was mostly the SHAPE of cobuy_today and cf_hist (rise fast, then flatten).
     With that shape inside, the model is still a readable sum of weights.
    python -m src.learner --name ap8 --choice --without o1,o2                # the model
    python -m src.learner --no-download --name ap8c --without o1,o2,h,x       # per-product control (= AP7), same moment
  Live it tied with AP7 (Sun 07:26): more clicks (+14 %), no more purchases."""
N_TASTE, TASTE_DIM = 15, 64
"""Candidates contributed by personal taste and number of factors in the factorisation."""
MAX_NEW = 2
"""Cap on NEW products that are not the shopper's in a list of the choice model. Why: the model gives them ×4.8 (and,
when shown, they sell ~2 times more than a known product in the same slot), but we had only shown them ~0.5 times
per list; without a cap it would put ~5 per list, far beyond what we have seen. New products the shopper viewed or
added to the cart do not count towards the cap."""
CHOICE_CLICK_WEIGHT, CHOICE_L2 = 0.3, 3.0
"""In the choice model a list with a click and no purchase counts 0.3 (there are 3 times more clicks than purchases:
they refine the weights without overruling purchases). Tried 0 / 0.15 / 0.3 / 0.5 / 1 and L2 1-30: barely changes."""
_N_BASE = _I["brand_rate"] + 1
_I_CO, _I_CF = _I["cobuy_today"], _I["cf_hist"]
_I_X = [_I[n] for n in GROUPS["x"]]
_I_TASTE, _I_X5 = _I["taste_hist"], slice(_I["ctx_dev_sub"], _I["views_today_u"] + 1)
_I_SHAPE = [_I[n] for n in ("co_any", "cf_any", "co_sqrt", "cf_sqrt")]
_I_NEW_BASE = [_I[n] for n in NEW_USER_SIGNALS]
_I_NEW = [_I["n_" + n] for n in NEW_USER_SIGNALS]
PRODUCT_SIGNALS = ["pop", "rate", "type_rate", "type_pop", "brand_rate"]
"""Signals that depend only on the product: the best-of lists (candidates) are built with them."""

LEGACY_SIGNALS = {
    "carrito_hoy": "cart_today", "carrito_antiguo": "cart_old", "visto_sesion": "viewed_session",
    "ya_comprado": "bought", "afin_cat": "aff_cat", "afin_sub": "aff_sub", "misma_cat": "same_cat", "pop": "pop",
    "tasa": "rate", "sin_vistas": "no_views", "nuevo": "new_user", "misma_sub": "same_sub", "tasa_tipo": "type_rate",
    "pop_tipo": "type_pop", "tasa_marca": "brand_rate", "cocompra_hoy": "cobuy_today", "cf_hist": "cf_hist",
    "gusto_hist": "taste_hist", "ctx_dev_sub": "ctx_dev_sub", "parte_ses_cat": "session_cat_share",
    "prod_nuevo": "new_product", "precio_rel_sub": "price_rel_sub", "vistas_hoy_u": "views_today_u",
    "co_hay": "co_any", "cf_hay": "cf_any", "co_raiz": "co_sqrt", "cf_raiz": "cf_sqrt",
}
"""Spanish signal names used by the models trained during the event (data/aprendiz_<name>.json)."""
LEGACY_SIGNALS.update({"n_" + es: "n_" + en for es, en in list(LEGACY_SIGNALS.items())})


# ---------------------------------------------------------------- RawTree (read only)
def _query(sql: str, timeout: float = 300) -> list[dict]:
    from src.rawtree import query
    for attempt in range(4):
        try:
            return query(sql, timeout=timeout)
        except Exception as e:  # unstable network: retry a few times
            if attempt == 3:
                raise
            print(f"  retry ({e})"[:160], flush=True)
            time.sleep(2 + 2 * attempt)
    return []


def _server_now_ms() -> int:
    """The time comes from RawTree: the laptop clock runs seconds behind (measured by the Operator)."""
    return int(_query("SELECT toUnixTimestamp64Milli(now64(3)) AS t", 20)[0]["t"])


def _between(a_ms: int, b_ms: int) -> str:
    return (f"ts::DateTime64(3) >= fromUnixTimestamp64Milli(toInt64({a_ms})) "
            f"AND ts::DateTime64(3) < fromUnixTimestamp64Milli(toInt64({b_ms}))")


def _save(name: str, obj) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / (name + ".tmp")
    tmp.write_bytes(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(DATA / name)  # atomic swap: if interrupted halfway, no broken file is left


def _read(name: str, empty):
    f = DATA / name
    return pickle.loads(f.read_bytes()) if f.exists() else empty


def load_history() -> dict:
    """Memory of the 60 days: downloaded ONCE (the history does not change) and stored in data/learner/.
    Returns purchases and carts per shopper, their views per subcategory and views/purchases per product."""
    h = _read("history.pkl", None)
    if h is not None:
        return _with_device(h)
    print("history: downloading (first time only, ~1-2 min)...", flush=True)
    pairs = {}
    for kind in ("purchase", "add_to_cart"):
        d: dict[str, set] = defaultdict(set)
        for f in _query(f"""SELECT user_id::String AS u, product_id::String AS p FROM shop.history_events
                            WHERE event_type = '{kind}' GROUP BY u, p LIMIT 5000000"""):
            d[sys.intern(f["u"])].add(sys.intern(f["p"]))
        pairs[kind] = dict(d)
    affinity: dict[str, dict] = defaultdict(dict)
    chunks = 8
    for k in range(chunks):  # views per shopper and subcategory (events have no category: join with the catalog)
        for f in _query(f"""SELECT e.user_id::String AS u, c.s AS s, count() AS n
              FROM shop.history_events AS e
              INNER JOIN (SELECT product_id::String AS product_id, any(subcategory::String) AS s
                          FROM shop.catalog GROUP BY product_id) AS c ON e.product_id::String = c.product_id
              WHERE e.event_type = 'view' AND cityHash64(e.user_id::String) % {chunks} = {k}
              GROUP BY u, s LIMIT 5000000"""):
            affinity[sys.intern(f["u"])][sys.intern(f["s"])] = int(f["n"])
    products = {f["p"]: (int(f["v"]), int(f["c"])) for f in _query(
        """SELECT product_id::String AS p, countIf(event_type = 'view') AS v, countIf(event_type = 'purchase') AS c
           FROM shop.history_events GROUP BY p""")}
    h = {"purchases": pairs["purchase"], "carts": pairs["add_to_cart"], "affinity": dict(affinity), "products": products}
    _save("history.pkl", h)
    print(f"history: {len(h['purchases'])} shoppers with purchases, {len(h['carts'])} with carts, "
          f"{len(h['affinity'])} with views, {len(products)} products", flush=True)
    return _with_device(h)


def _with_device(h: dict) -> dict:
    """Adds purchases per device and product to the stored history (for `ctx_dev_sub`). One light query, once.
    If it fails the signal is 0 and everything else stays the same (it is retried on the next start)."""
    if "device" not in h:
        try:
            rows = _query("""SELECT device::String AS d, product_id::String AS p, count() AS n FROM shop.history_events
                             WHERE event_type = 'purchase' GROUP BY d, p LIMIT 500000""", 60)
            h["device"] = [(f["d"], f["p"], int(f["n"])) for f in rows]
            _save("history.pkl", h)
        except Exception as e:
            print(f"history: no purchases per device ({e})"[:160], flush=True)
    return h


def download_live(until_ms: int | None = None) -> tuple[list, dict, dict]:
    """INCREMENTAL download of today's data: only what is newer than what is already in data/learner/.
    Returns (sorted events [(t_ms, user, product, type)], requests {request_id: ...}, feedback {request_id: ...})."""
    until_ms = until_ms or _server_now_ms() - 5000
    ev = _read("events.pkl", {"until_ms": LIVE_START_MS, "rows": []})
    a = ev["until_ms"]
    while a < until_ms:
        b = min(a + WINDOW_MS, until_ms)
        rows = _query(f"""SELECT toUnixTimestamp64Milli(ts::DateTime64(3)) AS t, user_id::String AS u,
                          product_id::String AS p, event_type::String AS e FROM shop.events WHERE {_between(a, b)}
                          LIMIT 1000000""")
        ev["rows"] += [(int(f["t"]), sys.intern(f["u"]), sys.intern(f["p"]), _EVENT_TYPE[f["e"]])
                       for f in rows if f["e"] in _EVENT_TYPE]
        print(f"  events {time.strftime('%H:%M', time.gmtime(a / 1000))} UTC: {len(rows)}", flush=True)
        a = b
    ev["rows"].sort(key=lambda f: f[0])
    ev["until_ms"] = until_ms
    _save("events.pkl", ev)

    # Requests and feedback: a 10-minute margin is downloaded again in case some row arrived late (overwritten by id).
    rf = _read("responses.pkl", {"until_ms": LIVE_START_MS, "requests": {}, "feedback": {}, "version": {}})
    a = max(LIVE_START_MS, rf["until_ms"] - 10 * 60 * 1000)
    while a < until_ms:
        b = min(a + WINDOW_MS, until_ms)
        for f in _query(f"""SELECT request_id::String AS rid, toUnixTimestamp64Milli(ts::DateTime64(3)) AS t,
                            user_id::String AS u, is_new_user::Bool AS new_user, `context.device`::String AS dev,
                            recent_views AS rv FROM shop.requests WHERE {_between(a, b)} LIMIT 1000000"""):
            rf["requests"][f["rid"]] = (int(f["t"]), sys.intern(f["u"]), bool(f["new_user"]), sys.intern(f["dev"] or ""),
                                        tuple(sys.intern(p) for p in _viewed_products(f["rv"])))
        n = 0
        for f in _query(f"""SELECT request_id::String AS rid, shown, clicked::String AS c, purchased::String AS p,
                            served_by::String AS sb, model_version::String AS mv FROM {_team()}.feedback
                            WHERE {_between(a, b)} AND request_id::String != '__init__' LIMIT 1000000"""):
            shown = _as_list(f["shown"])
            if len(shown) == 10:
                rf["feedback"][f["rid"]] = (tuple(sys.intern(p) for p in shown), f["c"] or "", f["p"] or "", f["sb"] == "team")
                rf["version"][f["rid"]] = sys.intern(f["mv"] or "")
                n += 1
        print(f"  feedback {time.strftime('%H:%M', time.gmtime(a / 1000))} UTC: {n}", flush=True)
        a = b
    rf["until_ms"] = until_ms
    _save("responses.pkl", rf)
    return ev["rows"], rf["requests"], rf["feedback"]


def _team() -> str:
    from src.rawtree import TEAM
    return TEAM


def _as_list(x) -> list:
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except ValueError:
            return []
    return list(x or [])


def _viewed_products(rv) -> list[str]:
    """recent_views -> [product_id, ...] from oldest to newest (may contain repeats)."""
    return [v["product_id"] for v in _as_list(rv) if isinstance(v, dict) and v.get("product_id")]


# ---------------------------------------------------------------- the World: what is known at an instant
class World:
    """State per shopper and per product, fed event by event. One thread writes (`event`, `catch_up`) and another
    reads (`recommend`): the lock avoids reading a dictionary halfway through a change."""

    def __init__(self, catalog: dict[str, dict], history: dict | None = None):
        self.catalog = catalog
        h = history or {"purchases": {}, "carts": {}, "affinity": {}, "products": {}}
        self.bought: dict[str, set] = {u: set(s) for u, s in h["purchases"].items()}
        self.cart_old: dict[str, set] = h["carts"]             # includes what was bought: subtracted when reading
        self.cart_today: dict[str, set] = {}
        self.known: set = set(h["products"])    # products with activity in the history: the rest are NEW
        self.views_sub: dict[str, dict] = {u: dict(d) for u, d in h["affinity"].items()}  # user -> subcat -> n
        self.views_p: dict[str, float] = {}
        self.purchases_p: dict[str, float] = {}
        for p, (v, c) in h["products"].items():
            self.views_p[p] = HISTORY_WEIGHT * v
            self.purchases_p[p] = HISTORY_WEIGHT * c
        self.total_views = sum(self.views_p.values())
        self.total_purchases = sum(self.purchases_p.values())
        self.until_ms = LIVE_START_MS
        self._recent: set = set()
        self._lock = threading.Lock()
        self._types = _type_numbers(catalog, h["products"])
        self.views_today: dict[str, list] = {}       # shopper -> last products viewed today (no repeats)
        self.views_today_p: dict[str, int] = {}      # product -> views today
        self.cobuy: dict[str, dict] = {}             # X -> {Y: times someone viewed X today and later bought Y}
        self._cf = _cf_neighbors(h["purchases"]) if history else {}
        self.views_u: dict[str, dict] = {}           # shopper -> {product: times viewed today} (those in views_today)
        self._taste = _taste_svd(h["purchases"], h["carts"]) if history else None
        self._ctx_dev = _device_lift(catalog, h.get("device") or [])

    # ---- write
    def event(self, t: int, u: str, p: str, kind: int) -> None:
        if kind == VIEW:
            sub = self.catalog.get(p, {}).get("subcategory")
            if sub:
                d = self.views_sub.setdefault(u, {})
                d[sub] = d.get(sub, 0) + 1
            self.views_p[p] = self.views_p.get(p, 0.0) + 1
            self.total_views += 1
            self.views_today_p[p] = self.views_today_p.get(p, 0) + 1
            v = self.views_today.setdefault(u, [])
            if p in v:
                v.remove(p)
            v.append(p)
            n = self.views_u.setdefault(u, {})
            n[p] = n.get(p, 0) + 1
            if len(v) > 20:
                n.pop(v[0], None)
                del v[0]
        elif kind == CART:
            self.cart_today.setdefault(u, set()).add(p)
        elif kind == PURCHASE:
            self.bought.setdefault(u, set()).add(p)
            self.purchases_p[p] = self.purchases_p.get(p, 0.0) + 1
            self.total_purchases += 1
            for x in self.views_today.get(u, ()):
                if x != p:  # viewing X and buying X is already counted by `viewed_session`
                    d = self.cobuy.setdefault(x, {})
                    d[p] = d.get(p, 0) + 1
        if t > self.until_ms:
            self.until_ms = t

    def apply(self, rows: list[tuple]) -> None:
        with self._lock:
            for f in rows:
                self.event(*f)

    def catch_up(self, overlap_ms: int = 5000) -> int:
        """LIVE: fetches from shop.events what is newer than the last applied event (with a few seconds of overlap in
        case some event arrived late; repeats are dropped). One light query: call every 1-2 s outside the loop that
        answers. Returns how many new events came in."""
        since = self.until_ms - overlap_ms
        rows = _query(f"""SELECT toUnixTimestamp64Milli(ts::DateTime64(3)) AS t, user_id::String AS u,
                          product_id::String AS p, event_type::String AS e FROM shop.events
                          WHERE ts::DateTime64(3) >= fromUnixTimestamp64Milli(toInt64({since}))
                          ORDER BY ts LIMIT 200000""", timeout=10)
        new = []
        for f in rows:
            key = (int(f["t"]), f["u"], f["p"], f["e"])
            if key not in self._recent and f["e"] in _EVENT_TYPE:
                self._recent.add(key)
                new.append((key[0], sys.intern(f["u"]), sys.intern(f["p"]), _EVENT_TYPE[f["e"]]))
        self.apply(new)
        if len(self._recent) > 50000:  # only the overlap needs remembering
            self._recent = {c for c in self._recent if c[0] >= self.until_ms - 2 * overlap_ms}
        return len(new)

    @classmethod
    def up_to_date(cls, catalog: dict[str, dict]) -> "World":
        """To start the service: history + all of today's stored events + whatever is missing until now."""
        world = cls(catalog, load_history())
        events, _, _ = download_live()
        world.apply(events)
        world.catch_up()
        return world

    # ---- read
    def snapshot(self, u: str) -> dict:
        """Small copy of what is known about a shopper (to read it later without the lock)."""
        with self._lock:
            bought = self.bought.get(u) or ()
            today = self.cart_today.get(u) or ()
            old = self.cart_old.get(u) or ()
            return {"u": u, "bought": set(bought),
                    "cart_today": {p for p in today if p not in bought},
                    "cart_old": {p for p in old if p not in bought and p not in today},
                    "views_sub": dict(self.views_sub.get(u) or {}),
                    "views_today": list(self.views_today.get(u) or ()),
                    "views_n": dict(self.views_u.get(u) or {})}

    def product_numbers(self, p: str) -> tuple[float, float]:
        """(pop, rate) of a product with what has been seen so far. Both are proportions: they don't grow with time."""
        purchases = self.purchases_p.get(p, 0.0)
        views = self.views_p.get(p, 0.0)
        # with no data yet (empty World) a typical mean rate is used so that the logarithm exists
        mean = self.total_purchases / self.total_views if self.total_purchases > 0 and self.total_views > 0 else 0.05
        pop = math.log((purchases + 1.0) / (self.total_purchases + 5000.0))
        rate = math.log((purchases + PSEUDO_VIEWS * mean) / (views + PSEUDO_VIEWS))
        return pop, rate

    def cobuy_today(self, sources: list[str]) -> dict[str, float]:
        """product -> share of TODAY's views of `sources` that ended in a purchase of that product (summed)."""
        r: dict[str, float] = {}
        with self._lock:
            for x in sources:
                d = self.cobuy.get(x)
                if d:
                    k = 1.0 / (self.views_today_p.get(x, 0) + PSEUDO_COBUY)
                    for y, n in d.items():
                        r[y] = r.get(y, 0.0) + n * k
        return r

    def cf_history(self, bought) -> dict[str, float]:
        """product -> similarity (cosine) with what this shopper already bought, summed. Empty if nothing bought."""
        r: dict[str, float] = {}
        for x in bought:
            for y, v in self._cf.get(x, ()):
                r[y] = r.get(y, 0.0) + v
        return r

    def type_numbers(self, d: dict) -> tuple[float, float, float]:
        """(type_rate, type_pop, brand_rate) of a product from its catalog entry. Works the same for a new one."""
        t = self._types
        if not t:
            return 0.0, 0.0, 0.0
        sub = d.get("subcategory")
        rate, pop = t["type"].get((sub, _price_band(t["cuts"].get(sub), d.get("price_eur"))), t["mean"])
        return rate, pop, t["brand"].get(d.get("brand"), t["mean"][0])

    def taste(self, u: str | None, products: list[str]) -> np.ndarray | None:
        """How well each product fits that shopper's taste (their factors · the product's).
        None if the shopper has no carts or purchases in the history; 0 for a product that was not there."""
        g = self._taste
        if not g or u not in g["u"]:
            return None
        return g["V"][[g["p"].get(p, -1) for p in products]] @ g["U"][g["u"][u]]   # -1 = last row, all zeros

    def best_taste(self, u: str | None, n: int) -> list[str]:
        """The n history products that best fit that shopper's taste (candidates)."""
        g = self._taste
        if not g or u not in g["u"]:
            return []
        s = g["V"][:-1] @ g["U"][g["u"][u]]
        return [g["products"][j] for j in np.argpartition(-s, n)[:n]]


def _cf_neighbors(purchases: dict[str, set]) -> dict[str, list]:
    """Item-to-item collaborative filtering with the 60 days of purchases: for each product, the CF_NEIGHBORS that
    share the most buyers with it (cosine = buyers in common / root of each one's buyers).
    Computed once (~2 s) and stored in data/learner/cf.pkl: the history does not change."""
    stored = _read("cf.pkl", None)
    if stored is not None and stored.get("n") == len(purchases):
        return stored["neighbors"]
    from scipy import sparse
    products = sorted({p for s in purchases.values() for p in s})
    if not products:
        return {}
    idx = {p: i for i, p in enumerate(products)}
    rows, cols = [], []
    for k, s in enumerate(purchases.values()):
        rows += [k] * len(s)
        cols += [idx[p] for p in s]
    M = sparse.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(len(purchases), len(products)))
    C = (M.T @ M).toarray()
    n = np.sqrt(np.maximum(C.diagonal(), 1.0))
    C = C / n[:, None] / n[None, :]
    np.fill_diagonal(C, 0.0)
    neighbors = {}
    for i, p in enumerate(products):
        best = np.argpartition(-C[i], CF_NEIGHBORS)[:CF_NEIGHBORS]
        neighbors[p] = [(products[j], float(C[i, j])) for j in best if C[i, j] > 0]
    _save("cf.pkl", {"n": len(purchases), "neighbors": neighbors})
    return neighbors


def _taste_svd(purchases: dict[str, set], carts: dict[str, set]) -> dict | None:
    """Personal taste by factorisation (truncated SVD, TASTE_DIM factors) of the 60-day shopper × product table:
    1 if added to the cart, +1 if bought. Each shopper and each product are summarised in TASTE_DIM numbers; their dot
    product says how well they fit, also for products that shopper never touched.
    Computed once (~5 s) and stored in data/learner/taste.pkl: the history does not change."""
    key = (len(purchases), len(carts), TASTE_DIM)
    stored = _read("taste.pkl", None)
    if stored is not None and stored.get("key") == key:
        return stored
    from scipy import sparse
    from scipy.sparse.linalg import svds
    users = sorted(set(purchases) | set(carts))
    products = sorted({p for d in (purchases, carts) for s in d.values() for p in s})
    if min(len(users), len(products)) <= TASTE_DIM:
        return None
    ui = {u: i for i, u in enumerate(users)}
    pi = {p: i for i, p in enumerate(products)}
    rows, cols = [], []
    for d in (purchases, carts):
        for u, s in d.items():
            rows += [ui[u]] * len(s)
            cols += [pi[p] for p in s]
    M = sparse.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(len(users), len(products)))
    U, s, Vt = svds(M, k=TASTE_DIM)   # repeated pairs (cart and purchase) are summed when building the matrix
    g = {"key": key, "u": ui, "p": pi, "products": products, "U": (U * s).astype(np.float32),
         "V": np.vstack([Vt.T, np.zeros((1, TASTE_DIM))]).astype(np.float32)}
    _save("taste.pkl", g)
    return g


def _device_lift(catalog: dict, rows: list[tuple]) -> dict[str, dict]:
    """device -> subcategory -> log(share of the purchases made from that device that fall in the subcategory
    / that same share in the whole shop). 0 = like the average. Works the same for a new shopper or product."""
    by: dict = defaultdict(Counter)
    everything: Counter = Counter()
    for d, p, n in rows:
        sub = catalog.get(p, {}).get("subcategory")
        if sub:
            by[d][sub] += n
            everything[sub] += n
    total = sum(everything.values())
    if not total:
        return {}
    return {d: {s: math.log((c[s] / sum(c.values()) + 1e-4) / (everything[s] / total + 1e-4)) for s in everything}
            for d, c in by.items()}


def _related(world: World, snap: dict, viewed: list[str]) -> tuple[dict, dict]:
    """(related today, related in the history) for a shopper; stored in their snapshot to compute them only once.
    Today's sources: what was viewed in the session (without views, the last things viewed today) and today's cart."""
    if "co" not in snap:
        sources = list(dict.fromkeys(reversed(viewed)))[:TODAY_SOURCES] or snap.get("views_today", [])[-TODAY_SOURCES:]
        snap["co"] = world.cobuy_today(list(dict.fromkeys(sources + list(snap["cart_today"]))))
        snap["cf"] = world.cf_history(snap["bought"])
    return snap["co"], snap["cf"]


def _price_band(cuts: tuple | None, price) -> int:
    """Price band within its subcategory: 0 cheap, 1 middle, 2 expensive (thirds of the catalog)."""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return 1
    if not cuts:
        return 1
    return 0 if price <= cuts[0] else (1 if price <= cuts[1] else 2)


def _type_numbers(catalog: dict, products: dict) -> dict:
    """With the history's views and purchases per product (shop.history_events, 60 days), groups by TYPE
    (subcategory × price band) and by brand. Computed once: the history does not change."""
    if not products:
        return {}
    prices: dict[str, list] = defaultdict(list)
    for d in catalog.values():
        if d.get("subcategory") and d.get("price_eur") is not None:
            prices[d["subcategory"]].append(float(d["price_eur"]))
    cuts = {s: (float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))) for s, v in prices.items()}
    kind: dict = defaultdict(lambda: [0.0, 0.0, 0])   # views, purchases, number of products
    brand: dict = defaultdict(lambda: [0.0, 0.0])
    tv = tc = 0.0
    for p, (v, c) in products.items():
        d = catalog.get(p)
        if not d:
            continue
        a = kind[(d.get("subcategory"), _price_band(cuts.get(d.get("subcategory")), d.get("price_eur")))]
        a[0] += v; a[1] += c; a[2] += 1
        b = brand[d.get("brand")]
        b[0] += v; b[1] += c
        tv += v; tc += c
    if not tv or not tc:
        return {}
    mean = tc / tv

    def rate(v: float, c: float) -> float:
        return math.log((c + PSEUDO_TYPE * mean) / (v + PSEUDO_TYPE))

    def pop(c: float, n: int) -> float:  # same scale as `pop`: what an average product of that type would sell
        return math.log((HISTORY_WEIGHT * c / max(n, 1) + 1.0) / (HISTORY_WEIGHT * tc + 5000.0))

    return {"cuts": cuts, "type": {k: (rate(v, c), pop(c, n)) for k, (v, c, n) in kind.items()},
            "brand": {k: rate(v, c) for k, (v, c) in brand.items()}, "mean": (math.log(mean), pop(tc, len(products)))}


def signals(world: World, snap: dict, viewed: list[str], new_user: bool, candidates: list[str],
            with_h: bool = True, dev: str = "", with_x: bool = True) -> np.ndarray:
    """Matrix (len(candidates), len(SIGNALS)). The same function for training and for recommending.
    with_h=False / with_x=False skip the signals of groups "h" / "x" (for models that don't use them: they stay
    at 0). `dev` = context.device of the request (only `ctx_dev_sub` uses it)."""
    co, cf = _related(world, snap, viewed) if with_h or with_x else ({}, {})
    cat = world.catalog
    last = viewed[-1] if viewed else None
    d_last = cat.get(last, {}) if last else {}
    in_session = set(viewed)
    total = sum(snap["views_sub"].values())
    by_cat: dict[str, int] = {}
    if total:
        sub_to_cat = _subcategory_to_category(cat)
        for sub, n in snap["views_sub"].items():
            c = sub_to_cat.get(sub)
            if c:
                by_cat[c] = by_cat.get(c, 0) + n
    X = np.zeros((len(candidates), len(SIGNALS)))
    for i, p in enumerate(candidates):
        d = cat.get(p, {})
        bought = p in snap["bought"]
        pop, rate = world.product_numbers(p)
        theirs = p in snap["cart_today"] or p in snap["cart_old"] or p in in_session
        X[i, :_N_BASE] = (p in snap["cart_today"],
                p in snap["cart_old"],
                p in in_session and not bought,
                bought,
                math.sqrt(by_cat.get(d.get("category"), 0) / total) if total else 0.0,
                math.sqrt(snap["views_sub"].get(d.get("subcategory"), 0) / total) if total else 0.0,
                bool(last) and d.get("category") == d_last.get("category"),
                pop, rate,
                not viewed, new_user,
                bool(last) and not theirs and not bought and d.get("subcategory") == d_last.get("subcategory"),
                *world.type_numbers(d))
        if co or cf:
            X[i, _I_CO] = math.sqrt(co.get(p, 0.0))
            X[i, _I_CF] = math.sqrt(cf.get(p, 0.0))
    if new_user:
        X[:, _I_NEW] = X[:, _I_NEW_BASE]
    if with_x:
        taste = world.taste(snap.get("u"), candidates)
        if taste is not None:
            X[:, _I_TASTE] = taste
        X[:, _I_SHAPE] = np.column_stack([X[:, _I_CO] > 0, X[:, _I_CF] > 0, np.sqrt(X[:, _I_CO]), np.sqrt(X[:, _I_CF])])
        lift = world._ctx_dev.get(dev) or {}
        median = _subcategory_median(cat)
        session_cats = Counter(cat.get(p, {}).get("category") for p in viewed)
        views_n = snap.get("views_n") or {}
        known = world.known
        for i, p in enumerate(candidates):
            d = cat.get(p, {})
            sub, price = d.get("subcategory"), d.get("price_eur")
            X[i, _I_X5] = (lift.get(sub, 0.0),
                           session_cats.get(d.get("category"), 0) / len(viewed) if viewed else 0.0,
                           bool(known) and p not in known,
                           math.log(float(price) / median[sub]) if price and median.get(sub) else 0.0,
                           math.log1p(views_n.get(p, 0)))
    return X


_SUB_CAT: dict = {}
_MEDIAN: dict = {}


def _subcategory_median(cat: dict) -> dict[str, float]:
    """subcategory -> median price of its products. Recomputed if the catalog changes (new prices)."""
    key = (id(cat), len(cat))
    if _MEDIAN.get("key") != key:
        prices: dict[str, list] = defaultdict(list)
        for d in cat.values():
            if d.get("subcategory") and d.get("price_eur"):
                prices[d["subcategory"]].append(float(d["price_eur"]))
        _MEDIAN["map"] = {s: float(np.median(v)) for s, v in prices.items()}
        _MEDIAN["key"] = key
    return _MEDIAN["map"]


def _subcategory_to_category(cat: dict) -> dict[str, str]:
    """subcategory -> category (each subcategory belongs to one only). Recomputed if the catalog changes."""
    key = (id(cat), len(cat))
    if _SUB_CAT.get("key") != key:
        _SUB_CAT.clear()
        _SUB_CAT["key"] = key
        _SUB_CAT["map"] = {d.get("subcategory"): d.get("category") for d in cat.values() if d.get("subcategory")}
    return _SUB_CAT["map"]


# ---------------------------------------------------------------- the model
class Model:
    """Weights of the logistic regression + the "best-of" lists to draw candidates from."""

    def __init__(self, weights: dict[str, float], intercept: float, positions: list[float], info: dict | None = None):
        self.weights = weights                    # only the signals this model was trained with
        self.w = np.array([weights.get(n, 0.0) for n in NAMES])   # the missing ones weigh 0
        self.intercept = intercept
        self.positions = np.array(positions)      # 10 values; position 1 is 0
        self.info = info or {}
        self._lists: dict = {}
        self._lists_t = 0.0

    @staticmethod
    def file(name: str | None = None) -> Path:
        """data/learner.json, or data/learner_<name>.json to keep several trainings at once (A/B)."""
        return MODEL_FILE if not name else MODEL_FILE.with_name(f"learner_{name}.json")

    @classmethod
    def load(cls, name: str | None = None) -> "Model | None":
        f = cls.file(name)
        if f.exists():
            j = json.loads(f.read_text(encoding="utf-8"))
        else:
            legacy = MODEL_FILE.with_name(f"aprendiz_{name}.json" if name else "aprendiz.json")
            if not legacy.exists():
                return None
            j = _from_legacy(json.loads(legacy.read_text(encoding="utf-8")))
        if not set(j["weights"]) <= set(NAMES):  # has signals this code does not know: better not use it
            return None
        return cls(j["weights"], j["intercept"], j["positions"], j.get("info"))

    def save(self, name: str | None = None) -> None:
        f = self.file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"weights": self.weights, "intercept": self.intercept,
                                 "positions": [float(x) for x in self.positions], "info": self.info},
                                indent=1, ensure_ascii=False), encoding="utf-8")

    def lists(self, world: World, force: bool = False) -> dict:
        """Best products (by the product-only signals) of the shop, per category and per subcategory."""
        if force or not self._lists or time.monotonic() - self._lists_t > LISTS_TTL_S:
            # the choice model brings separately the weights the lists are built with (see train_choice)
            weights = self.info.get("list_weights") or self.weights
            w = [weights.get(n, 0.0) for n in PRODUCT_SIGNALS]
            with_type = any(w[2:])
            points = []
            for p, d in list(world.catalog.items()):
                pop, rate = world.product_numbers(p)
                value = w[0] * pop + w[1] * rate
                if with_type:
                    value += sum(a * b for a, b in zip(w[2:], world.type_numbers(d)))
                points.append((value, p, d.get("category"), d.get("subcategory")))
            points.sort(reverse=True)
            by_cat: dict[str, list] = defaultdict(list)
            by_sub: dict[str, list] = defaultdict(list)
            for _, p, c, s in points:
                by_cat[c].append(p)
                by_sub[s].append(p)
            everything = [p for _, p, _, _ in points]
            # For exploring: new products (no activity in the history) and those left out of the best-of lists.
            # Without a loaded history it is unknown what is new: no exploring with new products.
            known = world.known
            self._lists = {"all": everything, "cat": dict(by_cat), "sub": dict(by_sub),
                           "sub_to_cat": _subcategory_to_category(world.catalog),
                           "new": {c: [p for p in l if p not in known] for c, l in by_cat.items()} if known else {},
                           "new_all": [p for p in everything if p not in known] if known else [],
                           "rest": {c: l[N_CATEGORY:] for c, l in by_cat.items()},
                           "rest_all": everything[N_GLOBAL:]}
            self._lists_t = time.monotonic()
        return self._lists

    def value(self, X: np.ndarray) -> float:
        """Expected purchases of an already ordered list (sum of each slot's probability, with its position)."""
        z = self.intercept + X @ self.w + self.positions[:len(X)]
        return float((1 / (1 + np.exp(-z))).sum())


def _from_legacy(j: dict) -> dict:
    """A model file saved during the event (Spanish keys) -> the format of this file."""
    def names(d: dict) -> dict:
        return {LEGACY_SIGNALS.get(n, n): v for n, v in d.items()}
    info = dict(j.get("info") or {})
    if "pesos_listas" in info:
        info["list_weights"] = names(info.pop("pesos_listas"))
    if "max_nuevos" in info:
        info["max_new"] = info.pop("max_nuevos")
    return {"weights": names(j["pesos"]), "intercept": j["constante"], "positions": j["puestos"], "info": info}


def _parse_request(request: dict) -> tuple[str, list[str], bool]:
    """(user_id, viewed products from oldest to newest, new shopper?)."""
    new_user = request.get("is_new_user")
    return (str(request.get("user_id") or ""), _viewed_products(request.get("recent_views")),
            new_user is True or new_user == 1 or str(new_user).lower() == "true")


def _device(request: dict) -> str:
    """context.device of the request, whether `context` comes as an object, as JSON text or already flattened."""
    c = request.get("context")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            c = None
    if isinstance(c, dict) and c.get("device"):
        return str(c["device"])
    return str(request.get("context.device") or "")


def candidates(world: World, model: Model, snap: dict, viewed: list[str], ok) -> list[str]:
    lists = model.lists(world)
    cat = world.catalog
    cand = list(reversed(viewed)) + list(snap["cart_today"]) + list(snap["cart_old"])
    if viewed:
        d = cat.get(viewed[-1], {})
        cand += lists["cat"].get(d.get("category"), [])[:N_CATEGORY]
        cand += lists["sub"].get(d.get("subcategory"), [])[:N_SUBCATEGORY]
    subs = sorted(snap["views_sub"].items(), key=lambda kv: -kv[1])
    by_cat: Counter = Counter()
    for sub, n in subs:
        by_cat[lists["sub_to_cat"].get(sub)] += n
    for c, _ in by_cat.most_common(TOP_CATEGORIES):
        cand += lists["cat"].get(c, [])[:N_CATEGORY]
    for sub, _ in subs[:TOP_SUBCATEGORIES]:
        cand += lists["sub"].get(sub, [])[:N_SUBCATEGORY]
    if model.w[_I_CO] or model.w[_I_CF]:  # hybrid models only: the others give the same lists as before
        co, cf = _related(world, snap, viewed)
        cand += sorted(co, key=co.get, reverse=True)[:N_COBUY] + sorted(cf, key=cf.get, reverse=True)[:N_CF]
    if model.w[_I_TASTE]:  # only models that use personal taste
        cand += world.best_taste(snap.get("u"), N_TASTE)
    cand += lists["all"][:N_GLOBAL]
    return [p for p in dict.fromkeys(cand) if ok(p)]


def should_explore(request_id: str) -> bool:
    """Is this one of the exploring requests? Depends only on request_id: same answer live and when analysing."""
    return bool(request_id) and zlib.crc32(str(request_id).encode()) % 1000 < EXPLORE_FRACTION * 1000


def _random_pick(source: list[str] | None, rng: random.Random, ok) -> str | None:
    """A valid product from the list, picked at random (all with the same probability). None if none found."""
    if not source:
        return None
    for _ in range(EXPLORE_TRIES):
        p = source[rng.randrange(len(source))]
        if ok(p):
            return p
    return None


def _explore(request_id: str, world: World, model: Model, snap: dict, viewed: list[str], chosen: list[str],
             ok) -> list[str]:
    """Replaces slots 9 and 10 with a new product and one from outside the best-of lists. Both come from the
    category the shopper cares about (that of the last viewed product; without views, the one they view most; if
    nothing is known, from the whole shop). The first 8 slots are not touched."""
    lists = model.lists(world)
    category = world.catalog.get(viewed[-1], {}).get("category") if viewed else None
    if not category and snap["views_sub"]:
        by_cat: Counter = Counter()
        for sub, n in snap["views_sub"].items():
            by_cat[lists["sub_to_cat"].get(sub)] += n
        category = by_cat.most_common(1)[0][0]
    rng = random.Random(zlib.crc32(str(request_id).encode()))  # same request -> same answer
    out = list(chosen)
    for slot, from_category, from_shop in ((SLOT_NEW, lists["new"].get(category), lists["new_all"]),
                                           (SLOT_REST, lists["rest"].get(category), lists["rest_all"])):
        for source in (from_category, from_shop):
            p = _random_pick(source, rng, lambda p: ok(p) and p not in out)
            if p:
                out[slot] = p
                break
    return out


def recommend(request: dict, world: World, model: Model, available: set[str], explore: bool = False,
              force: dict[str, float] | None = None) -> list[str]:
    """10 distinct product_ids, available and never already bought by that shopper, from best to worst.
    With explore=True, in half of the requests slots 9 and 10 are exploration (see `_explore`).
    `force` = {signal: multiplier} replaces the learned weight of those signals in THIS call, to try in an A/B a
    stronger or weaker signal than the feedback says without retraining. Example: {"same_sub": 3.0}."""
    u, viewed, new_user = _parse_request(request)
    snap = world.snapshot(u)
    bought = snap["bought"]

    def ok(p: str) -> bool:
        return p in available and p not in bought

    cand = candidates(world, model, snap, viewed, ok)
    chosen: list[str] = []
    w = model.w
    if force:
        w = w.copy()
        for name, multiplier in force.items():
            w[_I[name]] = math.log(multiplier)
    if cand:
        s = signals(world, snap, viewed, new_user, cand, with_h=bool(w[_I_CO] or w[_I_CF]), dev=_device(request),
                    with_x=bool(w[_I_X].any())) @ w
        order = np.argsort(-s, kind="stable")
        cap = model.info.get("max_new")
        if cap is None or not world.known:
            chosen = [cand[i] for i in order[:10]]
        else:  # choice model: at most `cap` NEW products that are not the shopper's (see MAX_NEW)
            theirs, new = snap["cart_today"] | snap["cart_old"] | set(viewed), 0
            for i in order:
                if cand[i] not in world.known and cand[i] not in theirs:
                    if new >= cap:
                        continue
                    new += 1
                chosen.append(cand[i])
                if len(chosen) == 10:
                    break
    if len(chosen) < 10:  # almost never: filled with the shop's best and, as a last resort, with anything
        have = set(chosen)
        for source, require in ((model.lists(world)["all"], ok), (sorted(available), ok),
                                (sorted(available), available.__contains__)):
            for p in source:
                if len(chosen) == 10:
                    break
                if p not in have and require(p):
                    chosen.append(p)
                    have.add(p)
    if explore and len(chosen) == 10 and should_explore(request.get("request_id") or ""):
        chosen = _explore(request["request_id"], world, model, snap, viewed, chosen, ok)
    return chosen


# ---------------------------------------------------------------- training
def replay(world: World, events: list[tuple], requests: list[tuple[int, str]], on_request) -> None:
    """Replays the day in order: before each request (t, request_id) ONLY the events before it (minus DELAY_MS)
    are applied, and then on_request(request_id) is called. No signal looks into the future."""
    i, n = 0, len(events)
    for t, rid in requests:
        while i < n and events[i][0] < t - DELAY_MS:
            world.event(*events[i])
            i += 1
        on_request(rid)


def training_matrix(catalog: dict, history: dict, events: list, requests: dict, feedback: dict) -> dict:
    """One row per shown product: signals + 9 position columns. Returns X, purchase, click, t and the ids."""
    order = sorted((requests[rid][0], rid) for rid in feedback if rid in requests)
    world = World(catalog, history)
    Xs, ys, cs = [], [], []

    def on_request(rid: str) -> None:
        _, u, new_user, dev, viewed = requests[rid]
        shown, click, purchase, _ = feedback[rid]
        Xs.append(signals(world, world.snapshot(u), list(viewed), new_user, list(shown), dev=dev))
        ys.append([p == purchase for p in shown])
        cs.append([p == click for p in shown])

    replay(world, events, order, on_request)
    positions = np.tile(np.eye(10)[:, 1:], (len(order), 1))
    return {"X": np.hstack([np.vstack(Xs), positions]), "purchase": np.array(ys, dtype=float).ravel(),
            "click": np.array(cs, dtype=float).ravel(), "t": np.array([t for t, _ in order]),
            "rids": [rid for _, rid in order]}


def only_users(data: dict, new_users: bool) -> dict:
    """The responses of a single shopper type (new or known): to train a specialist."""
    f = (data["X"][:, _I["new_user"]] > 0) == new_users
    r = f.reshape(-1, 10)[:, 0]
    return {"X": data["X"][f], "purchase": data["purchase"][f], "click": data["click"][f], "t": data["t"][r],
            "rids": [x for x, keep in zip(data["rids"], r) if keep]}


def _loss(y: np.ndarray, z: np.ndarray) -> float:
    return float(-(y * z - np.logaddexp(0, z)).sum())


def fit(X: np.ndarray, y: np.ndarray, l2: float = L2) -> np.ndarray:
    """Logistic regression with numpy (Newton's method with step control). Returns [intercept, weights...].
    Columns are put on the same scale to fit and the weights are returned on the original scale."""
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-12] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    reg = l2 * np.r_[0.0, np.ones(Z.shape[1] - 1)]
    w = np.zeros(Z.shape[1])
    w[0] = math.log(max(y.mean(), 1e-9) / max(1 - y.mean(), 1e-9))
    f = _loss(y, Z @ w) + 0.5 * float(reg @ (w * w))
    for _ in range(100):
        p = 1 / (1 + np.exp(-(Z @ w)))
        g = Z.T @ (p - y) + reg * w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        scale = 1.0
        while True:  # if the full step makes it worse, try half of it
            w2 = w - scale * step
            f2 = _loss(y, Z @ w2) + 0.5 * float(reg @ (w2 * w2))
            if f2 <= f or scale < 1e-4:
                break
            scale /= 2
        gain = f - f2
        w, f = w2, f2
        if gain < 1e-7 * max(1.0, abs(f)):
            break
    return np.r_[w[0] - float((w[1:] * mu / sd).sum()), w[1:] / sd]


def _logloss(y: np.ndarray, z: np.ndarray) -> float:
    return _loss(y, z) / len(y)


def train(data: dict, click_weight: float, val_fraction: float = 0.3, verbose: bool = True,
          without: tuple[str, ...] = ()) -> tuple[Model, dict]:
    """Trains and validates BY TIME: learns from the start of the day and checks against the end.
    The returned model is trained with EVERYTHING (validation only says how much to trust it).
    `without` = signals this training does NOT use (for a clean A/B: same moment, same data, one signal less)."""
    X, purchase, click = data["X"], data["purchase"], data["click"]
    k = len(SIGNALS)
    used = [n for n in NAMES if n not in without]
    if without:  # zero column = the signal does not exist for this model (its weight is 0 and is not saved)
        X = X.copy()
        X[:, [_I[n] for n in without if n in _I]] = 0.0
    t_row = np.repeat(data["t"], 10)
    cutoff = float(np.quantile(data["t"], 1 - val_fraction))
    trn, val = t_row < cutoff, t_row >= cutoff
    target = np.maximum(purchase, click_weight * click)

    def measure(w: np.ndarray) -> float:
        return _logloss(purchase[val], w[0] + X[val] @ w[1:])

    w_pos = fit(X[trn][:, k:], purchase[trn])
    base = _logloss(purchase[val], w_pos[0] + X[val][:, k:] @ w_pos[1:])
    w_val = fit(X[trn], target[trn])
    if click_weight > 0:  # the mixed target raises the mean: recalibrate the intercept with purchases
        w_val[0] += math.log(purchase[trn].mean() / target[trn].mean())
    info = {"responses": int(len(data["t"])), "purchases": int(purchase.sum()), "clicks": int(click.sum()),
            "val_responses": int(val.sum() // 10), "val_purchases": int(purchase[val].sum()),
            "logloss_position_only": base, "logloss_model": measure(w_val), "click_weight": click_weight}
    info["improvement_pct"] = 100 * (base - info["logloss_model"]) / base

    # Learning curve: does it improve with more data? Trained with the last 25/50/75/100 % before the cutoff.
    t_trn = data["t"][data["t"] < cutoff]
    curve = []
    for part in (0.25, 0.5, 0.75, 1.0):
        since = float(np.quantile(t_trn, 1 - part))
        f = trn & (t_row >= since)
        w = fit(X[f], target[f])
        if click_weight > 0:
            w[0] += math.log(max(purchase[f].mean(), 1e-9) / target[f].mean())
        curve.append((int(f.sum() // 10), int(purchase[f].sum()), 100 * (base - measure(w)) / base))
    info["curve"] = curve

    # In the validation responses with a purchase: does the model (ignoring position) put the bought one on top?
    z = (X[val][:, :k] @ w_val[1:k + 1]).reshape(-1, 10)
    yv = purchase[val].reshape(-1, 10)
    has = yv.sum(1) > 0
    real_pos = yv[has].argmax(1)
    rank = (z[has] > z[has][np.arange(has.sum()), real_pos][:, None]).sum(1)
    info["hit_1"] = float((rank == 0).mean())
    info["hit_3"] = float((rank < 3).mean())
    info["shown_1"] = float((real_pos == 0).mean())
    info["shown_3"] = float((real_pos < 3).mean())

    # Calibration: lists sorted by what the model predicts, in 5 groups: predicted vs real.
    p_list = (1 / (1 + np.exp(-(w_val[0] + X[val] @ w_val[1:])))).reshape(-1, 10).sum(1)
    real = yv.sum(1)
    groups = np.array_split(np.argsort(p_list), 5)
    info["calibration"] = [(100 * float(p_list[g].mean()), 100 * float(real[g].mean())) for g in groups]

    w = fit(X, target)
    if click_weight > 0:
        w[0] += math.log(purchase.mean() / target.mean())
    model = Model({n: float(w[1 + _I[n]]) for n in used}, float(w[0]), [0.0] + [float(v) for v in w[k + 1:]], info)
    model_val = Model({n: float(w_val[1 + _I[n]]) for n in used}, float(w_val[0]),
                      [0.0] + [float(v) for v in w_val[k + 1:]], {"cutoff_ms": cutoff})
    if verbose:
        _report(model, info)
    return model, {"model_val": model_val, "cutoff_ms": cutoff}


def fit_choice(X: np.ndarray, chosen: np.ndarray, weight: np.ndarray, l2: float = CHOICE_L2) -> np.ndarray:
    """Conditional logit (numpy + L-BFGS). X are lists of 10 consecutive rows; `chosen` marks ONE row per list and
    `weight` says how much each list counts. Returns one weight per column, without intercept: whatever is equal for
    the 10 products cancels out. Columns are put on the same scale to fit and returned on the original one."""
    from scipy.optimize import minimize
    k = X.shape[1]
    sd = X.std(0)
    sd[sd < 1e-12] = 1.0
    Z = (X / sd).reshape(-1, 10, k)
    Y = chosen.reshape(-1, 10)

    def f(w: np.ndarray) -> tuple[float, np.ndarray]:
        z = Z @ w
        z -= z.max(1, keepdims=True)
        pr = np.exp(z)
        pr /= pr.sum(1, keepdims=True)
        loss = -(weight * np.log((pr * Y).sum(1))).sum() + 0.5 * l2 * float(w @ w)
        return float(loss), np.einsum("lik,li->k", Z, (pr - Y) * weight[:, None]) + l2 * w

    return minimize(f, np.zeros(k), jac=True, method="L-BFGS-B", options={"maxiter": 500}).x / sd


def _choice_loss(z: np.ndarray, chosen: np.ndarray) -> np.ndarray:
    """Per list: -log P(the chosen one | one of the 10 was chosen). Random gives log(10) = 2.30; lower = orders better.
    Unlike the per-product logloss, it does not reward guessing which REQUESTS buy: it only looks at the order."""
    z = z.reshape(-1, 10)
    z = z - z.max(1, keepdims=True)
    return -np.log((np.exp(z) * chosen.reshape(-1, 10)).sum(1) / np.exp(z).sum(1))


def train_choice(data: dict, click_weight: float = CHOICE_CLICK_WEIGHT, val_fraction: float = 0.3,
                 verbose: bool = True, without: tuple[str, ...] = ()) -> tuple[Model, dict]:
    """AP8: learns "which of the 10 shown was chosen" from the lists with a purchase (weight 1) or a click without
    purchase (click_weight). Validates BY TIME like `train` and compares, on the same lists, with the per-product
    model (with the same signals and with AP7's). The returned model is trained with everything."""
    k = len(SIGNALS)
    out = set(without) | {"no_views", "new_user"}     # equal for the 10 products of a list: they cannot order
    used = [n for n in NAMES if n not in out]
    X = data["X"][:, [_I[n] for n in used] + list(range(k, k + 9))]
    purchase, click = data["purchase"].reshape(-1, 10), data["click"].reshape(-1, 10)
    cutoff = float(np.quantile(data["t"], 1 - val_fraction))
    before = data["t"] < cutoff
    with_purchase, with_click = purchase.sum(1) > 0, click.sum(1) > 0

    def rows(lists: np.ndarray) -> np.ndarray:
        return (np.flatnonzero(lists)[:, None] * 10 + np.arange(10)).ravel()

    def fit_with(mask: np.ndarray, columns=slice(None)) -> np.ndarray:
        parts = [(with_purchase & mask, purchase, 1.0)]
        if click_weight > 0:
            parts.append((with_click & ~with_purchase & mask, click, click_weight))
        return fit_choice(np.vstack([X[rows(f)][:, columns] for f, _, _ in parts]),
                          np.concatenate([y[f].ravel() for f, y, _ in parts]),
                          np.concatenate([np.full(int(f.sum()), p) for f, _, p in parts]))

    val_p, val_c = with_purchase & ~before, with_click & ~before
    fp, fc = rows(val_p), rows(val_c)

    def measure(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return _choice_loss(z[fp], purchase[val_p]), _choice_loss(z[fc], click[val_c])

    rng = np.random.default_rng(0)

    def difference(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
        d = a - b
        means = d[rng.integers(0, len(d), (400, len(d)))].mean(1)
        return float(d.mean()), float(np.quantile(means, .025)), float(np.quantile(means, .975))

    w_val = fit_with(before)
    z_val = X @ w_val
    c_purchase, c_click = measure(z_val)
    w_pos = fit_with(before, slice(len(used), None))
    info = {"kind": "choice", "max_new": MAX_NEW, "responses": int(len(data["t"])), "purchases": int(with_purchase.sum()),
            "clicks": int(with_click.sum()), "click_weight": click_weight, "l2": CHOICE_L2,
            "val_lists": (int(val_p.sum()), int(val_c.sum())),
            "choice": (float(c_purchase.mean()), float(c_click.mean())),
            "choice_position_only": tuple(float(e.mean()) for e in measure(X[:, len(used):] @ w_pos)), "vs": {}}

    # The same data with the usual model ("is this product bought, yes or no?"): with these signals and with AP7's
    trn = np.repeat(before, 10)
    for label, names in (("per-product, same signals", used + ["no_views", "new_user"]),
                         ("per-product, AP7 signals", NAMES[:12])):
        Xp = data["X"][:, [_I[n] for n in names] + list(range(k, k + 9))]
        wp = fit(Xp[trn], data["purchase"][trn])
        r_purchase, r_click = measure(Xp @ wp[1:])
        info["vs"][label] = {"choice": (float(r_purchase.mean()), float(r_click.mean())),
                             "purchase": difference(c_purchase, r_purchase), "click": difference(c_click, r_click)}
        if label.endswith("same signals"):  # the best-of lists (candidates) are still built with these weights:
            # within a list popularity barely orders, but we have only shown popular products
            info["list_weights"] = {n: float(wp[1 + names.index(n)]) for n in PRODUCT_SIGNALS if n in names}
        del Xp

    s = (z_val - X[:, len(used):] @ w_val[len(used):])[fp].reshape(-1, 10)   # without the position
    real_pos = purchase[val_p].argmax(1)
    rank = (s > s[np.arange(len(s)), real_pos][:, None]).sum(1)
    info["hit_1"], info["hit_3"] = float((rank == 0).mean()), float((rank < 3).mean())
    info["shown_1"], info["shown_3"] = float((real_pos == 0).mean()), float((real_pos < 3).mean())

    def model_from(w: np.ndarray, extra: dict) -> Model:
        # the intercept is only used by `value()`: chosen so that the mean prediction is the real purchase rate
        z, a, b = X @ w, -30.0, 5.0
        for _ in range(50):
            c = (a + b) / 2
            a, b = (c, b) if (1 / (1 + np.exp(-(c + z)))).mean() < data["purchase"].mean() else (a, c)
        return Model({n: float(v) for n, v in zip(used, w)}, (a + b) / 2, [0.0] + [float(v) for v in w[len(used):]], extra)

    model = model_from(fit_with(np.ones(len(before), dtype=bool)), info)
    model_val = model_from(w_val, {"cutoff_ms": cutoff, "list_weights": info["list_weights"], "max_new": MAX_NEW})
    if verbose:
        _report_choice(model, info)
    return model, {"model_val": model_val, "cutoff_ms": cutoff}


def _report_choice(model: Model, info: dict) -> None:
    print(f"\nTRAINED (choice) with {info['purchases']} lists with a purchase and {info['clicks']} with a click, out of "
          f"{info['responses']} of our responses; a list with a click and no purchase counts {info['click_weight']}")
    print("Each signal: how much it multiplies the probability of being the chosen one in the list (>1 up, <1 down)")
    for n, text in SIGNALS:
        if n in model.weights:
            print(f"  {n:<18} ×{math.exp(model.weights[n]):7.2f}   {text}")
    print("  positions 2..10    " + " ".join(f"×{math.exp(v):.2f}" for v in model.positions[1:]))
    np_, nc = info["val_lists"]
    print(f"\nVALIDATION by time (last 30 %: {np_} lists with a purchase, {nc} with a click; model trained only on earlier data)")
    print("  choice measure = -log P(the chosen one | the list); random 2.303; lower = orders better")
    print(f"    position only                     purchase {info['choice_position_only'][0]:.4f} · click {info['choice_position_only'][1]:.4f}")
    for label, r in info["vs"].items():
        print(f"    {label:<33} purchase {r['choice'][0]:.4f} · click {r['choice'][1]:.4f}")
    print(f"    THIS MODEL (choice)               purchase {info['choice'][0]:.4f} · click {info['choice'][1]:.4f}")
    for label, r in info["vs"].items():
        print(f"    difference vs «{label}» (95 % CI, negative = better): purchase {r['purchase'][0]:+.4f} "
              f"[{r['purchase'][1]:+.4f}, {r['purchase'][2]:+.4f}] · click {r['click'][0]:+.4f} [{r['click'][1]:+.4f}, {r['click'][2]:+.4f}]")
    print(f"  lists with a purchase: the model puts the bought one 1st in {100 * info['hit_1']:.1f} % (we put it "
          f"1st in {100 * info['shown_1']:.1f} %); in the top 3 {100 * info['hit_3']:.1f} % "
          f"(we did {100 * info['shown_3']:.1f} %)")


def _report(model: Model, info: dict) -> None:
    print(f"\nTRAINED with {info['responses']} of our responses ({info['purchases']} purchases, {info['clicks']} clicks); "
          f"weight of a click = {info['click_weight']}")
    print("Each signal: how much it multiplies the relative purchase probability (>1 up, <1 down)")
    for n, text in SIGNALS:
        if n in model.weights:
            print(f"  {n:<18} ×{math.exp(model.weights[n]):7.2f}   {text}")
        else:
            print(f"  {n:<18}  (not used in this training)")
    print("  positions 2..10    " + " ".join(f"×{math.exp(v):.2f}" for v in model.positions[1:]))
    print(f"\nVALIDATION by time (last 30 %: {info['val_responses']} responses, "
          f"{info['val_purchases']} purchases; model trained only on earlier data)")
    print(f"  error (logloss) with position only {info['logloss_position_only']:.5f} · with the model "
          f"{info['logloss_model']:.5f}  →  {info['improvement_pct']:+.1f} % better")
    print("  does it improve with more data? (training with the most recent part before the cutoff)")
    for n, c, m in info["curve"]:
        print(f"    {n:>6} responses, {c:>4} purchases → {m:+.1f} % better than position only")
    print(f"  responses with a purchase: the model puts the bought one 1st in {100 * info['hit_1']:.1f} % "
          f"(we put it 1st in {100 * info['shown_1']:.1f} %); in the top 3 "
          f"{100 * info['hit_3']:.1f} % (we did {100 * info['shown_3']:.1f} %)")
    print("  calibration (lists from least to most promising): purchases/100 predicted → real")
    print("    " + "  ".join(f"{a:.2f}→{b:.2f}" for a, b in info["calibration"]))


def dry_run(catalog: dict, history: dict, events: list, requests: dict, feedback: dict, model: Model,
            cutoff_ms: float, n: int, seed: int = 0) -> dict:
    """Full rehearsal SENDING NOTHING: replays the day and, on n real requests after the cutoff, calls recommend()
    with what was known at that instant. Checks that answers are valid, how long they take and how much the list
    WOULD be worth according to the model vs the one we really showed. NOTE: the value is the model's estimate
    (trained only on data before the cutoff), not a measurement: the measurement is a live A/B."""
    from src import catalog as catalog_mod
    from src.validate import response_errors
    available = catalog_mod.available(catalog)
    order = sorted((requests[rid][0], rid) for rid in feedback if rid in requests)
    after = [rid for t, rid in order if t >= cutoff_ms]
    rng = np.random.default_rng(seed)
    picked = set(rng.choice(after, size=min(n, len(after)), replace=False))
    world = World(catalog, history)
    rows = []

    def on_request(rid: str) -> None:
        if rid not in picked:
            return
        t, u, new_user, dev, viewed = requests[rid]
        shown, _, purchase, _ = feedback[rid]
        request = {"request_id": rid, "user_id": u, "is_new_user": new_user, "context": {"device": dev},
                   "recent_views": [{"product_id": p} for p in viewed]}
        t0 = time.perf_counter()
        items = recommend(request, world, model, available)
        ms = (time.perf_counter() - t0) * 1000
        snap = world.snapshot(u)
        with_exp = recommend(request, world, model, available, explore=True)
        rows.append({
            "explores": with_exp != items,
            "exp_invalid": bool(response_errors(with_exp, available)) or any(p in snap["bought"] for p in with_exp),
            "exp_same_8": with_exp[:8] == items[:8],
            "exp_new": with_exp[SLOT_NEW] not in world.known,
            "exp_rest": with_exp[SLOT_REST] != items[SLOT_REST],
            "value_exp": model.value(signals(world, snap, list(viewed), new_user, with_exp, dev=dev)),
            "new_user": new_user, "no_views": not viewed, "ms": ms,
            "invalid": bool(response_errors(items, available)),
            "with_bought": any(p in snap["bought"] for p in items),
            "value_ours": model.value(signals(world, snap, list(viewed), new_user, list(shown), dev=dev)),
            "value_new": model.value(signals(world, snap, list(viewed), new_user, items, dev=dev)),
            "real_purchase": bool(purchase),
            "same": len(set(items) & set(shown)),
            "carts": sum(p in snap["cart_today"] or p in snap["cart_old"] for p in items),
            "first_cart": items[0] in snap["cart_today"] or items[0] in snap["cart_old"],
            "first_viewed": items[0] in viewed,
        })

    model.lists(world, force=True)
    replay(world, events, order, on_request)

    def mean(key: str, f=lambda x: True) -> float:
        v = [x[key] for x in rows if f(x)]
        return float(np.mean(v)) if v else float("nan")

    ms = sorted(x["ms"] for x in rows)
    r = {"n": len(rows), "invalid": sum(x["invalid"] for x in rows),
         "with_bought": sum(x["with_bought"] for x in rows),
         "ms_p50": ms[len(ms) // 2], "ms_p95": ms[int(len(ms) * .95)], "ms_max": ms[-1]}
    print(f"\nDRY RUN on {r['n']} real requests after the cutoff (nothing sent)")
    print(f"  invalid: {r['invalid']} · with something already bought: {r['with_bought']} · "
          f"time p50 {r['ms_p50']:.2f} ms, p95 {r['ms_p95']:.2f} ms, max {r['ms_max']:.2f} ms")
    print(f"  the new list shares on average {mean('same'):.1f} of 10 products with the one we showed; "
          f"it has {mean('carts'):.1f} products from their carts; the 1st is from a cart in "
          f"{100 * mean('first_cart'):.0f} % and something viewed in the session in {100 * mean('first_viewed'):.0f} %")
    print("  purchases/100 requests ACCORDING TO THE MODEL (estimate, not a measurement):")
    for label, f in (("all", lambda x: True), ("new shopper", lambda x: x["new_user"]),
                     ("known shopper", lambda x: not x["new_user"]), ("no views (home)", lambda x: x["no_views"])):
        k = sum(1 for x in rows if f(x))
        print(f"    {label:<20} n={k:<5} what we showed {100 * mean('value_ours', f):.2f} "
              f"(real: {100 * mean('real_purchase', f):.2f}) → new list {100 * mean('value_new', f):.2f}")
        r[label] = (100 * mean("value_ours", f), 100 * mean("real_purchase", f), 100 * mean("value_new", f))
    exp = lambda x: x["explores"]  # noqa: E731
    k = sum(1 for x in rows if exp(x))
    r["exploration"] = {"n": k, "invalid": sum(x["exp_invalid"] for x in rows),
                        "value_without": 100 * mean("value_new", exp), "value_with": 100 * mean("value_exp", exp)}
    print(f"  EXPLORATION (explore=True): {k} of {len(rows)} requests explore ({100 * k / max(len(rows), 1):.0f} %); "
          f"invalid or with something already bought: {r['exploration']['invalid']}; first 8 slots intact in "
          f"{100 * mean('exp_same_8', exp):.0f} %; slot 9 with a new product {100 * mean('exp_new', exp):.0f} %; "
          f"slot 10 changed {100 * mean('exp_rest', exp):.0f} %")
    print(f"    cost according to the model in the exploring requests (purchases/100): without exploring "
          f"{r['exploration']['value_without']:.2f} → exploring {r['exploration']['value_with']:.2f}")
    return r


def exploration_report(requests: dict, feedback: dict, versions: dict, history: dict, hours: float = 3) -> None:
    """What happened with what was explored, read from the feedback. For each version, compares the requests that
    were due to explore (by their request_id) with the rest. In a version that does NOT explore both groups must
    come out equal (a sanity check); in one that explores, the difference is what exploring adds and costs."""
    known = set(history["products"])
    ts = [requests[rid][0] for rid in feedback if rid in requests]
    if not ts:
        return
    since = max(ts) - hours * 3600 * 1000
    groups: dict = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])  # n, new in slot 9, click s9-10, purchase s9-10, purchase list, click list, new per list
    for rid, (shown, click, purchase, ours) in feedback.items():
        if not ours or rid not in requests or requests[rid][0] < since:
            continue
        g = groups[(versions.get(rid, ""), should_explore(rid))]
        g[0] += 1
        g[1] += shown[SLOT_NEW] not in known
        g[2] += click in (shown[SLOT_NEW], shown[SLOT_REST])
        g[3] += purchase in (shown[SLOT_NEW], shown[SLOT_REST])
        g[4] += bool(purchase)
        g[5] += bool(click)
        g[6] += sum(p not in known for p in shown)
    print(f"\nEXPLORATION in the feedback of the last {hours:g} h (responses served by us)")
    print(f"  {'version':<18}{'due':>10}{'responses':>12}{'slot 9 new':>15}{'clicks s9-10':>13}{'buys s9-10':>15}"
          f"{'buys list':>15}{'clicks list':>13}{'new/list':>14}")
    for (version, due), g in sorted(groups.items()):
        if g[0] < 50:
            continue
        print(f"  {version:<18}{'yes' if due else 'no':>10}{g[0]:>12}{100 * g[1] / g[0]:>13.1f} %{g[2]:>13}{g[3]:>15}"
              f"{100 * g[4] / g[0]:>13.2f} %{100 * g[5] / g[0]:>11.2f} %{g[6] / g[0]:>14.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="download nothing new: use data/learner/")
    ap.add_argument("--dry-run", type=int, default=0, help="number of real requests to rehearse recommend() on")
    ap.add_argument("--name", default=None,
                    help="save to data/learner_<name>.json instead of data/learner.json (does not overwrite the live one)")
    ap.add_argument("--without", default="",
                    help="signals or groups (o1, o2, h, x) this training does NOT use, comma separated (e.g. same_sub)")
    ap.add_argument("--only", choices=["new", "known"], default=None,
                    help="train only on responses to that shopper type (hybrid specialist)")
    ap.add_argument("--click-weight", type=float, default=None,
                    help=f"how much a click without purchase counts as a purchase (default {CLICK_WEIGHT}; with --choice, "
                         f"{CHOICE_CLICK_WEIGHT})")
    ap.add_argument("--choice", action="store_true",
                    help="AP8: learn 'which of the 10 shown is chosen' instead of 'bought or not' product by product")
    args = ap.parse_args()

    from src import catalog as catalog_mod
    catalog = catalog_mod.load_catalog()
    history = load_history()
    if args.no_download:
        events = _read("events.pkl", {"rows": []})["rows"]
        rf = _read("responses.pkl", {"requests": {}, "feedback": {}})
        requests, feedback = rf["requests"], rf["feedback"]
    else:
        events, requests, feedback = download_live()
    versions = _read("responses.pkl", {}).get("version", {})
    print(f"data: {len(events)} events today, {len(requests)} requests, {len(feedback)} responses with feedback")

    t0 = time.time()
    data = training_matrix(catalog, history, events, requests, feedback)
    print(f"signals computed in {time.time() - t0:.0f} s ({len(data['t'])} responses)")

    without = tuple(s for n in args.without.split(",") if n for s in GROUPS.get(n, [n]))  # "o1" / "o2" = whole group
    unknown = [n for n in without if n not in NAMES]
    if unknown:
        raise SystemExit(f"--without: signals that don't exist: {unknown}. Available: {NAMES}")
    if args.only:
        data = only_users(data, args.only == "new")
        without += ("new_user",) + tuple(GROUPS["o1"])  # constant within a single shopper type
    without = tuple(dict.fromkeys(without))
    if args.choice:
        model, extra = train_choice(data, CHOICE_CLICK_WEIGHT if args.click_weight is None else args.click_weight,
                                    without=without)
    else:
        model, extra = train(data, CLICK_WEIGHT if args.click_weight is None else args.click_weight, without=without)
    model.info["trained_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    if args.dry_run:
        model.info["dry_run"] = dry_run(catalog, history, events, requests, feedback, extra["model_val"],
                                        extra["cutoff_ms"], args.dry_run)
    exploration_report(requests, feedback, versions, history)
    model.save(args.name)
    print(f"\nsaved {Model.file(args.name)} (not committed to the repo)")


if __name__ == "__main__":
    main()
