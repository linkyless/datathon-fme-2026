"""R5: explainable scoring with a logistic regression. Owner: Ranking.

    python -m src.scoring          # trains (or reads what's already in data/), prints coefficients and validation

Instead of adding points "by eye", a small model learns how much each signal is worth for a product SHOWN in the
carousel to get bought. It's a single neuron with a sigmoid:  P(purchase) = 1 / (1 + e^-(b + w1*x1 + w2*x2 + ...)).

- Data: visits to the old carousel (shop.history_sessions) between START and CUTOFF. Each visit gives 10 rows, one
  per product shown; label = 1 if they bought that product.
- The signals (features) also exist for NEW products and shoppers: product_id is never used.
- The pieces (co-visits, bought, best sellers) are computed only with data BEFORE START: that way the model learns
  with the same information it would have live and never looks at the future.
- Old-carousel bias: it mostly showed popular stuff. Each row weighs 1/propensity (clipped at WEIGHT_CLIP) so that
  what was hardly ever shown counts for what it's worth (IPS, docs/research.md point 3).
- Position bias: what's on top sells more just for being on top. The model carries one signal per slot (2nd..10th)
  during training; it's dropped when recommending, because we want to know what the PRODUCT is worth.
- Validation (Ranking's own check, not the official evaluation): Evaluation's 100,000 test visits (after CUTOFF),
  with the State in data/state_<CUTOFF>.pkl.

The regression is solved with numpy (Newton's method), no new dependencies. The result (coefficients) is saved to
data/scoring.json (data/ is not committed).
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import numpy as np

from src import catalog, covisits, users
from src.state import State, by_category
from src.rawtree import query

DATA = Path(__file__).resolve().parent.parent / "data"
START, CUTOFF = "2026-10-24", "2026-11-07"
"""Trained on the two weeks before the evaluation cutoff."""
ONE_IN_WITH_PURCHASE, ONE_IN_WITHOUT_PURCHASE = 1, 10
"""Purchases are rare (6,545 out of 466,000 visits in those two weeks, ~1.4 %): we take ALL visits with a purchase
and 1 in 10 without. To compensate, each visit without a purchase weighs 10 times more."""
WEIGHT_CLIP = 50.0
L2 = 1.0
"""Brake so that no coefficient blows up (regularisation)."""
DECAY = 0.7

SIGNALS = [
    ("log_price", "log of the price"),
    ("same_category", "same category as the last viewed"),
    ("same_subcategory", "same subcategory as the last viewed"),
    ("same_brand", "same brand as something viewed"),
    ("revisit", "already viewed in this session"),
    ("covisit", "log(1 + co-visit points with what was viewed)"),
    ("popularity", "-log(1 + rank among the best sellers)"),
    ("category_popularity", "-log(1 + rank among its category's best sellers)"),
    ("price_distance", "|log price - mean log price of what was viewed|"),
    ("cold_x_popularity", "popularity, only if there are no views (cold shopper)"),
    ("mobile_x_log_price", "log price, only when coming from mobile (ios/android)"),
]
NAMES = [n for n, _ in SIGNALS]
SLOTS = [f"slot_{i}" for i in range(2, 11)]


# ---------------------------------------------------------------- signals for each candidate
class _Indices:
    """Rank of each product in the best-seller lists (computed once per State)."""

    def __init__(self, state: State):
        self.global_ = {pid: i for i, pid in enumerate(state.popular)}
        self.category = {pid: i for items_list in state.popular_by_category.values()
                         for i, pid in enumerate(items_list)}
        self.n = len(state.popular) + 1


_cache: dict[int, _Indices] = {}


def _indices(state: State) -> _Indices:
    if id(state) not in _cache:
        _cache.clear()
        _cache[id(state)] = _Indices(state)
    return _cache[id(state)]


def _log_price(payload: dict) -> float:
    try:
        return math.log(max(float(payload.get("price_eur") or 0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def signals(request: dict, candidates: list[str], state: State) -> np.ndarray:
    """One row per candidate with the SIGNALS (no slots). Only reads `state`, like recommend()."""
    cat = state.catalog_info
    idx = _indices(state)
    viewed = [v["product_id"] for v in (request.get("recent_views") or []) if v.get("product_id")]
    last = cat.get(viewed[-1], {}) if viewed else {}
    brands = {cat.get(v, {}).get("brand") for v in viewed} - {None, ""}
    prices = [_log_price(cat.get(v, {})) for v in viewed]
    avg_price = sum(prices) / len(prices) if prices else 0.0
    ctx = request.get("context") or {}
    mobile = float(str(ctx.get("device", "")) in ("ios", "android"))
    cold = float(not viewed)

    covis: dict[str, float] = {}
    for k, pid in enumerate(reversed(viewed)):
        for pos, (other, weight) in enumerate(state.covisits.get(pid, [])):
            covis[other] = covis.get(other, 0.0) + (DECAY ** k) * weight / (pos + 1)

    viewed_set = set(viewed)
    rows = []
    for c in candidates:
        d = cat.get(c, {})
        lp = _log_price(d)
        pop = -math.log1p(idx.global_.get(c, idx.n))
        rows.append([
            lp,
            float(bool(viewed) and d.get("category") == last.get("category")),
            float(bool(viewed) and d.get("subcategory") == last.get("subcategory")),
            float(d.get("brand") in brands),
            float(c in viewed_set),
            math.log1p(covis.get(c, 0.0)),
            pop,
            -math.log1p(idx.category.get(c, idx.n)),
            abs(lp - avg_price) if prices else 0.0,
            cold * pop,
            mobile * lp,
        ])
    return np.asarray(rows, dtype=np.float64)


def _slots(n_rows: int) -> np.ndarray:
    """Slot signal (2nd..10th) for the 10 rows of a visit; the 1st is the reference (all zeros)."""
    m = np.zeros((n_rows, len(SLOTS)))
    for i in range(1, min(n_rows, 10)):
        m[i, i - 1] = 1.0
    return m


# ---------------------------------------------------------------- using the model (called by ranking.recommend)
_model: dict | None = None
_loaded_model = False


def saved_model() -> dict | None:
    """Coefficients from data/scoring.json, read ONCE (on the first call). None if the file doesn't exist: then
    recommend() carries on without a model. To create it: python -m src.scoring."""
    global _model, _loaded_model
    if not _loaded_model:
        _loaded_model = True
        try:
            m = json.loads((DATA / "scoring.json").read_text())
            m["_mean"], m["_std"] = np.asarray(m["avg"]), np.asarray(m["deviation"])
            m["_coef"] = np.asarray(m["coefficients"])
            _model = m if m.get("signals") == NAMES else None  # different signals = old file: don't use it
        except (OSError, ValueError, KeyError):
            _model = None
        if _model is None:  # shows up in the service console: otherwise use_model would be silently ignored
            print("WARNING scoring: no valid data/scoring.json -> use_model does NOTHING. "
                  "Run: python -m src.scoring", flush=True)
    return _model


def score_candidates(request: dict, candidates: list[str], state: State, model: dict) -> np.ndarray:
    """Score of each candidate (higher = more likely to be bought). No slots: just the product.
    It's the inside of the sigmoid; ranking doesn't need to apply it."""
    return (signals(request, candidates, state) - model["_mean"]) / model["_std"] @ model["_coef"]


# ---------------------------------------------------------------- data
_SQL = """
SELECT user_id::String AS user_id, `context.device`::String AS device, recent_views, shown, propensity,
       purchased::String AS purchased
FROM shop.history_sessions
WHERE ts::DateTime64(3) >= '{start}' AND ts::DateTime64(3) < '{end_ts}'
  AND if(purchased::String != '', cityHash64(request_id::String) % {one_in_with} = 0,
                                  cityHash64(request_id::String) % {one_in_without} = 0)"""


def _training_visits() -> list[dict]:
    target_path = DATA / f"train_{START}_{CUTOFF}.pkl"
    if target_path.exists():
        return pickle.loads(target_path.read_bytes())
    print("Downloading training visits (first time only)...")
    rows = query(_SQL.format(start=START, end_ts=CUTOFF, one_in_with=ONE_IN_WITH_PURCHASE,
                             one_in_without=ONE_IN_WITHOUT_PURCHASE),
                 timeout=600)
    visits = [{"user_id": f["user_id"], "context": {"device": f["device"]}, "recent_views": f["recent_views"] or [],
               "shown": f["shown"], "propensity": f["propensity"], "purchased": f["purchased"] or None}
              for f in rows]
    DATA.mkdir(exist_ok=True)
    target_path.write_bytes(pickle.dumps(visits))
    return visits


def _training_state() -> State:
    """Pieces computed only with data before START."""
    cat = catalog.load_catalog(START)
    pop = catalog.load_popular(START)
    return State(catalog_info=cat, available_ids=catalog.available(cat), popular=pop,
                 popular_by_category=by_category(cat, pop), covisits=covisits.load_covisits(START),
                 bought=users.load_bought(START))


def _matrix(visits: list[dict], state: State, weigh: bool):
    """X (signals + slots), y (bought?), w (weight of each row) and which visit each row belongs to.

    Repurchases OUT (organisers: there are no repurchases live; in the history they're ~62 % of purchases and the
    model would learn that artefact): visits whose purchase is something the shopper already had are dropped, and
    in the rest, the rows of products they already had (live they don't get shown). Also returns how many visits
    were dropped."""
    blocks, ys, ws, grp = [], [], [], []
    discarded = 0
    for g, v in enumerate(visits):
        had = state.bought.get(str(v.get("user_id", "")), set())
        if v["purchased"] and v["purchased"] in had:
            discarded += 1
            continue
        shown = list(v["shown"])
        remaining = [i for i, p in enumerate(shown) if p not in had]
        if not remaining:
            continue
        x = np.hstack([signals(v, shown, state), _slots(len(shown))])[remaining]  # original slot is kept
        blocks.append(x)
        ys.extend(float(shown[i] == v["purchased"]) for i in remaining)
        base = 1.0 if (not weigh or v["purchased"]) else ONE_IN_WITHOUT_PURCHASE / ONE_IN_WITH_PURCHASE
        ws.extend(base * (min(1.0 / max(float(v["propensity"][i]), 1e-9), WEIGHT_CLIP) if weigh else 1.0)
                  for i in remaining)
        grp.extend([g] * len(remaining))
    return np.vstack(blocks), np.asarray(ys), np.asarray(ws), np.asarray(grp), discarded


# ---------------------------------------------------------------- the regression (Newton with numpy)
def fit(X: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = L2, iterations: int = 30) -> np.ndarray:
    """Weighted logistic regression. Returns [b, w1, w2, ...]. Newton: at each step the loss is approximated by a
    parabola and we jump to its minimum; with ~20 signals it converges in a few rounds."""
    X1 = np.hstack([np.ones((len(X), 1)), X])
    beta = np.zeros(X1.shape[1])
    brake = np.full(X1.shape[1], l2)
    brake[0] = 0.0  # no brake on the intercept
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(X1 @ beta, -30, 30)))
        gradient = X1.T @ (w * (p - y)) + brake * beta
        hessian = np.zeros((X1.shape[1], X1.shape[1]))
        for i in range(0, len(X1), 500_000):  # in chunks so memory doesn't fill up
            t = X1[i:i + 500_000]
            hessian += (t * (w[i:i + 500_000] * p[i:i + 500_000] * (1 - p[i:i + 500_000]))[:, None]).T @ t
        step = np.linalg.solve(hessian + np.diag(brake), gradient)
        beta -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return beta


def auc(points: np.ndarray, y: np.ndarray) -> float:
    """Probability that a bought row scores higher than a non-bought one (0.5 = chance, 1 = perfect)."""
    ordering = np.argsort(points, kind="mergesort")
    ranks = np.empty(len(points))
    ranks[ordering] = np.arange(1, len(points) + 1)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ---------------------------------------------------------------- main
def main() -> None:
    visits = _training_visits()
    purchases = sum(bool(v["purchased"]) for v in visits)
    print(f"Training {START} -> {CUTOFF}: {len(visits)} visits ({purchases} with a purchase)")
    state = _training_state()
    X, y, w, _, outside = _matrix(visits, state, weigh=True)
    print(f"Dropped {outside} visits whose purchase was a repurchase; {int(y.sum())} purchases left to learn from")
    avg = X[:, :len(NAMES)].mean(axis=0)
    spread = X[:, :len(NAMES)].std(axis=0) + 1e-9
    Xn = X.copy()
    Xn[:, :len(NAMES)] = (X[:, :len(NAMES)] - avg) / spread  # same scale: comparable coefficients
    beta = fit(Xn, y, w)

    print(f"\n{'signal':<24}{'coef.':>8}   x odds  what it is")
    for (name, txt), b in zip(SIGNALS + [(p, "") for p in SLOTS], beta[1:]):
        print(f"{name:<24}{b:>8.3f}  {math.exp(b):>6.2f}   {txt}")

    model = {"start": START, "cutoff": CUTOFF, "signals": NAMES, "avg": avg.tolist(),
             "deviation": spread.tolist(), "intercept": float(beta[0]),
             "coefficients": beta[1:1 + len(NAMES)].tolist(), "slots": beta[1 + len(NAMES):].tolist()}
    (DATA / "scoring.json").write_text(json.dumps(model, indent=1))

    # The model's own diagnostic (comparing with other versions is Evaluation's job, src/evaluate.py):
    # AUC on visits after the cutoff, with that cutoff's State and the same repurchase filter.
    test = pickle.loads((DATA / f"test_{CUTOFF}.pkl").read_bytes())
    est = pickle.loads((DATA / f"state_{CUTOFF}.pkl").read_bytes())
    Xt, yt, _, _, outside_t = _matrix(test, est, weigh=False)
    s = (Xt[:, :len(NAMES)] - avg) / spread @ beta[1:1 + len(NAMES)]  # no slots: just the product
    print(f"\nDiagnostic {CUTOFF}+: {len(test)} visits, {outside_t} dropped as repurchases, {int(yt.sum())} purchases")
    print(f"Model AUC (no slots): {auc(s, yt):.3f}  (0.5 = chance, 1 = perfect)")


if __name__ == "__main__":
    main()
