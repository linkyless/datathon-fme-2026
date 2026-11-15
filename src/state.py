"""The team CONTRACT. Owner: Operator. If you change a field, tell all four of us.

`State` is everything `ranking.recommend()` can use to decide. It's computed OUTSIDE the live loop (heavy queries),
kept in memory and refreshed every so often. `recommend()` never runs a query: it only reads these fields.

Each field has an owner who writes the function that fills it, always with the same shape:
`load_xxx(until: str | None) -> value`. `until` ('YYYY-MM-DD') cuts the data at that date for offline evaluation
(so we never cheat by looking at the future). Live it's called with `until=None`.

Until a function is written it returns an empty value and `recommend()` keeps working with the rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src import catalog, coldstart, covisits, trends, users


@dataclass
class State:
    # --- Operator (src/catalog.py) ---
    catalog_info: dict[str, dict] = field(default_factory=dict)
    """product_id -> {"category", "subcategory", "brand", "price_eur", "is_active", ...}"""
    available_ids: set[str] = field(default_factory=set)
    """Products that can be recommended RIGHT NOW (active and in stock). Recommending anything else voids the answer."""
    popular: list[str] = field(default_factory=list)
    """All-time best sellers, most to least. It's the official fallback and the emergency filler."""
    popular_by_category: dict[str, list[str]] = field(default_factory=dict)
    """category -> best sellers of that category. It's the baseline from the opening slides (docs/opening.md).
    Computed from `catalog_info` and `popular` (no queries): see `by_category()`."""

    # --- Ranking (src/covisits.py, src/users.py) ---
    covisits: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    """product_id -> [(other_product_id, weight), ...] highest weight first. "Whoever viewed X viewed/bought Y"."""
    bought: dict[str, set[str]] = field(default_factory=dict)
    """user_id -> products they already bought (we don't show them again)."""
    user_categories: dict[str, list[str]] = field(default_factory=dict)
    """user_id -> their 3 most active categories (views + cart + purchases), most to least (src/choice.py).
    For a shopper WITHOUT views, showing their favourite category converts ~8x more (0.78 % vs 0.09 %)."""
    trends: dict[str, float] = field(default_factory=dict)
    """product_id -> lift (src/trends.py): >1 = more activity than usual in the last half hour."""

    # --- Data (src/coldstart.py) ---
    popular_by_context: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    """(device, region) -> best sellers for that kind of shopper. For new users."""
    neighbours: dict[str, list[str]] = field(default_factory=dict)
    """product_id -> products with a similar description (embeddings). For new products."""
    shopper_profiles: dict[str, dict] = field(default_factory=dict)
    """user_id -> {"categories", "subcategories", "brands", "median_price", "purchases"}: R6 profile
    (filled by coldstart.load_profiles from team_29.scratch; build_profiles uploads it)."""
    product_profiles: dict[str, dict] = field(default_factory=dict)
    """product_id -> {"shown_n", "was_bought", "ips_rate"}: purchase rate corrected by propensity (R6)."""


def by_category(cat: dict[str, dict], popular: list[str]) -> dict[str, list[str]]:
    """Splits the popular list by category, keeping the order."""
    out: dict[str, list[str]] = {}
    for pid in popular:
        category = cat.get(pid, {}).get("category")
        if category:
            out.setdefault(str(category), []).append(pid)
    return out


def build(until: str | None = None) -> State:
    """Computes the whole State. Offline: until='YYYY-MM-DD'. Live: until=None.

    R6 profiles come from team_29.scratch; if that cutoff isn't built there yet,
    run `python -m src.coldstart` first (live) or `build_profiles(until)` (offline).
    """
    cat = catalog.load_catalog(until)
    pop = catalog.load_popular(until)
    shopper_profiles, product_profiles = coldstart.load_profiles(until)
    if not shopper_profiles and not product_profiles:
        # this cutoff isn't in scratch yet: build it NOW with the same cutoff
        # (respects `until`, never looks at the future) and reload; only happens the first time.
        shopper_profiles, product_profiles = coldstart.build_profiles(until)
    return State(
        catalog_info=cat,
        available_ids=catalog.available(cat),
        popular=pop,
        popular_by_category=by_category(cat, pop),
        covisits=covisits.load_covisits(until),
        bought=users.load_bought(until),
        trends=trends.load_trends(until),
        popular_by_context=coldstart.load_context_popular(until),
        neighbours=coldstart.load_neighbours(until),
        shopper_profiles=shopper_profiles,
        product_profiles=product_profiles,
    )
