"""e-samen-v2: CHOICE model, "out of the 10 we showed, which one do they buy?". Owner: Operator.

    python -m src.choice --download   # pulls the 28,053 carousel purchases and impressions from RawTree (~30 s)
    python -m src.choice              # trains, validates against v0 and saves data/choice.json

The idea, for a product manager:
- The carousel history has 28,053 purchases. For each one we know the 10 products shown, their order and which one
  was bought. A "conditional logit" (the same neuron as R5, but comparing ONLY among the 10 of each visit) learns what
  makes a product beat its 9 carousel neighbours.
- Comparing within what was shown avoids the old carousel's bias (it showed popular stuff): popular only wins if it
  really gets picked more than what was next to it. Position goes in as a signal when training and is dropped when
  recommending.
- Key new signal: each product's QUALITY = purchases it got / purchases it "should" have got given the slots it
  appeared in (if it showed up a lot in slot 1, more purchases were expected). Generalises better than popularity.
- Price signals relative to what was viewed (measured: people buy at ~0.55x the price of the last viewed product),
  subcategory, brand and revisit. Never the product_id directly (except through the smoothed quality).
- Purchases of products the shopper ALREADY had (repurchases) are removed from training and validation.

Honest validation (never looks at the future): quality and popularity with data BEFORE CUTOFF; validated on the
purchases after it. Metrics: top-1 hit (is the bought one the highest-scored of the 10?) and mean log-likelihood.
"""
from __future__ import annotations

import json
import math
import pickle
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
CUTOFF = "2026-11-07"
TRAIN_START = "2026-10-01"
SMOOTHING = 5.0
"""Quality = (purchases + 5*mu) / (expected + 5), with mu = TYPICAL quality of a well-measured product (median of those
with >= 500 impressions). A product with few impressions stays close to typical, not to 1: smoothing towards 1 made
products that were hardly ever shown (1,845 of them) look better than proven ones (measured: typical ~ e^-0.8)."""
_MU: dict = {}
L2 = 1.0

SIGNALS = [
    "log_quality", "popularity", "same_category", "same_subcategory", "same_brand", "sub_in_views",
    "log_price_ratio", "log_price_ratio2", "log_price", "revisit", "cold", "cold_x_quality",
    "cold_x_popularity", "cold_x_log_price",
]
SLOTS = [f"slot_{i}" for i in range(2, 11)]


# ---------------------------------------------------------------- pieces (quality and popularity)
def position_bias(imp: list[dict], ses: list[dict], before: bool | None) -> np.ndarray:
    """Purchases per impression in each slot 1..10 (what being in that slot is worth)."""
    n_imp = np.zeros(11)
    for f in imp:
        if before is None or bool(f["before"]) == before:
            n_imp[int(f["pos"])] += float(f["n"])
    n_purch = np.zeros(11)
    for s in ses:
        if before is None or (s["ts"] < CUTOFF) == before:
            if s["p"] in s["shown"]:
                n_purch[s["shown"].index(s["p"]) + 1] += 1
    return n_purch / np.maximum(n_imp, 1)


def qualities(imp: list[dict], ses: list[dict], before: bool | None) -> tuple[dict, dict]:
    """product_id -> (purchases, purchases expected from its slots). before=True: only data < CUTOFF; None: all."""
    sp = position_bias(imp, ses, before)
    expected: dict[str, float] = defaultdict(float)
    for f in imp:
        if before is None or bool(f["before"]) == before:
            expected[f["pid"]] += float(f["n"]) * sp[int(f["pos"])]
    purchases: Counter = Counter()
    for s in ses:
        if before is None or (s["ts"] < CUTOFF) == before:
            purchases[s["p"]] += 1
    return dict(purchases), dict(expected)


def typical_quality(purchases: dict, expected: dict) -> float:
    key_name = (id(purchases), id(expected))
    if key_name not in _MU:
        r = [(purchases.get(p, 0) + 0.5) / (e + 0.5) for p, e in expected.items() if e >= 5.0]
        _MU.clear()
        _MU[key_name] = float(np.median(r)) if r else 1.0
    return _MU[key_name]


def log_quality(pid: str, purchases: dict, expected: dict, drop_ids: int = 0) -> float:
    mu = typical_quality(purchases, expected)
    return math.log((purchases.get(pid, 0) - drop_ids + SMOOTHING * mu) / (expected.get(pid, 0.0) + SMOOTHING))


def popularity_ranks(firsts: list[dict], until: str | None) -> dict[str, int]:
    """Rank of each product by number of distinct buyers (0 = best seller)."""
    c = Counter(f["p"] for f in firsts if until is None or f["t"] < until)
    return {pid: i for i, (pid, _) in enumerate(c.most_common())}


# ---------------------------------------------------------------- signals for a candidate
def signals(cand: list[str], viewed: list[str], cat: dict, lc: dict[str, float], rank: dict[str, int]) -> np.ndarray:
    """Matrix (len(cand), len(SIGNALS)). `lc` = log_quality per product (already computed)."""
    n_pop = len(rank) + 1
    last = cat.get(viewed[-1], {}) if viewed else {}
    cold = 0.0 if viewed else 1.0
    p_last = _price(last)
    viewed_subs = {cat.get(v, {}).get("subcategory") for v in viewed} - {None, ""}
    viewed_set = set(viewed)
    X = np.zeros((len(cand), len(SIGNALS)))
    for k, pid in enumerate(cand):
        d = cat.get(pid, {})
        q = lc.get(pid, 0.0)
        pop = -math.log1p(rank.get(pid, n_pop))
        lp = math.log(max(_price(d), 1.0))
        lr = math.log(max(_price(d), 1.0) / max(p_last, 1.0)) if viewed else 0.0
        X[k] = (q, pop,
                float(bool(last) and d.get("category") == last.get("category")),
                float(bool(last) and d.get("subcategory") == last.get("subcategory")),
                float(bool(last) and d.get("brand") == last.get("brand")),
                float(d.get("subcategory") in viewed_subs),
                lr, lr * lr, lp, float(pid in viewed_set), cold, cold * q, cold * pop, cold * lp)
    return X


def _price(d: dict) -> float:
    try:
        return float(d.get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- training (conditional logit)
def _viewed(s: dict) -> list[str]:
    return [v["product_id"] for v in (s["rv"] or []) if v.get("product_id")]


def _is_repurchase(s: dict, first: dict) -> bool:
    t = first.get((s["u"], s["p"]))
    return t is not None and t < s["ts"]


def prepare(sessions, cat, purchases, expected, rank, with_slot, leave_one_out):
    """List of (X 10xk, index of the bought one)."""
    rows = []
    for s in sessions:
        if s["p"] not in s["shown"]:
            continue
        lc = {pid: log_quality(pid, purchases, expected, drop_ids=int(leave_one_out and pid == s["p"]))
              for pid in s["shown"]}
        X = signals(s["shown"], _viewed(s), cat, lc, rank)
        if with_slot:
            P = np.zeros((len(s["shown"]), len(SLOTS)))
            for j in range(1, len(s["shown"])):
                P[j, j - 1] = 1.0
            X = np.hstack([X, P])
        rows.append((X, s["shown"].index(s["p"])))
    return rows


def fit(rows, columns: list[int] | None = None, iters: int = 30) -> np.ndarray:
    """Newton on the softmax log-likelihood within each visit, with an L2 brake."""
    k = rows[0][0].shape[1] if columns is None else len(columns)
    w = np.zeros(k)
    for _ in range(iters):
        g = -L2 * w
        H = -L2 * np.eye(k)
        for X, y in rows:
            Xc = X if columns is None else X[:, columns]
            z = Xc @ w
            p = np.exp(z - z.max())
            p /= p.sum()
            avg = p @ Xc
            g += Xc[y] - avg
            H -= (Xc * p[:, None]).T @ Xc - np.outer(avg, avg)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w


def check(rows, w, columns=None) -> dict:
    hit = ll = top3 = 0.0
    for X, y in rows:
        Xc = X if columns is None else X[:, columns]
        z = Xc @ w
        p = np.exp(z - z.max())
        p /= p.sum()
        ordering = np.argsort(-z, kind="stable")
        hit += ordering[0] == y
        top3 += y in ordering[:3]
        ll += math.log(max(p[y], 1e-12))
    n = max(len(rows), 1)
    return {"n": len(rows), "hit_1": hit / n, "hit_top3": top3 / n, "loglik": ll / n}


# ---------------------------------------------------------------- favourite category (cold shopper)
def load_user_categories(until: str | None = None, live_hours: int = 24) -> dict[str, list[str]]:
    """user_id -> their 3 categories with the most events (view, cart, purchase), most to least.

    Measured (carousel visits WITHOUT views since 2026-10-01, category shown vs the shopper's earlier activity):
    their #1 category converts 0.78-0.86 %, #2 ~0.26 %, #3 ~0.09 %, one with no activity 0.03 %.
    `until`: only earlier history_events (offline). None: the whole history + shop.events of the last hours."""
    from src.rawtree import query
    where_extra = f"WHERE e.ts::DateTime64(3) < '{until}'" if until else ""
    live_now = "" if until else f"""UNION ALL SELECT user_id::String u, product_id::String pid FROM shop.events
                                WHERE ts::DateTime64(3) > now() - INTERVAL {live_hours} HOUR"""
    rows = query(f"""
        SELECT u, cat FROM (
          SELECT x.u u, c.category::String cat, count() n FROM (
            SELECT e.user_id::String u, e.product_id::String pid FROM shop.history_events e {where_extra} {live_now}
          ) x JOIN shop.catalog c ON c.product_id::String = x.pid
          GROUP BY u, cat ORDER BY u, n DESC, cat
        ) LIMIT 3 BY u""", timeout=600)
    out: dict[str, list[str]] = {}
    for f in rows:
        out.setdefault(f["u"], []).append(f["cat"])
    return out


# ---------------------------------------------------------------- recommending live
class Model:
    def __init__(self, w: np.ndarray, lc: dict[str, float], rank: dict[str, int]):
        self.w, self.lc, self.rank = w, lc, rank

    @classmethod
    def load(cls, target_path: Path = DATA / "choice.json") -> "Model | None":
        if not target_path.exists():
            return None
        d = json.loads(target_path.read_text(encoding="utf-8"))
        return cls(np.array(d["w"]), d["lc"], d["rank"])


def suggest(request: dict, state, model: Model, filter_bought: bool = True, n_cold: int = 300,
            cold_favourites: bool = False) -> list[str]:
    """Candidates: every available product in the category of the last viewed one (+ what was viewed in that
    category); for a shopper without views, the n_cold best by quality and popularity. They get scored and the best
    10 are returned."""
    cat = state.catalog_info
    viewed = [v["product_id"] for v in (request.get("recent_views") or []) if v.get("product_id")]
    favourites: list[str] = []
    already = state.bought.get(str(request.get("user_id", "")), set()) if filter_bought else set()
    disp = state.available_ids
    if viewed:
        category = cat.get(viewed[-1], {}).get("category")
        # ONLY its category (+ whatever viewed product belongs to it): the old carousel always showed 10 from the
        # category of the last viewed product, so the model can't compare across categories (and 97.6 % buy in it).
        cand = [pid for pid in _by_category(state).get(category, []) if pid in disp]
        cand += [pid for pid in reversed(viewed) if pid in disp and cat.get(pid, {}).get("category") == category]
    else:
        favourites = (getattr(state, "user_categories", {}).get(str(request.get("user_id", "")), [])
                      if cold_favourites else [])  # v3: OFF until measured (the validated v2 doesn't have it)
        if favourites:  # their most active category (8x vs another one); the 2nd only as backup
            by_key = _by_category(state)
            cand = [pid for c in favourites[:2] for pid in by_key.get(c, []) if pid in disp]
        else:
            cand = [pid for pid in _best_cold(state, model, n_cold) if pid in disp]
    cand = [pid for pid in dict.fromkeys(cand) if pid not in already]
    s = signals(cand, viewed, cat, model.lc, model.rank) @ model.w
    if not viewed and favourites:
        cat_order = {c: k for k, c in enumerate(favourites[:2])}
        key_names = [(cat_order.get(cat.get(pid, {}).get("category"), 9), -s[i]) for i, pid in enumerate(cand)]
        ordering = sorted(range(len(cand)), key=lambda i: key_names[i])
    else:
        ordering = np.argsort(-s, kind="stable")
    chosen = [cand[i] for i in ordering[:10]]
    if len(chosen) < 10:  # filler: all-time best sellers (not scored against the category ones)
        chosen += [pid for pid in state.popular
                   if pid in disp and pid not in already and pid not in chosen][:10 - len(chosen)]
    return chosen


_cache: dict = {}


def _by_category(state) -> dict[str, list[str]]:
    key_name = ("cat", id(state.catalog_info))
    if key_name not in _cache:
        by_key: dict[str, list[str]] = defaultdict(list)
        for pid, d in state.catalog_info.items():
            by_key[d.get("category")].append(pid)
        _cache.clear()
        _cache[key_name] = dict(by_key)
    return _cache[key_name]


def _best_cold(state, model: Model, n: int) -> list[str]:
    key_name = ("cold", id(state.catalog_info), id(model))
    if key_name not in _cache:
        all_items = list(state.catalog_info)
        s = signals(all_items, [], state.catalog_info, model.lc, model.rank) @ model.w
        _cache[key_name] = [all_items[i] for i in np.argsort(-s, kind="stable")[:n]]
    return _cache[key_name]


# ---------------------------------------------------------------- main
def download() -> None:
    from src.rawtree import query
    ses = query("""SELECT ts::String ts, user_id::String u, shown, purchased::String p, recent_views rv,
      `context.device`::String dev, `context.page`::String page, revenue_eur::Float64 rev
      FROM shop.history_sessions WHERE purchased::String != ''""", timeout=900)
    imp = query(f"""SELECT pid, pos, (t < '{CUTOFF}') before, count() n FROM (SELECT arrayJoin(arrayEnumerate(shown)) pos,
      shown[pos]::String pid, ts::DateTime64(3) t FROM shop.history_sessions) GROUP BY pid, pos, before""", timeout=900)
    purchases = query("""SELECT user_id::String u, product_id::String p, min(ts::DateTime64(3))::String t
      FROM shop.history_events WHERE event_type='purchase' GROUP BY u, p""", timeout=900)
    DATA.mkdir(exist_ok=True)
    (DATA / "choice.pkl").write_bytes(pickle.dumps({"ses": ses, "imp": imp, "purchases": purchases}))
    print(f"downloaded: {len(ses)} carousel purchases, {len(imp)} impression rows, {len(purchases)} first purchases")


def main() -> None:
    import sys
    from src.catalog import load_catalog

    if "--download" in sys.argv or not (DATA / "choice.pkl").exists():
        download()

    d = pickle.loads((DATA / "choice.pkl").read_bytes())  # written by download() above
    ses, imp, firsts = d["ses"], d["imp"], d["purchases"]
    first = {(f["u"], f["p"]): f["t"] for f in firsts}
    cat = load_catalog()
    ses = [s for s in ses if s["p"] in s["shown"]]
    fresh = [s for s in ses if not _is_repurchase(s, first)]
    print(f"carousel purchases: {len(ses)}, without repurchases: {len(fresh)} ({len(fresh)/len(ses):.0%})")

    # validation: pieces with data < CUTOFF; train [TRAIN_START, CUTOFF); test >= CUTOFF
    purchases, expected = qualities(imp, ses, before=True)
    rank = popularity_ranks(firsts, CUTOFF)
    training = [s for s in fresh if TRAIN_START <= s["ts"] < CUTOFF]
    test = [s for s in fresh if s["ts"] >= CUTOFF]
    f_train = prepare(training, cat, purchases, expected, rank, with_slot=True, leave_one_out=True)
    f_test = prepare(test, cat, purchases, expected, rank, with_slot=True, leave_one_out=False)
    print(f"train {len(f_train)} purchases, test {len(f_test)} purchases\n")

    k = len(SIGNALS)
    slots = list(range(k, k + len(SLOTS)))
    idx = {n: i for i, n in enumerate(SIGNALS)}
    compare = {
        "position only": slots,
        "v0 (popularity + same category)": [idx["popularity"], idx["same_category"]] + slots,
        "e-samen-v2 (all signals)": None,
    }
    results = {}
    for name, cols in compare.items():
        w = fit(f_train, cols)
        results[name] = check(f_test, w, cols)
        print(f"{name:<38} {results[name]}")
        if cols is None:
            w_val = w

    print("\nsignal                    coef.   x odds")
    for n, c in zip(SIGNALS + SLOTS, w_val):
        print(f"{n:<24} {c:7.3f} {math.exp(c):7.2f}")

    # final model for live: pieces with ALL the history, trained on every non-repurchase purchase
    purchases, expected = qualities(imp, ses, before=None)
    rank = popularity_ranks(firsts, None)
    f_all = prepare(fresh, cat, purchases, expected, rank, with_slot=True, leave_one_out=True)
    w = fit(f_all)[: len(SIGNALS)]  # without the slots: when recommending we value the product
    lc = {pid: log_quality(pid, purchases, expected) for pid in set(expected) | set(purchases)}
    lc_new = math.log(typical_quality(purchases, expected))
    lc.update({pid: lc_new for pid in cat if pid not in lc})  # products with no history: typical quality
    (DATA / "choice.json").write_text(json.dumps({"w": w.tolist(), "signals": SIGNALS, "lc": lc, "rank": rank,
                                                  "validation": results}), encoding="utf-8")
    print(f"\nsaved data/choice.json ({len(lc)} products with a quality)")


if __name__ == "__main__":
    main()
