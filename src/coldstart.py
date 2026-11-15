"""Cold start: new shoppers and new products. Owner: Data.

D3 and D4 of the data role guide (docs/archive/guides/data.md). The embeddings themselves come from src/embeddings.py.
"""
from __future__ import annotations

import base64
import json
import time

import numpy as np

from src.catalog import load_popular
from src.embeddings import MAX_TEXTS, PAUSE_S, request_embeddings, product_text
from src.rawtree import insert, query

NEIGHBOURS = 20

# Mixing in popular_by_context (Evaluation's measurement on Sat: context alone sank the cold hit rate from 0.60 to
# 0.46): the first GLOBALS_FIRST items are the all-time best sellers (the known good ones) and the rest of the list is
# specific to the (device, region). That way the filler can never do worse than the general best sellers, and the
# context part keeps its signal for the tail.
GLOBALS_FIRST = 20


def load_context_popular(until: str | None = None) -> dict[tuple[str, str], list[str]]:
    """(device, region) -> list of 50 per kind of shopper, most to least (mixed with the general best sellers).

    `until` ('YYYY-MM-DD'): only use data with ts < until. None = everything (live).
    """
    where_extra = f" AND ts::DateTime64(3) < '{until}'" if until else ""
    # Consistency note with covisits.py: here every purchase EVENT counts (a session that buys twice adds 2);
    # covisits counts EACH SESSION ONCE (highest weight). They're different signals on purpose: "how many purchases
    # from that kind of shopper" vs "in how many sessions they're related".
    rows = query(
        "SELECT device::String d, region::String r, product_id::String pid, count() c "
        "FROM shop.history_events "
        f"WHERE event_type = 'purchase'{where_extra} "
        "GROUP BY d, r, pid "
        "ORDER BY d, r, c DESC, pid "          # pid breaks ties: stable order
        "LIMIT 50 BY d, r"
    )
    by_context: dict[tuple[str, str], list[str]] = {}
    for f in rows:
        by_context.setdefault((f["d"], f["r"]), []).append(f["pid"])

    # Safe mix: the GLOBALS_FIRST all-time best sellers first, then the context-specific ones, and fill up to 50
    # with more global ones.
    global_best = load_popular(until)
    out: dict[tuple[str, str], list[str]] = {}
    for pair, theirs in by_context.items():
        viewed: set[str] = set()
        mixed = global_best[:GLOBALS_FIRST]
        viewed.update(mixed)
        mixed += [p for p in theirs if p not in viewed and not viewed.add(p)]
        mixed += [p for p in global_best if p not in viewed and not viewed.add(p)]
        out[pair] = mixed[:50]
    return out


_ATTRIBUTES_CACHE: tuple[float, dict[str, tuple[str, str]]] | None = None
_VECTORS_CACHE: tuple[float, list[str], np.ndarray] | None = None


def _attributes() -> dict[str, tuple[str, str]]:
    """product_id -> (category, subcategory) from the catalog, cached for 10 min."""
    global _ATTRIBUTES_CACHE
    if _ATTRIBUTES_CACHE and time.time() - _ATTRIBUTES_CACHE[0] < 600:
        return _ATTRIBUTES_CACHE[1]
    rows = query("SELECT product_id::String pid, category::String c, subcategory::String s FROM shop.catalog")
    attributes = {f["pid"]: (f["c"], f["s"]) for f in rows}
    _ATTRIBUTES_CACHE = (time.time(), attributes)
    return attributes


def _vectors(until: str | None, cache_s: int = 120) -> tuple[list[str], np.ndarray]:
    """Normalised vectors from scratch (ids in the same order as the matrix rows).

    Used by load_neighbours and load_legacy_covisits: within one State refresh they're decoded only once (short
    cache). `until` cuts by created_at.
    """
    global _VECTORS_CACHE
    if _VECTORS_CACHE and time.time() - _VECTORS_CACHE[0] < cache_s:
        return _VECTORS_CACHE[1], _VECTORS_CACHE[2]
    rows = query("SELECT product_id::String pid, vec FROM team_29.scratch WHERE kind = 'emb_v1'")
    decoded = {
        f["pid"]: np.frombuffer(base64.b64decode(f["vec"]), dtype=np.float16).astype(np.float32)
        for f in rows
        if f["pid"] and f["vec"]  # skip broken rows (e.g. empty product_id) just in case
    }
    if until:  # drop products created after the cutoff (created_at < until)
        created = {
            f["product_id"]
            for f in query(f"SELECT product_id FROM shop.catalog WHERE created_at < '{until}'")
        }
        decoded = {pid: v for pid, v in decoded.items() if pid in created}
    ids = list(decoded)
    matrix = np.vstack([decoded[p] for p in ids]) if ids else np.zeros((0, 1), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    _VECTORS_CACHE = (time.time(), ids, matrix)
    return ids, matrix


def _detailed_neighbours(
    until: str | None = None,
    ids: list[str] | None = None,
    matrix: np.ndarray | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """product_id -> [(neighbour, similarity), ...]: the best 20 WITHIN its subcategory.

    If its subcategory has fewer than 20, it fills up with its category (what D4 asks for).
    Similarity = dot product (the vectors are normalised).
    """
    if ids is None or matrix is None:
        ids, matrix = _vectors(until)
    n = len(ids)
    if not n:
        return {}
    attributes = _attributes()
    sub_of = [attributes.get(p, ("", ""))[1] for p in ids]
    cat_of = [attributes.get(p, ("", ""))[0] for p in ids]

    out: dict[str, list[tuple[str, float]]] = {}
    incomplete: list[int] = []

    # step 1: the best ones within the product's own subcategory
    by_sub: dict[str, list[int]] = {}
    for i in range(n):
        by_sub.setdefault(sub_of[i], []).append(i)
    for grp in by_sub.values():
        block = matrix[grp] @ matrix[grp].T
        for row, i_global in enumerate(grp):
            pairs: list[tuple[int, float]] = []
            for j in np.argsort(-block[row]):
                j = int(j)
                if j == row:  # skip itself
                    continue
                pairs.append((grp[j], float(block[row, j])))
                if len(pairs) == NEIGHBOURS:
                    break
            out[ids[i_global]] = [(ids[a], s) for a, s in pairs]
            if len(pairs) < NEIGHBOURS:
                incomplete.append(i_global)

    # step 2: whoever didn't reach 20 neighbours gets filled from the same category
    if incomplete:
        by_cat: dict[str, list[int]] = {}
        for i in range(n):
            by_cat.setdefault(cat_of[i], []).append(i)
        blocks: dict[str, np.ndarray] = {}  # one similarity block per category
        for i in incomplete:
            already = out[ids[i]]
            if len(already) >= NEIGHBOURS:
                continue
            grp = by_cat[cat_of[i]]
            if len(grp) - 1 <= len(already):  # not even the whole category gets to 20
                continue
            key_name = cat_of[i]
            if key_name not in blocks:
                blocks[key_name] = matrix[grp] @ matrix[grp].T
            block = blocks[key_name]
            row = grp.index(i)
            slots = {a for a, _ in already} | {ids[i]}
            pairs: list[tuple[str, float]] = []
            for j in np.argsort(-block[row]):
                j = int(j)
                candidate = ids[grp[j]]
                if candidate in slots:
                    continue
                pairs.append((candidate, float(block[row, j])))
                if len(already) + len(pairs) == NEIGHBOURS:
                    break
            out[ids[i]] = already + pairs
    return out


def load_neighbours(until: str | None = None) -> dict[str, list[str]]:
    """product_id -> the 20 products with the most similar description (by embeddings), most similar first.

    Neighbours WITHIN the product's subcategory (topped up from the category if needed); computed by
    `_detailed_neighbours` from team_NN.scratch. `until` ('YYYY-MM-DD') cuts by created_at so that offline
    evaluation doesn't see future products.
    """
    return {pid: [v for v, _ in pairs] for pid, pairs in _detailed_neighbours(until).items()}


def load_legacy_covisits(
    until: str | None = None,
    covisits: dict[str, list[tuple[str, float]]] | None = None,
    neighbours_det: dict[str, list[tuple[str, float]]] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """"Borrowed" co-visits for products that don't have any yet (D6, research point 8).

    For every product WITHOUT a row in `covisits`: the similarity-weighted average of its neighbours' co-visits
    (the ones from `load_neighbours`), cut to 20 sorted pairs. Returns a dict with the SAME shape as `covisits`
    (product_id -> [(other, weight), ...]), ready to be merged.
    """
    cov = covisits or {}
    if not cov:
        return {}
    det = neighbours_det if neighbours_det is not None else _detailed_neighbours(until)
    legacy: dict[str, list[tuple[str, float]]] = {}
    for pid, pairs in det.items():
        if pid in cov:
            continue  # already has real co-visits: leave it alone
        rows = [(v, s) for v, s in pairs if v in cov]  # only neighbours with known co-visits
        if not rows:
            continue
        total = sum(s for _, s in rows)
        if total <= 0:
            continue
        points: dict[str, float] = {}
        for v, s in rows:
            for other, weight in cov[v]:
                points[other] = points.get(other, 0.0) + s * float(weight)
        ordering = sorted(((o, w / total) for o, w in points.items()), key=lambda t: -t[1])[:NEIGHBOURS]
        legacy[pid] = [(o, round(w, 4)) for o, w in ordering if w > 0]
    return legacy


# ---------- Profiles for R6 (requested by Ranking, Sat ~18:50) ----------
# (1) shopper profile: top 3 categories/subcategories/brands and median price per shopper
#     (history_events + live shop.events). (2) product profile: times shown, times bought and propensity-corrected
#     purchase rate, from history_sessions with arrayJoin.
# Stored in team_29.scratch with a version stamp (`gen`) and a cutoff (`until`), and read with load_profiles(until),
# which only returns rows of the same cutoff (respecting `until` = never looking at the future).

SHOPPER_PROFILE = "shopper_profile_v2"
PRODUCT_PROFILE = "product_profile_v2"
# v1 of these kinds got CORRUPTED in scratch (top-3 reduced to characters, Sat ~18:00) and can't be deleted without
# admin rights: moving to v2 makes the loader ignore every v1 row automatically.
PROFILE_GEN_MIN = 1800  # seconds: don't rebuild if the latest thing in scratch is more recent than this


def _live_union(until: str | None) -> str:
    """shop.events only counts LIVE (until=None): with a cutoff it'd be future data.

    Events don't carry the category: the catalog one is joined here too.
    """
    if until:
        return ""
    return ("UNION ALL SELECT e2.user_id::String AS u, c2.c AS c, c2.s AS s, c2.b AS b, "
            "e2.price_eur::Float64 AS pr "
            "FROM shop.events e2 "
            "INNER JOIN (SELECT product_id::String pid, category::String c, "
            "subcategory::String s, brand::String b FROM shop.catalog) c2 "
            "ON e2.product_id::String = c2.pid "
            "WHERE e2.event_type::String = 'purchase'")


def _empty_profile() -> dict:
    return {"categories": [], "subcategories": [], "brands": [], "median_price": 0.0, "purchases": 0}


def _build_shopper_profile(until: str | None) -> dict[str, dict]:
    """user_id -> {"categories": [3], "subcategories": [3], "brands": [3], "median_price": EUR, "purchases": n}.

    Top 3 per dimension with LIMIT 3 BY (tested; the groupArray-of-tuples tricks come back flattened through the
    Dynamic path and break the profile).
    """
    where_extra = f" WHERE e.event_type::String = 'purchase' AND e.ts::DateTime64(3) < '{until}'" if until \
        else " WHERE e.event_type::String = 'purchase'"
    union = _live_union(until)
    dim_map = (("c", "categories"), ("s", "subcategories"), ("b", "brands"))
    top: dict[str, dict] = {}
    for dim, key_name in dim_map:
        sql = f"""
        SELECT u, v, n FROM (
          SELECT u, {dim} AS v, count() AS n FROM (
            SELECT e.user_id::String AS u, c.c AS c, c.s AS s, c.b AS b, e.price_eur::Float64 AS pr
            FROM shop.history_events e
            INNER JOIN (SELECT product_id::String pid, category::String c,
                               subcategory::String s, brand::String b FROM shop.catalog) c
                      ON e.product_id::String = c.pid
            {where_extra}
            {union}
          ) GROUP BY u, v ORDER BY n DESC
        ) LIMIT 3 BY u"""
        for f in query(sql, timeout=600):
            d = top.setdefault(f["u"], _empty_profile())
            d[key_name].append(str(f["v"]))  # rows already come sorted by purchases per shopper
    rows_med = query(f"""
        SELECT u, round(quantile(0.5)(pr), 2) AS med, count() AS purchases FROM (
          SELECT e.user_id::String AS u, c.c AS c, c.s AS s, c.b AS b, e.price_eur::Float64 AS pr
          FROM shop.history_events e
          INNER JOIN (SELECT product_id::String pid, category::String c,
                             subcategory::String s, brand::String b FROM shop.catalog) c
                    ON e.product_id::String = c.pid
          {where_extra}
          {union}
        ) GROUP BY u""", timeout=600)
    for f in rows_med:
        d = top.setdefault(f["u"], _empty_profile())
        d["median_price"] = float(f["med"] or 0)
        d["purchases"] = int(f["purchases"])
    return top


def _build_product_profile(until: str | None) -> dict[str, dict]:
    """product_id -> {"shown_n": n, "was_bought": n, "ips_rate": propensity-corrected rate}."""
    where_extra = f" WHERE ts::DateTime64(3) < '{until}'" if until else ""
    sql = f"""
    SELECT p, count() AS shown_n, countIf(p = pc) AS was_bought,
           round(if(sum(w) > 0, sum(wc) / sum(w), 0), 4) AS ips_rate
    FROM (
      SELECT z.1 AS p, purchased::String AS pc,
             if(z.2::Float64 > 0, 1.0 / z.2::Float64, 0) AS w,
             if(z.2::Float64 > 0 AND z.1 = pc, 1.0 / z.2::Float64, 0) AS wc
      FROM (SELECT arrayJoin(arrayZip(shown, propensity)) AS z, purchased::String AS purchased
            FROM shop.history_sessions{where_extra})
    )
    GROUP BY p"""
    return {f["p"]: {"shown_n": int(f["shown_n"]), "was_bought": int(f["was_bought"]),
                     "ips_rate": float(f["ips_rate"])} for f in query(sql, timeout=600)}


def build_profiles(until: str | None = None, force: bool = False) -> tuple[dict, dict]:
    """Computes the R6 profiles, uploads them to team_29.scratch and returns them.

    Every row carries `gen` (version stamp) and `until` (the cutoff: "LIVE" if None): the loader only reads rows of
    its own cutoff. If the last upload is less than PROFILE_GEN_MIN seconds old it doesn't recompute (so scratch
    doesn't grow out of control) unless `force=True`.
    """
    h = until or "LIVE"
    try:
        last = query(f"SELECT max(gen) AS g FROM team_29.scratch "
                     f"WHERE kind = '{SHOPPER_PROFILE}' AND until::String = '{h}'")[0]["g"] or 0
    except RuntimeError:  # scratch doesn't have these columns yet (first time)
        last = 0
    if not force and time.time() - last < PROFILE_GEN_MIN:
        return load_profiles(until)

    gen = int(time.time())
    shopper = _build_shopper_profile(until)
    product = _build_product_profile(until)
    rows = [{"kind": SHOPPER_PROFILE, "until": h, "gen": gen, "user_id": u, **payload}
            for u, payload in shopper.items()]
    rows += [{"kind": PRODUCT_PROFILE, "until": h, "gen": gen, "product_id": p, **payload}
             for p, payload in product.items()]
    for i in range(0, len(rows), 500):
        insert("scratch", rows[i : i + 500])
    print(f"profiles uploaded to scratch: {len(shopper)} shoppers + {len(product)} products (gen {gen})")
    return shopper, product


def _by_gen(rows: list[dict], key_name: str) -> dict[str, dict]:
    """Out of the scratch rows, keeps the one with the highest `gen` per key (the latest)."""
    best: dict[str, dict] = {}
    for f in rows:
        k = str(f[key_name])
        if k not in best or int(f.get("gen") or 0) > int(best[k].get("gen") or 0):
            best[k] = f
    return best


def _as_list(v) -> list[str]:
    """Dynamic columns sometimes return the list as is and sometimes as JSON text."""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.startswith("["):
        return [str(x) for x in json.loads(v)]
    return []


def load_profiles(until: str | None = None) -> tuple[dict, dict]:
    """Reads the R6 profiles of THIS cutoff from team_29.scratch.

    Returns (shopper_profiles, product_profiles):
    - user_id -> {"categories": [3], "subcategories": [3], "brands": [3], "median_price": EUR, "purchases": n}
    - product_id -> {"shown_n": n, "was_bought": n, "ips_rate": corrected rate}
    If they haven't been built yet (python -m src.coldstart builds them, or the service does), returns ({}, {}).
    """
    h = until or "LIVE"  # "" in a Dynamic column behaved badly in the WHERE: explicit marker
    try:
        rows = query(f"SELECT user_id::String AS user_id, gen, categories, subcategories, brands, "
                     f"median_price, purchases FROM team_29.scratch "
                     f"WHERE kind = '{SHOPPER_PROFILE}' AND until::String = '{h}'")
    except RuntimeError:  # first time: scratch doesn't have these columns yet
        rows = []
    shopper = {u: {"categories": _as_list(f.get("categories")),
                   "subcategories": _as_list(f.get("subcategories")),
                   "brands": _as_list(f.get("brands")),
                   "median_price": float(f.get("median_price") or 0),
                   "purchases": int(f.get("purchases") or 0), "gen": int(f.get("gen") or 0)}
               for u, f in _by_gen(rows, "user_id").items()}
    try:
        rows_p = query(f"SELECT product_id::String AS product_id, gen, shown_n, was_bought, ips_rate "
                       f"FROM team_29.scratch WHERE kind = '{PRODUCT_PROFILE}' AND until::String = '{h}'")
    except RuntimeError:
        rows_p = []
    product = {p: {"shown_n": int(f.get("shown_n") or 0), "was_bought": int(f.get("was_bought") or 0),
                   "ips_rate": float(f.get("ips_rate") or 0), "gen": int(f.get("gen") or 0)}
               for p, f in _by_gen(rows_p, "product_id").items()}
    return shopper, product


def main() -> None:
    """python -m src.coldstart: builds and uploads the R6 profiles (live, until=None)."""
    shopper, product = build_profiles(None, force=True)
    print(f"shopper profiles: {len(shopper)} | product profiles: {len(product)}")


def embed_new() -> int:
    """Embeds the 'new_product's from catalog_updates that aren't in scratch yet and uploads them.

    Returns how many new products were embedded. The service calls it every ~5 minutes so that new products get
    neighbours while they're fresh.
    """
    new_ids = [
        f
        for f in query(
            "SELECT product_id::String pid, category, subcategory, brand, description, ts "
            "FROM shop.catalog_updates WHERE change = 'new_product' "
            "ORDER BY ts::DateTime64(3) ASC"  # a product can have several rows: the last one wins
        )
    ]
    last: dict[str, dict] = {}
    for f in new_ids:
        last[f["pid"]] = f
    already_in = {
        f["product_id"]
        for f in query("SELECT product_id::String product_id FROM team_29.scratch WHERE kind = 'emb_v1'")
    }
    pending = [f for pid, f in last.items() if pid and pid not in already_in]
    if not pending:
        return 0

    rows_out = []
    for i in range(0, len(pending), MAX_TEXTS):
        batch = pending[i : i + MAX_TEXTS]
        vectors = request_embeddings([product_text(f) for f in batch])
        for f, v in zip(batch, vectors):
            vec = np.asarray(v, dtype=np.float32)
            nrm = float(np.linalg.norm(vec)) or 1.0
            vec = vec / nrm
            rows_out.append((f, vec))
        if i + MAX_TEXTS < len(pending):
            time.sleep(PAUSE_S)
    insert(
        "scratch",
        [
            {
                "kind": "emb_v1",
                "product_id": f["pid"],
                "vec": base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii"),
            }
            for f, v in rows_out
        ],
    )
    return len(rows_out)


if __name__ == "__main__":
    main()
