"""Offline evaluation: scores every version on the last week of the history. Owner: Evaluation.

    python -m src.evaluate                 # every ARM, with the test set and State saved in data/
    python -m src.evaluate --calibrate     # also compares with the real leaderboard of each version that went live
    python -m src.evaluate --recompute     # rebuilds the State (when some piece changes)
    python -m src.evaluate --arms C,CF,V2 --n 20000   # only those arms and part of the test set (faster)
    python -m src.evaluate --recall        # separately: recall@10 of the candidate sources on shop.history_events

Reproduces starter/offline_eval.ipynb with our contract (src/state.py). The "exam" is 100,000 carousel visits from the
last week; the State only uses data BEFORE that week, so nothing looks at the future.

To make offline look like the leaderboard (Sat night):
- Each arm uses EXACTLY the parameters it ran with live (including the special keys of src/service.py) and, if the
  answer fails or is invalid, it's replaced by best sellers, like the service does.
- `--calibrate` asks the public leaderboard API for the expected conversion of each of our versions and of the
  baseline in the SAME window, and says which offline metric ranks the versions like the leaderboard (rank
  correlation).
- The leaderboard scores the hidden model's purchase PROBABILITY (that's why it gives 1.0545 % and not round numbers):
  it doesn't have the noise of our real purchases in the feedback. It's the best ground truth we have to calibrate.
"""
from __future__ import annotations

import argparse
import math
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import requests

from src import ranking
from src.state import State, build
from src.rawtree import TEAM, query
from src.validate import response_errors

DATA = Path(__file__).resolve().parent.parent / "data"
N_TEST = 100_000
BOOTSTRAP = 200
"""How many resamples the bootstrap does for the confidence interval (see `bootstrap()`)."""
IPS_CAP = 50
"""Max weight of a hit (1/propensity can be huge and make the score jump around). Same cap as R5."""
LEADERBOARD = "https://datathon.rawtree.dev/api/state"
"""Public leaderboard API: ?by=model&from=<epoch>&to=<epoch> (window of 15 min to 3 h, in 5-minute steps)."""

# ---------------------------------------------------------------- arms (= versions, as they ran live)
_V0 = {"_no_covisits": True, "_no_bought": True}
ARMS: dict[str, dict | None] = {
    "baseline": None,                                          # all-time best sellers in stock: the leaderboard's 0
    "C": _V0,                                                  # v0: best sellers of the last viewed category
    "CF": {**_V0, "_filter_bought": True},                     # C without what that shopper already bought
    "D": {"_no_bought": True},                                 # co-visits, no filter
    "E": {**_V0, "revisits": 0.3},                             # C + revisits
    "T": {**_V0, "trend": 0.5},                                # C + trends (offline there are hardly any trends)
    "A": {"_no_covisits": True},                               # v1 without co-visits (with ranking's filter)
    "B": {},                                                   # ranking.py with its defaults (= v1)
    "M": {"use_model": True},                                  # R5, logistic regression
    "MT": {"use_model": True, "trend": 0.5},
    "MF": {"use_model": True, "_filter_bought": True},
    "MTF": {"use_model": True, "trend": 0.5, "_filter_bought": True},
    "V2": {"_choice": True, "_filter": True},                  # e-samen-v2 (src/choice.py) dropping what was bought
    "V2N": {"_choice": True, "_filter": False},                # e-samen-v2 without dropping it
    "V3": {"_choice": True, "_filter": True, "_favourites": True},  # v2 + cold shoppers to their favourite category
    "R6": {"use_tree": True},                                  # LightGBM tree (src/tree.py), without bought products
    "A6": {"_combo": True},                                    # live from 18:51: R6 with views, V3 without views
}
"""Name -> params as in the service's arm sets. To measure a new idea, add a line here with its key from
ranking.PARAMS."""

LIVE = [("baseline", "baseline"), ("e-samen-v0", "C"), ("e-samen-v1-A", "A"), ("e-samen-v1-B", "B"),
        ("e-samen-v1-C", "C"), ("e-samen-v1-CF", "CF"), ("e-samen-v1-D", "D"), ("e-samen-v1-E", "E"),
        ("e-samen-v1-T", "T"), ("e-samen-v1-A6", "A6")]
"""(model_version live, offline arm that reproduces it). The afternoon's v0 is equivalent to C (it had no other
pieces). M, MT, MF and MTF aren't here: R5 v1 ran live (with already_bought) and today's code is R5 v2, another
model."""

COMPARE_WITH = ("baseline", "C")
"""Which arms the difference with its confidence interval is printed against."""

METRICS = ["hit_rate@10", "purchase_hit@10", "purchase_hit_sr", "IPS10_clicks", "IPS10_purchases",
           "IPS10_purchases_sr", "PBM_purchases", "PBM_purchases_sr"]
"""Columns of `hits()`. "_sr" = without repurchases: a purchase of a product the shopper had already bought before
the cutoff doesn't count (the organiser warns that the CSV exaggerates repurchases)."""
METRICS_CI = ["purchase_hit@10", "IPS10_purchases", "PBM_purchases_sr"]
"""Metrics printed with a confidence interval (the rest go in the table)."""


def cutoff_date() -> str:
    """Last day of shop.history_sessions minus 7 days, as in the notebook."""
    last = query("SELECT toDate(max(ts::DateTime64(3))) AS d FROM shop.history_sessions")[0]["d"]
    return str(date.fromisoformat(last) - timedelta(days=7))


def _to_request(f: dict) -> dict:
    """history_sessions row -> same format as a shop.requests row (what recommend expects)."""
    return {
        "request_id": f["request_id"], "ts": f["ts"], "user_id": f["user_id"],
        "context": {"device": f["device"], "region": f["region"], "page": f["page"]},
        "recent_views": f.get("recent_views") or [],
        # what really happened (recommend doesn't look at it; only the metrics use it)
        "shown": f["shown"], "propensity": f["propensity"],
        "clicked": f.get("clicked") or None, "purchased": f.get("purchased") or None,
    }


def load_test(cutoff: str) -> list[dict]:
    """The 100,000 exam visits. Downloaded once and then read from data/ (always the same ones)."""
    target_path = DATA / f"test_{cutoff}.pkl"
    if target_path.exists():
        return pickle.loads(target_path.read_bytes())
    print(f"Downloading {N_TEST} test visits (first time only)...")
    rows = query(f"""
        SELECT request_id::String AS request_id, toString(ts) AS ts, user_id::String AS user_id,
               `context.device`::String AS device, `context.region`::String AS region,
               `context.page`::String AS page, recent_views, shown, propensity,
               clicked::String AS clicked, purchased::String AS purchased
        FROM shop.history_sessions WHERE ts >= '{cutoff}' ORDER BY rand() LIMIT {N_TEST}""", timeout=600)
    test = [_to_request(f) for f in rows]
    DATA.mkdir(exist_ok=True)
    target_path.write_bytes(pickle.dumps(test))
    return test


def load_state(cutoff: str, recompute: bool) -> State:
    """State built only with data before the cutoff. Saved in data/ because it takes a while."""
    target_path = DATA / f"state_{cutoff}.pkl"
    changed = recompute or not target_path.exists()
    if not changed:
        state = pickle.loads(target_path.read_bytes())
    else:
        print("Building the offline State...")
        state = build(until=cutoff)
    # a State saved before a field was added doesn't have it: set it empty
    for fld in fields(State):
        if not hasattr(state, fld.name):
            setattr(state, fld.name, fld.default_factory())
    # build() doesn't fill user_categories (the service refresh does): A6 and V3 need it
    if not state.user_categories:
        from src import choice
        print("Computing each shopper's favourite categories before the cutoff (once)...")
        state.user_categories = choice.load_user_categories(cutoff)
        changed = True
    if changed:  # only the main process gets here: the per-arm ones read the finished State
        DATA.mkdir(exist_ok=True)
        target_path.write_bytes(pickle.dumps(state))
    return state


# ---------------------------------------------------------------- models that need preparing first
def prepare_models(cutoff: str, arms: list[str]) -> list[str]:
    """Creates what M* and v2 need, WITHOUT looking at the future. Returns the arms that can't be evaluated."""
    outside = []
    if any(ARMS[b] and ARMS[b].get("use_model") for b in arms):
        from src import scoring
        if scoring.CUTOFF != cutoff:
            print(f"WARNING: scoring.CUTOFF={scoring.CUTOFF} != cutoff {cutoff}: M, MT, MF and MTF are skipped")
            outside += [b for b in arms if ARMS[b] and ARMS[b].get("use_model")]
        elif scoring.saved_model() is None:  # missing or from another version: M would silently be B
            print("Training R5 (python -m src.scoring, data before the cutoff)...")
            scoring.main()
    if any(ARMS[b] and (ARMS[b].get("use_tree") or ARMS[b].get("_combo")) for b in arms):
        try:
            _offline_tree(cutoff)
        except Exception as e:
            print(f"WARNING: couldn't prepare R6 ({e}); R6 and A6 are skipped")
            outside += [b for b in arms if ARMS[b] and (ARMS[b].get("use_tree") or ARMS[b].get("_combo"))]
    if True:  # v2 also provides PBM's position curve: prepared even if the arm isn't evaluated
        try:
            _model_v2(cutoff)
        except Exception as e:  # no network or no pkl: evaluate everything else
            print(f"WARNING: couldn't prepare v2 ({e}); skipped, PBM uses the click curve")
            outside += [b for b in arms if ARMS[b] and ARMS[b].get("_choice")]
    return outside


def _model_v2(cutoff: str):
    """Choice model trained ONLY with data < cutoff (data/choice.json uses the whole history, which includes the
    test week: evaluating with it would be cheating). Saved in data/ for the worker processes.
    Also saves the position curve it learns (see `position_curve`)."""
    from src import choice
    target_path = DATA / f"choice_offline_{cutoff}.pkl"
    if target_path.exists():
        saved = pickle.loads(target_path.read_bytes())
        if isinstance(saved, dict):  # if it isn't a dict it's the old format (model only, no curve): rebuild
            return saved["model"]
    if choice.CUTOFF != cutoff:
        raise ValueError(f"choice.CUTOFF={choice.CUTOFF} != cutoff {cutoff}")
    if not (DATA / "choice.pkl").exists():
        print("Downloading the v2 data (python -m src.choice --download, ~30 s)...")
        choice.download()
    print("Training v2 with data before the cutoff...")
    d = pickle.loads((DATA / "choice.pkl").read_bytes())
    ses, imp, firsts = d["ses"], d["imp"], d["purchases"]
    first = {(f["u"], f["p"]): f["t"] for f in firsts}
    ses = [s for s in ses if s["p"] in s["shown"]]
    fresh = [s for s in ses if not choice._is_repurchase(s, first)]
    purchases, expected = choice.qualities(imp, ses, before=True)
    rank = choice.popularity_ranks(firsts, cutoff)
    catalog_info = load_state(cutoff, recompute=False).catalog_info
    training = [s for s in fresh if choice.TRAIN_START <= s["ts"] < cutoff]
    rows = choice.prepare(training, catalog_info, purchases, expected, rank, with_slot=True, leave_one_out=True)
    w = choice.fit(rows)
    curve = [1.0] + [math.exp(x) for x in w[len(choice.SIGNALS):]]  # effect of slots 2..10 vs the 1st
    lc = {pid: choice.log_quality(pid, purchases, expected) for pid in set(expected) | set(purchases)}
    typical = math.log(choice.typical_quality(purchases, expected))
    lc.update({pid: typical for pid in catalog_info if pid not in lc})
    model = choice.Model(w[: len(choice.SIGNALS)], lc, rank)  # without the slots, like the live model
    target_path.write_bytes(pickle.dumps({"model": model, "curve": curve}))
    return model


def _offline_tree(cutoff: str) -> dict:
    """R6 without looking at the future. The trees (data/tree.txt) are trained only with data < cutoff, but
    data/tree.json stores quality and popularity with the WHOLE history (for live), which includes the test week's
    purchases: the tree would "know" what got bought in the exam. Here they're recomputed with data < cutoff, as in
    src/tree.py's validation. It's installed into src.tree so that ranking.recommend uses it."""
    import lightgbm as lgb
    from collections import Counter
    from src import tree
    if tree.CUTOFF != cutoff:
        raise ValueError(f"tree.CUTOFF={tree.CUTOFF} != cutoff {cutoff}")
    if not (DATA / "tree.txt").exists():
        raise FileNotFoundError("data/tree.txt is missing: run python -m src.tree")
    target_path = DATA / f"tree_offline_{cutoff}.pkl"
    if target_path.exists():
        lc, rank = pickle.loads(target_path.read_bytes())
    else:
        d = pickle.loads((DATA / "choice.pkl").read_bytes())
        ses = [s for s in d["ses"] if s["p"] in s["shown"]]
        purchases, expected = tree.qualities(d["imp"], ses, before=True)
        lc = {pid: tree.log_quality(pid, purchases, expected) for pid in set(expected) | set(purchases)}
        lc["__typical__"] = math.log(tree.typical_quality(purchases, expected))
        rank = {pid: i for i, (pid, _) in
                enumerate(Counter(f["p"] for f in d["purchases"] if f["t"] < cutoff).most_common())}
        target_path.write_bytes(pickle.dumps((lc, rank)))
    tree._model = {"booster": lgb.Booster(model_file=str(DATA / "tree.txt")), "lc": lc, "rank": rank}
    tree._loaded = True
    return tree._model


# ---------------------------------------------------------------- recommending like the service
def prepare_arm(name: str, state: State) -> tuple[State, dict, bool, bool, bool]:
    """(arm's State, params for recommend, filter bought per shopper?, use v2?, A6 combo?).
    Same special keys as the service's safe response."""
    params = ARMS[name]
    if params is None:  # baseline: just the catalog, availability and all-time best sellers
        return State(catalog_info=state.catalog_info, available_ids=state.available_ids, popular=state.popular), {}, \
            False, False, False
    params = dict(params)
    apply_filter = bool(params.pop("_filter_bought", False))
    v2 = bool(params.pop("_choice", False))
    combo = bool(params.pop("_combo", False))
    empty_out = [fld for key_name, fld in (("_no_covisits", "covisits"), ("_no_bought", "bought"))
                 if params.pop(key_name, False)]
    return replace(state, **{fld: {} for fld in empty_out}), params, apply_filter, v2, combo


def recommendations(name: str, test: list[dict], state: State, cutoff: str) -> tuple[list[list[str]], int]:
    """The 10 for each visit and how many the "service" had to replace with best sellers (failure or invalid)."""
    est, params, apply_filter, v2, combo = prepare_arm(name, state)
    model = _model_v2(cutoff) if v2 or combo else None
    if v2 or combo:
        from src import choice
    if combo or params.get("use_tree"):
        _offline_tree(cutoff)  # in this process: ranking.recommend will use the tree without looking ahead
    available_ids = state.available_ids
    rescue = [pid for pid in state.popular if pid in available_ids][:10]
    recs, replaced = [], 0
    for t in test:
        e = est
        if apply_filter:  # filtered with the shopper's real purchases, BEFORE emptying pieces (like the service)
            already = state.bought.get(str(t.get("user_id", "")))
            if already:
                e = replace(est, available_ids=est.available_ids - already)
        try:
            if combo:  # A6: with views, the tree; without views, their favourite category with v2
                if any(v.get("product_id") for v in (t.get("recent_views") or [])):
                    items = ranking.recommend(t, e, {"use_tree": True, "filter_bought": True})
                else:
                    items = choice.suggest(t, e, model, filter_bought=True, cold_favourites=True)
            elif v2:
                items = choice.suggest(t, e, model, filter_bought=params.get("_filter", True),
                                       cold_favourites=params.get("_favourites", False))
            else:
                items = ranking.recommend(t, e, params)
            if response_errors(items, available_ids):
                raise ValueError("invalid")
        except Exception:
            items, replaced = rescue, replaced + 1
        recs.append(items)
    return recs, replaced


# ---------------------------------------------------------------- metrics
def position_curve(test: list[dict], cutoff: str) -> list[float]:
    """How much each slot 1..10 is worth relative to the 1st.

    Preferred: the one the choice model learns by comparing WITHIN each carousel (before the cutoff), which separates
    the slot effect from which product was there: [1, 0.59, 0.52, 0.41, ... 0.28] (Sat night; R5 gives something
    similar). If it's missing, the one from the test's clicks per slot, which overestimates the drop (popular stuff
    was on top)."""
    target_path = DATA / f"choice_offline_{cutoff}.pkl"
    if target_path.exists():
        saved = pickle.loads(target_path.read_bytes())
        if isinstance(saved, dict):
            return saved["curve"]
    clicks = [0] * 10
    for t in test:
        if t["clicked"] in t["shown"][:10]:
            clicks[t["shown"].index(t["clicked"])] += 1
    return [max(c, 1) / max(clicks[0], 1) for c in clicks]


def is_repurchase(test: list[dict], state: State) -> list[bool]:
    """Is that visit's purchase a product the shopper had already bought before the cutoff?"""
    return [bool(t["purchased"]) and t["purchased"] in state.bought.get(str(t["user_id"]), ()) for t in test]


def per_visit(t: dict, recs: list[str], repurchase: bool, curve: list[float]) -> list[float]:
    """The METRICS of one visit. hit/purchase_hit: 1/0, or NaN if there was no click/purchase (the mean ignoring NaN
    is the rate).

    IPS10: for each product the old carousel showed that's also in our 10, if it got clicked/bought it adds
    1/propensity (capped at IPS_CAP): what was rarely shown weighs more when it shows up.
    PBM (position-based model): same, but multiplied by curve[our slot] / curve[slot it was shown in]: moving it up
    to the 1st is worth more than leaving it at the 10th, as on the leaderboard."""
    nan = float("nan")
    purchase = t["purchased"]
    purchase_sr = None if repurchase else purchase
    slot = {pid: i for i, pid in enumerate(recs)}
    ips_click = ips_purchase = ips_purchase_sr = pbm = pbm_sr = 0.0
    for j, (pid, prop) in enumerate(zip(t["shown"][:10], (t.get("propensity") or [])[:10])):
        prop = float(prop or 0)  # 0 or NULL can't give an infinite weight
        if pid not in slot or prop <= 0:
            continue
        weight = min(1 / prop, IPS_CAP)
        position = curve[slot[pid]] / curve[j]
        ips_click += weight * (t["clicked"] == pid)
        ips_purchase += weight * (purchase == pid)
        ips_purchase_sr += weight * (purchase_sr == pid)
        pbm += weight * position * (purchase == pid)
        pbm_sr += weight * position * (purchase_sr == pid)
    return [float(t["clicked"] in slot) if t["clicked"] else nan,
            float(purchase in slot) if purchase else nan,
            float(purchase_sr in slot) if purchase_sr else nan,
            ips_click, ips_purchase, ips_purchase_sr, pbm, pbm_sr]


def hits(test: list[dict], recs: list[list[str]], repurchases: list[bool], curve: list[float]) -> np.ndarray:
    """Matrix visits x METRICS (kept per visit for the bootstrap)."""
    return np.array([per_visit(t, r, rc, curve) for t, r, rc in zip(test, recs, repurchases)], dtype=float)


def summary(test: list[dict], recs: list[list[str]], matrix: np.ndarray) -> dict[str, float]:
    """One number per metric. IPS10/PBM in expected purchases (or clicks) per 100 visits, like the leaderboard.
    Plus the official notebook's IPS/SNIPS of the click in slot 1."""
    out: dict[str, float] = {"n": len(test)}
    for k, m in enumerate(METRICS):
        col = matrix[:, k] if len(matrix) else np.array([np.nan])
        value = float(np.nanmean(col)) if np.any(~np.isnan(col)) else 0.0
        out[m] = 100 * value if m.startswith(("IPS", "PBM")) else value
    weights = rewards = 0.0
    ips1 = 0.0
    for t, r in zip(test, recs):
        prop = float((t.get("propensity") or [0])[0] or 0)
        if r and r[0] == t["shown"][0] and prop > 0:
            weights += 1 / prop
            reward = float(t["clicked"] == t["shown"][0])
            rewards += reward / prop
            ips1 += reward / prop
    out["SNIPS_slot1"] = rewards / weights if weights else 0.0
    out["IPS_slot1"] = ips1 / max(len(test), 1)
    return out


def _evaluate_arm(name: str, cutoff: str, n: int) -> tuple:
    """What each parallel process does: reads test and State from data/ and evaluates ONE arm."""
    test = load_test(cutoff)[:n]
    state = load_state(cutoff, recompute=False)
    recs, replaced = recommendations(name, test, state, cutoff)
    matrix = hits(test, recs, is_repurchase(test, state), position_curve(test, cutoff))
    cold_rows = [i for i, t in enumerate(test) if not t["recent_views"]]
    cold = summary([test[i] for i in cold_rows], [recs[i] for i in cold_rows], matrix[cold_rows])
    return name, summary(test, recs, matrix), cold, replaced, matrix


def bootstrap(a: np.ndarray, b: np.ndarray, times: int = BOOTSTRAP) -> list[tuple[float, float, float]]:
    """Difference a - b in each column, with its 95 % confidence interval.

    Bootstrap: draw, with replacement, as many visits as the test has and recompute the difference. Repeat it `times`
    times; the central 95 % of those differences is the interval. Both policies ALWAYS use the same drawn visits
    (paired bootstrap): that way only what the policy changes counts, not the luck of which visits came up. If the
    interval doesn't include 0, the difference isn't chance."""
    rand = np.random.default_rng(0)  # fixed seed: same result every run
    difference = np.nanmean(a, axis=0) - np.nanmean(b, axis=0)
    samples = []
    for _ in range(times):
        idx = rand.integers(0, len(a), len(a))
        samples.append(np.nanmean(a[idx], axis=0) - np.nanmean(b[idx], axis=0))
    low, high = np.percentile(samples, [2.5, 97.5], axis=0)
    return [(float(d), float(lo), float(hi)) for d, lo, hi in zip(difference, low, high)]


# ---------------------------------------------------------------- calibration against the leaderboard
def live_versions() -> dict[str, tuple[int, int, int]]:
    """model_version -> (requests, first ts, last ts) in epoch, from our feedback joined with submissions."""
    rows = query(f"""
        SELECT s.mv AS v, count() AS n, toUnixTimestamp(min(f.ts)) AS since, toUnixTimestamp(max(f.ts)) AS until
        FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts FROM {TEAM}.feedback
              WHERE request_id != '__init__' AND ts > now() - INTERVAL 2 DAY) AS f
        INNER JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
                    FROM {TEAM}.submissions GROUP BY rid) AS s ON f.rid = s.rid
        GROUP BY v""")
    return {f["v"]: (int(f["n"]), int(f["since"]), int(f["until"])) for f in rows}


def leaderboard(version: str, since: int, until: int) -> dict | None:
    """Expected conversion of `version` and of the baseline in the window that covers it (at most the last 3 h)."""
    a = since // 300 * 300
    b = max(-(-until // 300) * 300, a + 900)
    a = max(a, b - 10800)
    r = requests.get(LEADERBOARD, params={"by": "model", "from": a, "to": b}, timeout=30)
    r.raise_for_status()
    d = r.json()
    our_team = next((t for t in d["teams"] if t["team_id"] == TEAM), None)
    base = d["baseline"]
    if version == "baseline":  # the official baseline is measured on the same requests: lift 1 by definition
        row = {"conversion": base["conversion"], "cold_conversion": base["cold_conversion"],
               "requests": base["requests"]}
    else:
        row = next((v for v in (our_team or {}).get("versions", []) if v["model_version"] == version), None)
    if not row:
        return None
    return {"conv": row["conversion"], "cold": row.get("cold_conversion"), "n": row["requests"],
            "base": base["conversion"], "cold_base": base["cold_conversion"],
            "window": f"{datetime.fromtimestamp(a):%H:%M}-{datetime.fromtimestamp(b):%H:%M}"}


def _ranks(x: list[float]) -> np.ndarray:
    """Rank of each value (ties: average rank)."""
    x = np.asarray(x, dtype=float)
    r = np.empty(len(x))
    r[np.argsort(x, kind="stable")] = np.arange(len(x))
    for v in np.unique(x):
        r[x == v] = r[x == v].mean()
    return r


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation: 1 = they rank the versions the same; 0 = unrelated; -1 = reversed."""
    ra, rb = _ranks(a), _ranks(b)
    return float(np.corrcoef(ra, rb)[0, 1]) if ra.std() > 0 and rb.std() > 0 else float("nan")


def calibrate(results: dict[str, dict], cold_ones: dict[str, dict]) -> None:
    """Which offline metric looks like the leaderboard? Lift = conversion / the baseline's, live and offline."""
    metrics = [m for m in results["baseline"] if m != "n"]
    alive = live_versions()
    rows = []
    for version, arm in LIVE:
        if arm not in results or version not in alive:
            continue
        lb = leaderboard(version, *alive[version][1:])
        if lb and lb["base"] > 0:
            rows.append((version, arm, lb))
    if len(rows) < 3:
        print("\nCalibration: needs at least 3 versions with leaderboard data.")
        return

    def lift(res: dict, arm: str, m: str) -> float:
        base = res["baseline"].get(m) or 0.0
        return res[arm].get(m, 0.0) / base if base else float("nan")

    print("\nCalibration against the leaderboard (expected conversion; lift = version / baseline in the same window):")
    main_ones = ["purchase_hit@10", "purchase_hit_sr", "IPS10_purchases", "IPS10_purchases_sr", "PBM_purchases",
                 "PBM_purchases_sr"]
    print(f"{'version':<17}{'arm':<9}{'window':<13}{'req':>6}{'conv %':>8}{'base %':>8}{'LIFT':>7}" +
          "".join(f"{m[:14]:>16}" for m in main_ones))
    for version, arm, lb in rows:
        print(f"{version:<17}{arm:<9}{lb['window']:<13}{lb['n']:>6}{lb['conv']:>8.3f}{lb['base']:>8.3f}"
              f"{lb['conv'] / lb['base']:>7.2f}" + "".join(f"{lift(results, arm, m):>16.2f}" for m in main_ones))

    live_now = [lb["conv"] / lb["base"] for _, _, lb in rows]
    ranking_of_metrics = []
    for m in metrics:
        off = [lift(results, arm, m) for _, arm, _ in rows]
        if any(math.isnan(x) for x in off):
            continue
        error = float(np.mean([abs(math.log(max(o, 1e-9) / v)) for o, v in zip(off, live_now)]))
        ranking_of_metrics.append((spearman(off, live_now), -error, m, error))
    ranking_of_metrics.sort(reverse=True)
    print(f"\nWhich metric ranks the {len(rows)} versions like the leaderboard? (1 = same; error = how far the lift "
          f"is off, in log)")
    for rho, _, m, error in ranking_of_metrics:
        print(f"  {m:<20} rank correlation {rho:+.2f}   mean error {error:.2f}")

    cold_rows = [(v, b, lb) for v, b, lb in rows if lb.get("cold") and lb.get("cold_base")]
    if len(cold_rows) >= 3:
        live_f = [lb["cold"] / lb["cold_base"] for _, _, lb in cold_rows]
        print("\nCold segment (live: new shopper or new product; offline: no recent views):")
        best_ones = []
        for m in metrics:
            off = [lift(cold_ones, b, m) for _, b, _ in cold_rows]
            if not any(math.isnan(x) for x in off):
                best_ones.append((spearman(off, live_f), m))
        for rho, m in sorted(best_ones, reverse=True)[:5]:
            print(f"  {m:<20} rank correlation {rho:+.2f}")

    best = ranking_of_metrics[0][2]
    base_now = rows[-1][2]["base"]
    no_live = [b for b in results if b not in {br for _, br, _ in rows}]
    if no_live:
        print(f"\nPrediction with the best metric ({best}), current baseline {base_now:.3f} %:")
        for b in no_live:
            print(f"  {b:<10} lift {lift(results, b, best):.2f}  ->  ~{base_now * lift(results, b, best):.2f} %")


# ---------------------------------------------------------------- output
def show(results: dict[str, dict], replaced: dict[str, int] | None = None) -> None:
    columns = ["n"] + METRICS + ["SNIPS_slot1"]
    print(f"{'arm':<11}" + "".join(f"{c[:15]:>16}" for c in columns) + ("    replaced" if replaced else ""))
    for name, row in results.items():
        cells = "".join(f"{row[c]:>16.4f}" if isinstance(row[c], float) else f"{row[c]:>16}" for c in columns)
        print(f"{name:<11}{cells}" + (f"{replaced[name]:>12}" if replaced else ""))


def show_differences(hits_per_arm: dict[str, np.ndarray], against: str) -> None:
    """Each arm against `against`: difference and 95 % interval on METRICS_CI."""
    def verdict(lo: float, hi: float) -> str:
        if lo == hi == 0:
            return "identical"
        return "up" if lo > 0 else "down" if hi < 0 else "could be luck"

    cols = [METRICS.index(m) for m in METRICS_CI]
    print(f"\nDifference vs {against} (95 % confidence interval, bootstrap of {BOOTSTRAP}; IPS/PBM per visit):")
    print(f"{'arm':<11}" + "".join(f"{'diff ' + m + ' [95 % CI]':<52}" for m in METRICS_CI))
    for name, a in hits_per_arm.items():
        if name == against:
            continue
        differences = bootstrap(a[:, cols], hits_per_arm[against][:, cols])
        print(f"{name:<11}" + "".join(f"{d:>+9.4f} [{lo:+.4f}, {hi:+.4f}] {verdict(lo, hi):<17}"
                                      for d, lo, hi in differences))


# ---------------------------------------------------------------- recall@10 on shop.history_events (--recall)
RECALL_DAYS = 10
"""The recall exam is the purchases of the last RECALL_DAYS days of shop.history_events."""
RECALL_ONE_IN = 20
"""1 in every N shoppers is used (picked by user_id, always the same ones): pulling all 12M events is overkill."""


def _recall_popular(ctx: dict, base: dict) -> list[str]:
    """Bare minimum reference: the shop's best sellers (what the baseline would do)."""
    return [p for p in base["popular"] if p not in ctx["bought"]][:10]


def _recall_category_affinity(ctx: dict, base: dict) -> list[str]:
    """What we do now: the best sellers of the category that shopper looked at the most."""
    tally: dict[str, int] = {}
    for p in ctx["viewed"]:
        c = base["category"].get(p)
        if c:
            tally[c] = tally.get(c, 0) + 1
    if not tally:
        return _recall_popular(ctx, base)
    favourite = max(tally, key=tally.get)
    return [p for p in base["by_category"].get(favourite, []) if p not in ctx["bought"]][:10]


def _recall_recent_views(ctx: dict, base: dict) -> list[str]:
    """The last 10 distinct products they looked at (most recent first), without what was already bought."""
    return [p for p in dict.fromkeys(reversed(ctx["viewed"])) if p not in ctx["bought"]][:10]


RECALL_SOURCES = {
    "popular": _recall_popular,
    "category_affinity": _recall_category_affinity,
    "recent_viewed": _recall_recent_views,
}
"""Candidate sources measured by `--recall`. To try a new one (embedding neighbours, co-visits...), write a function
`f(ctx, base) -> list of product_id` and add it here. Only its first 10 count.
  ctx  = what's known about THAT shopper right before the purchase: {"user", "viewed" (product_ids in order, oldest to
         newest, with repeats), "bought" (set), "now" (ms)}.
  base = shared data computed ONLY with what's before the 10 exam days: {"popular" (best sellers, in order),
         "by_category" (category -> best sellers), "category" and "subcategory" (product_id -> name)}."""


def _recall_data() -> dict:
    """Events (views and purchases over the 60 days) of 1 in RECALL_ONE_IN shoppers, the catalog and the best sellers
    from BEFORE the exam window. Downloaded once and saved in data/."""
    target_path = DATA / f"recall_{RECALL_DAYS}d_1in{RECALL_ONE_IN}.pkl"
    if target_path.exists():
        return pickle.loads(target_path.read_bytes())
    print("Downloading the recall data (first time only, ~1 min)...")
    end_ts = int(query("SELECT toUnixTimestamp64Milli(max(ts::DateTime64(3))) AS t FROM shop.history_events")[0]["t"])
    start = end_ts - RECALL_DAYS * 24 * 3600 * 1000
    events: list[tuple] = []
    chunks = 4
    for k in range(chunks):
        rows = query(f"""
            SELECT user_id::String AS u, product_id::String AS p, event_type::String AS e,
                   toUnixTimestamp64Milli(ts::DateTime64(3)) AS t
            FROM shop.history_events
            WHERE event_type::String IN ('view', 'purchase')
              AND cityHash64(user_id::String) % {RECALL_ONE_IN * chunks} = {k} LIMIT 3000000""", timeout=600)
        events += [(f["u"], int(f["t"]), f["e"] == "purchase", f["p"]) for f in rows]
    sales = query(f"""
        SELECT product_id::String AS p, count() AS n FROM shop.history_events
        WHERE event_type = 'purchase' AND ts::DateTime64(3) < fromUnixTimestamp64Milli(toInt64({start}))
        GROUP BY p""", timeout=300)
    catalog_info = query("""SELECT product_id::String AS p, any(category::String) AS c, any(subcategory::String) AS s
                        FROM shop.catalog GROUP BY p""", timeout=120)
    payload = {"start": start, "end_ts": end_ts, "events": sorted(events),
               "sales": {f["p"]: int(f["n"]) for f in sales},
               "category": {f["p"]: f["c"] for f in catalog_info},
               "subcategory": {f["p"]: f["s"] for f in catalog_info}}
    DATA.mkdir(exist_ok=True)
    target_path.write_bytes(pickle.dumps(payload))
    return payload


def recall_history() -> dict[str, dict]:
    """recall@10 of each candidate source on the ORGANIC purchases of shop.history_events (not the carousel).

    For every purchase in the last RECALL_DAYS days the source gets what that shopper had done BEFORE (never after)
    and we check whether the bought product is among the 10 it proposes. Two questions, two columns:
      - "everything before": with all their views before the purchase. Note: people almost always buy something they
        just looked at, so repeating what was viewed wins by definition here. It's a ceiling, not for comparing
        sources.
      - "before seeing it": only with what came before the FIRST time they looked at that product. Measures whether
        the source can suggest something the shopper hasn't seen yet and will end up buying: this is the one that
        compares sources.
    Only purchases of a product that shopper hadn't bought before count (there are no repurchases live).
    It's not a conversion score: it's for discarding bad sources; what goes live is decided by an A/B."""
    d = _recall_data()
    popular = [p for p, _ in sorted(d["sales"].items(), key=lambda kv: -kv[1])]
    by_category: dict[str, list[str]] = {}
    for p in popular:
        by_category.setdefault(d["category"].get(p, ""), []).append(p)
    base = {"popular": popular, "by_category": by_category,
            "category": d["category"], "subcategory": d["subcategory"]}

    found = {n: [0, 0] for n in RECALL_SOURCES}
    purchases = repurchases = no_views = 0
    user = None
    viewed: list[str] = []
    first_view: dict[str, int] = {}   # product -> how many views the shopper had BEFORE looking at it the 1st time
    bought: set[str] = set()
    for u, t, is_purchase, p in d["events"]:          # sorted by shopper and time
        if u != user:
            user, viewed, first_view, bought = u, [], {}, set()
        if not is_purchase:
            first_view.setdefault(p, len(viewed))
            viewed.append(p)
            continue
        if t >= d["start"]:
            if p in bought:
                repurchases += 1
            else:
                purchases += 1
                no_views += not viewed
                before_seen = viewed[:first_view.get(p, len(viewed))]
                for name, source in RECALL_SOURCES.items():
                    for k, context in enumerate((viewed, before_seen)):
                        ctx = {"user": u, "viewed": context, "bought": bought, "now": t}
                        found[name][k] += p in source(ctx, base)[:10]
        bought.add(p)

    print(f"recall@10 on shop.history_events, last {RECALL_DAYS} days, 1 in {RECALL_ONE_IN} shoppers, "
          f"{purchases} purchases ({repurchases} repurchases left out; {no_views} with no previous view)")
    print(f"{'source':<24}{'everything before':>18}{'before seeing it':>18}{'+- (95 %)':>12}")
    out = {}
    for name, (everything, before) in found.items():
        r_all, r_before = everything / max(purchases, 1), before / max(purchases, 1)
        margin = 1.96 * math.sqrt(max(r_before * (1 - r_before), 1e-9) / max(purchases, 1))
        out[name] = {"everything_before": r_all, "before_seen": r_before, "n": purchases}
        print(f"{name:<24}{100 * r_all:>16.2f} %{100 * r_before:>16.2f} %{100 * margin:>10.2f} %")
    return out


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--recall", action="store_true",
                      help="only the recall@10 of the candidate sources on shop.history_events (RECALL_SOURCES)")
    args.add_argument("--recompute", action="store_true", help="rebuilds the offline State")
    args.add_argument("--calibrate", action="store_true", help="compares with the leaderboard of the live versions")
    args.add_argument("--arms", default=",".join(ARMS), help="comma-separated arms (default: all)")
    args.add_argument("--n", type=int, default=N_TEST, help="how many test visits to use (default: all)")
    a = args.parse_args()
    if a.recall:  # separate measurement: doesn't use the carousel, the State or the arms
        recall_history()
        return

    cutoff = cutoff_date()
    test = load_test(cutoff)[:a.n]
    load_state(cutoff, a.recompute)
    arms = [b for b in a.arms.split(",") if b in ARMS]
    for required in COMPARE_WITH:  # without them there's nothing to compare against
        if required not in arms:
            arms.insert(0, required)
    outside = prepare_models(cutoff, arms)
    arms = [b for b in arms if b not in outside]
    clicks = sum(bool(t["clicked"]) for t in test)
    purchases = sum(bool(t["purchased"]) for t in test)
    print(f"Cutoff {cutoff}, test {len(test)} visits, {clicks} with a click, {purchases} with a purchase, "
          f"ranking.py default (arm B) = {ranking.NAME}\n")

    # one arm per process; at most 4: each one loads its own copy of the test set and the State (hundreds of MB)
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        outs = list(pool.map(_evaluate_arm, arms, [cutoff] * len(arms), [a.n] * len(arms)))
    results = {name: everything for name, everything, _, _, _ in outs}
    cold_ones = {name: cold for name, _, cold, _, _ in outs}
    replaced = {name: s for name, _, _, s, _ in outs}
    hits_per_arm = {name: m for name, _, _, _, m in outs}

    show(results, replaced)
    print("\nCold segment (no recent views):")
    show(cold_ones)
    for against in COMPARE_WITH:
        show_differences(hits_per_arm, against)
    if a.calibrate:
        calibrate(results, cold_ones)


if __name__ == "__main__":
    main()
