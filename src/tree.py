"""R6: the "big model", LightGBM (trees) trained as a CHOICE within the carousel. Owner: Ranking.

    python -m src.tree      # trains, validates (top-1 hit) and saves data/tree.txt + data/tree.json

The idea, for a product manager:
- Same as e-samen-v2 (src/choice.py): on every visit with a purchase the old carousel showed 10 products and the
  shopper picked one. The model learns what makes a product BEAT its 9 carousel neighbours. Comparing only within
  what was shown avoids the old carousel's bias (it showed popular stuff).
- Difference with v2: instead of one neuron (each signal adds up on its own), hundreds of small trees that learn
  COMBINATIONS ("cheaper than what was viewed, but only if it's in their usual subcategory").
  LightGBM does it with the "lambdarank" objective: each visit is a group of 10 and the bought one should come 1st.
- The old carousel always showed 10 from the SAME category as the last viewed product: the tree can't learn
  "same category". So when recommending, candidates come from that category (as in v2).
- Repurchases are removed (they don't exist live: organisers' note).
- No looking at the future: profiles and co-visits for the training rows are computed with data before TRAIN_START;
  the test ones with data before CUTOFF. Quality is computed as in v2.
- Position goes in as a signal when training (what's on top sells more just for being on top); when recommending
  every candidate gets slot 1, so they compete on what they are.
"""
from __future__ import annotations

import json
import math
import pickle
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
CUTOFF = "2026-11-07"
TRAIN_START = "2026-10-01"
OUTAGE_START = "2026-10-31"
"""The last week of training is used to decide how many trees to keep (early stopping)."""
SMOOTHING = 5.0
DECAY = 0.7
PARAMS_LGB = {"objective": "lambdarank", "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50,
              "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0,
              "lambdarank_truncation_level": 10, "verbose": -1, "seed": 29}
"""Small trees (15 leaves) and slow learning: with ~10,000 purchases, big trees would memorise."""
MAX_TREES = 1000
N_COLD = 300
"""Shopper without views: the N_COLD best products by quality and popularity get scored."""

SIGNALS = [
    ("log_quality", "purchases / purchases expected from its slots (smoothed towards typical)"),
    ("popularity", "-log(1 + rank by distinct buyers)"),
    ("same_subcategory", "same subcategory as the last viewed"),
    ("same_brand", "same brand as the last viewed"),
    ("sub_in_views", "its subcategory appears in what was viewed"),
    ("log_price_ratio", "log(price / price of the last viewed)"),
    ("log_price", "log(price)"),
    ("revisit", "already viewed in this session"),
    ("cold", "the shopper has no views"),
    ("covisit", "log(1 + co-visit points with what was viewed)"),
    ("sub_in_profile", "its subcategory is among the shopper's top 3"),
    ("brand_in_profile", "its brand is among the shopper's top 3"),
    ("cat_in_profile", "its category is among the shopper's top 3"),
    ("log_price_ratio_usual", "log(price / median price of what the shopper buys)"),
    ("log_shopper_purchases", "log(1 + shopper's purchases)"),
    ("ips_rate", "product's purchase rate when shown, corrected by propensity (product profile)"),
    ("mobile", "comes from ios/android"),
    ("card", "on a product page (page = product)"),
    ("slot", "carousel slot (always 1 when recommending)"),
]
NAMES = [n for n, _ in SIGNALS]


# ---------------------------------------------------------------- quality (same as src/choice.py)
_MU: dict = {}


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


# ---------------------------------------------------------------- signals for the candidates of a visit
def _price(d: dict) -> float:
    try:
        return float(d.get("price_eur") or 0)
    except (TypeError, ValueError):
        return 0.0


def signals(cand: list[str], viewed: list[str], device: str, page: str, cat: dict, lc: dict[str, float],
            rank: dict[str, int], covisits: dict, profile: dict | None, product_profiles: dict,
            slots: list[int] | None = None) -> np.ndarray:
    """Matrix (len(cand), len(SIGNALS)). slots=None -> everything in slot 1 (when recommending)."""
    n_pop = len(rank) + 1
    lc_typical = lc.get("__typical__", 0.0)
    last = cat.get(viewed[-1], {}) if viewed else {}
    p_last = _price(last)
    viewed_subs = {cat.get(v, {}).get("subcategory") for v in viewed} - {None, ""}
    viewed_set = set(viewed)
    profile = profile or {}
    p_subs, p_brands, p_cats = set(profile.get("subcategories", [])), set(profile.get("brands", [])), \
        set(profile.get("categories", []))
    p_median = float(profile.get("median_price") or 0)
    log_purchases = math.log1p(float(profile.get("purchases") or 0))
    mobile = float(device in ("ios", "android"))
    card = float(page == "product")
    cold = float(not viewed)

    covis: dict[str, float] = {}
    for k, pid in enumerate(reversed(viewed)):
        for pos, (other, weight) in enumerate(covisits.get(pid, [])):
            covis[other] = covis.get(other, 0.0) + (DECAY ** k) * weight / (pos + 1)

    X = np.zeros((len(cand), len(SIGNALS)), dtype=np.float32)
    for i, pid in enumerate(cand):
        d = cat.get(pid, {})
        price = max(_price(d), 1.0)
        X[i] = (lc.get(pid, lc_typical),
                -math.log1p(rank.get(pid, n_pop)),
                float(bool(last) and d.get("subcategory") == last.get("subcategory")),
                float(bool(last) and d.get("brand") == last.get("brand")),
                float(d.get("subcategory") in viewed_subs),
                math.log(price / max(p_last, 1.0)) if viewed else 0.0,
                math.log(price),
                float(pid in viewed_set),
                cold,
                math.log1p(covis.get(pid, 0.0)),
                float(d.get("subcategory") in p_subs),
                float(d.get("brand") in p_brands),
                float(d.get("category") in p_cats),
                math.log(price / p_median) if p_median > 0 else 0.0,
                log_purchases,
                float(product_profiles.get(pid, {}).get("ips_rate", 0.0)),
                mobile,
                card,
                float(slots[i]) if slots else 1.0)
    return X


# ---------------------------------------------------------------- training data
def _viewed(s: dict) -> list[str]:
    return [v["product_id"] for v in (s["rv"] or []) if v.get("product_id")]


def _is_repurchase(s: dict, first: dict) -> bool:
    t = first.get((s["u"], s["p"]))
    return t is not None and t < s["ts"]


def _profiles(until: str) -> tuple[dict, dict]:
    """Shopper and product profiles (src/coldstart.py) with data before `until`, cached in data/ (not committed)."""
    target_path = DATA / f"profiles_{until}.pkl"
    if not target_path.exists():
        from src import coldstart
        target_path.write_bytes(pickle.dumps((coldstart._build_shopper_profile(until),
                                              coldstart._build_product_profile(until))))
    return pickle.loads(target_path.read_bytes())


FOLDS = 5
"""Out-of-fold quality for training (see _quality_per_fold)."""


def _fold(s: dict) -> int:
    return zlib.crc32(f"{s['u']}|{s['ts']}".encode()) % FOLDS  # same on every run


def _quality_per_fold(ses: list[dict], purchases: dict, expected: dict) -> list[dict[str, float]]:
    """For the training rows, each product's quality is computed WITHOUT the purchases of its own fold (1/5 of the
    visits), scaling the rest by 5/4. With "leave one out" (what v2 does) the bought product gets an exact value that
    differs from the non-bought one and a tree memorises it (measured: top-1 hit 0.112, chance level). Out of fold,
    every row in a fold shares the value: there's nothing to memorise."""
    by_fold = [Counter() for _ in range(FOLDS)]
    for s in ses:
        if s["ts"] < CUTOFF:
            by_fold[_fold(s)][s["p"]] += 1
    mu = typical_quality(purchases, expected)
    scale = FOLDS / (FOLDS - 1)
    out = []
    for k in range(FOLDS):
        outside = {pid: (purchases.get(pid, 0) - by_fold[k].get(pid, 0)) * scale
                   for pid in set(purchases) | set(expected)}
        out.append({pid: math.log((c + SMOOTHING * mu) / (expected.get(pid, 0.0) + SMOOTHING))
                    for pid, c in outside.items()})
    return out


def _matrix(sessions: list[dict], cat, purchases, expected, rank, covisits, profiles,
            lc_folds: list[dict] | None):
    """X (10 rows per visit), y (1 = the bought one) and the size of each group (visit).
    lc_folds: out-of-fold quality (training); None = plain quality (test)."""
    pc, pp = profiles
    blocks, ys, grps = [], [], []
    lc_normal = None
    for s in sessions:
        shown = list(s["shown"])
        if lc_folds is not None:
            lc = lc_folds[_fold(s)]
        else:
            lc_normal = lc_normal or {pid: log_quality(pid, purchases, expected)
                                      for pid in set(expected) | set(purchases)}
            lc = lc_normal
        blocks.append(signals(shown, _viewed(s), s.get("dev", ""), s.get("page", ""), cat, lc, rank, covisits,
                              pc.get(s["u"]), pp, slots=list(range(1, len(shown) + 1))))
        ys.extend(float(pid == s["p"]) for pid in shown)
        grps.append(len(shown))
    return np.vstack(blocks), np.asarray(ys), grps


def hit(points: np.ndarray, y: np.ndarray, grps: list[int]) -> tuple[float, float]:
    """(top-1 hit, top-3 hit): is the bought one the highest-scored of its visit?"""
    a1 = a3 = 0
    i = 0
    for g in grps:
        ordering = np.argsort(-points[i:i + g], kind="stable")
        was_bought = int(np.argmax(y[i:i + g]))
        a1 += ordering[0] == was_bought
        a3 += was_bought in ordering[:3]
        i += g
    return a1 / len(grps), a3 / len(grps)


# ---------------------------------------------------------------- using the model (called by ranking.recommend)
_model: dict | None = None
_loaded = False


def saved_model() -> dict | None:
    """data/tree.txt + data/tree.json, read ONCE. None (and a console warning) if missing or lightgbm isn't there."""
    global _model, _loaded
    if not _loaded:
        _loaded = True
        try:
            import lightgbm as lgb
            meta = json.loads((DATA / "tree.json").read_text(encoding="utf-8"))
            if meta.get("signals") != NAMES:
                raise ValueError("tree.json from another version")
            _model = {"booster": lgb.Booster(model_file=str(DATA / "tree.txt")), "lc": meta["lc"],
                      "rank": meta["rank"]}
        except (ImportError, OSError, ValueError, KeyError) as e:
            _model = None
            print(f"WARNING tree: use_tree does NOTHING ({e}). Run: python -m src.tree", flush=True)
    return _model


_cache: dict = {}


def _by_category(state) -> dict[str, list[str]]:
    key_name = ("cat", id(state.catalog_info))
    if key_name not in _cache:
        by_key: dict[str, list[str]] = defaultdict(list)
        for pid, d in state.catalog_info.items():
            by_key[d.get("category")].append(pid)
        _cache[key_name] = dict(by_key)
    return _cache[key_name]


def _best_cold(state, model: dict) -> list[str]:
    """For shoppers without views: the N_COLD best products by quality + popularity (no profile)."""
    key_name = ("cold", id(state.catalog_info), id(model))
    if key_name not in _cache:
        lc, rank = model["lc"], model["rank"]
        tip, n = lc.get("__typical__", 0.0), len(rank) + 1
        all_items = list(state.catalog_info)
        all_items.sort(key=lambda p: lc.get(p, tip) - 0.5 * math.log1p(rank.get(p, n)), reverse=True)
        _cache[key_name] = all_items[:N_COLD]
    return _cache[key_name]


def candidates(request: dict, state, model: dict, is_ok) -> list[str]:
    """With views: everything available in the category of the last viewed product, plus what was viewed from it.
    Without views: the N_COLD best by quality and popularity. `is_ok` = available (and not bought if filtering)."""
    cat = state.catalog_info
    viewed = [v["product_id"] for v in (request.get("recent_views") or []) if v.get("product_id")]
    if viewed:
        category = cat.get(viewed[-1], {}).get("category")
        cand = _by_category(state).get(category, []) + \
            [pid for pid in reversed(viewed) if cat.get(pid, {}).get("category") == category]
    else:
        cand = _best_cold(state, model)
    return [pid for pid in dict.fromkeys(cand) if is_ok(pid)]


def score_candidates(request: dict, cand: list[str], state, model: dict) -> np.ndarray:
    ctx = request.get("context") or {}
    viewed = [v["product_id"] for v in (request.get("recent_views") or []) if v.get("product_id")]
    profile = getattr(state, "shopper_profiles", {}).get(str(request.get("user_id", "")))
    X = signals(cand, viewed, str(ctx.get("device", "")), str(ctx.get("page", "")), state.catalog_info,
                model["lc"], model["rank"], state.covisits, profile, getattr(state, "product_profiles", {}))
    return model["booster"].predict(X, num_threads=1)


# ---------------------------------------------------------------- main
def main() -> None:
    import lightgbm as lgb

    from src.catalog import load_catalog
    from src.covisits import load_covisits

    target_path = DATA / "choice.pkl"
    if not target_path.exists():  # same data as v2: python -m src.choice --download
        from src.choice import download
        download()
    d = pickle.loads(target_path.read_bytes())
    ses, imp, firsts = d["ses"], d["imp"], d["purchases"]
    first = {(f["u"], f["p"]): f["t"] for f in firsts}
    cat = load_catalog()
    ses = [s for s in ses if s["p"] in s["shown"]]
    fresh = [s for s in ses if not _is_repurchase(s, first)]
    print(f"carousel purchases: {len(ses)}, without repurchases: {len(fresh)}")

    purchases, expected = qualities(imp, ses, before=True)
    rank = {pid: i for i, (pid, _) in enumerate(Counter(f["p"] for f in firsts if f["t"] < CUTOFF).most_common())}
    training = [s for s in fresh if TRAIN_START <= s["ts"] < CUTOFF]
    test = [s for s in fresh if s["ts"] >= CUTOFF]
    print(f"train {len(training)} purchases ({TRAIN_START} -> {CUTOFF}), test {len(test)} purchases (>= {CUTOFF})")

    cov_train, per_train = load_covisits(TRAIN_START), _profiles(TRAIN_START)
    cov_test, per_test = load_covisits(CUTOFF), _profiles(CUTOFF)
    lc_folds = _quality_per_fold(ses, purchases, expected)
    X, y, g = _matrix(training, cat, purchases, expected, rank, cov_train, per_train, lc_folds)
    Xt, yt, gt = _matrix(test, cat, purchases, expected, rank, cov_test, per_test, None)

    # 1) How many trees? Train up to OUTAGE_START and stop when the last week stops improving.
    n_a = sum(1 for s in training if s["ts"] < OUTAGE_START)
    rows_a = sum(g[:n_a])
    outage = lgb.train({**PARAMS_LGB, "metric": "ndcg", "eval_at": [1]},
                       lgb.Dataset(X[:rows_a], y[:rows_a], group=g[:n_a]), MAX_TREES,
                       valid_sets=[lgb.Dataset(X[rows_a:], y[rows_a:], group=g[n_a:])],
                       callbacks=[lgb.early_stopping(50, verbose=False)])
    n_trees = max(outage.best_iteration, 50)
    # 2) Final model: the whole training set with that number of trees.
    model = lgb.train(PARAMS_LGB, lgb.Dataset(X, y, group=g), n_trees)

    print(f"\ntrees: {n_trees}")
    a1, a3 = hit(model.predict(Xt), yt, gt)
    Xt1 = Xt.copy()
    Xt1[:, NAMES.index("slot")] = 1.0
    b1, b3 = hit(model.predict(Xt1), yt, gt)
    print(f"test (with its real slot, like v2):     top-1 hit {a1:.3f}, top-3 hit {a3:.3f}")
    print(f"test (everything in slot 1, like live): top-1 hit {b1:.3f}, top-3 hit {b3:.3f}")
    print("(chance: 0.100 and 0.300)")

    imp_gain = model.feature_importance("gain")
    print(f"\n{'signal':<28}{'% of the gain':>18}   what it is")
    for (name, txt), v in sorted(zip(SIGNALS, imp_gain), key=lambda t: -t[1]):
        print(f"{name:<28}{100 * v / imp_gain.sum():>17.1f}%   {txt}")

    # for live: same validated model; quality and popularity with ALL the history
    purchases_t, expected_t = qualities(imp, ses, before=None)
    lc = {pid: log_quality(pid, purchases_t, expected_t) for pid in set(expected_t) | set(purchases_t)}
    lc["__typical__"] = math.log(typical_quality(purchases_t, expected_t))  # new products: typical quality
    rank_t = {pid: i for i, (pid, _) in enumerate(Counter(f["p"] for f in firsts).most_common())}
    model.save_model(str(DATA / "tree.txt"))
    (DATA / "tree.json").write_text(json.dumps({"signals": NAMES, "lc": lc, "rank": rank_t, "trees": n_trees,
                                                "validation": {"hit_1_real_slot": a1, "hit_3_real_slot": a3,
                                                               "hit_1_slot_1": b1, "hit_3_slot_1": b3}}),
                                    encoding="utf-8")
    print(f"\nsaved data/tree.txt and data/tree.json ({len(lc)} products with a quality)")


if __name__ == "__main__":
    main()
