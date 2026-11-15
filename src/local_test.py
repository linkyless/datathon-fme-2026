"""Quick test WITHOUT NETWORK, to run before every commit.

    python -m src.local_test

Checks that every module imports (if someone breaks a file, it shows here) and that the AP7 path (the learner, the
rule C fallback and the service's safe response) returns valid answers on a toy shop.
"""
from __future__ import annotations

import importlib
import pkgutil
import time
from pathlib import Path

from src import learner, service
from src.validate import response_errors


def _toy_shop() -> service.Shop:
    ids = [f"p_{i:03d}" for i in range(40)]
    cat = {p: {"category": "c", "subcategory": "s", "price_eur": 10.0, "is_active": True} for p in ids}
    cat["p_001"]["category"] = "shoes"
    available = set(ids[:35])                    # p_035..p_039 out of stock
    popular = list(reversed(ids))                # the out-of-stock ones go first: they must be skipped
    return service.Shop(catalog=cat, available=available, popular=popular,
                        popular_by_category={"shoes": ["p_037", "p_030"]})   # p_037 out of stock: skipped


def _toy_model() -> learner.Model:
    weights = {"cart_today": 1.8, "viewed_session": 1.9, "aff_cat": 3.5, "same_cat": 0.8, "pop": 0.4, "rate": 1.5}
    return learner.Model(weights, -5.0, [0.0] + [-0.3] * 9)


def main() -> None:
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        importlib.import_module(f"src.{m.name}")
    shop = _toy_shop()
    world = learner.World(shop.catalog)
    world.event(1, "u_1", "p_002", learner.PURCHASE)
    world.event(2, "u_1", "p_004", learner.CART)
    model = _toy_model()
    requests = [
        {"request_id": "r1", "user_id": "u_1", "context": {"device": "mobile"},
         "recent_views": [{"product_id": "p_001", "ts": "x"}]},
        {"request_id": "r2", "user_id": "u_2", "is_new_user": True, "context": {}, "recent_views": []},
        {"request_id": "r3", "user_id": "u_3", "recent_views": [{"product_id": "p_new"}]},
        {"request_id": "r4"},                                                   # almost empty request
    ]
    for r in requests:
        start = time.perf_counter()
        items = learner.recommend(r, world, model, shop.available)
        ms = (time.perf_counter() - start) * 1000
        errs = response_errors(items, shop.available)
        assert not errs, f"{r['request_id']}: {errs}"
        assert ms < 50, f"{r['request_id']}: recommend takes {ms:.1f} ms"
        rule = service.category_best_sellers(r, shop, shop.available)
        assert not response_errors(rule, shop.available), f"{r['request_id']} (rule C): {response_errors(rule)}"
        sub, replaced = service.safe_response(r, shop)
        assert not replaced and not response_errors(sub["items"], shop.available), f"{r['request_id']}: {sub}"
    r1 = learner.recommend(requests[0], world, model, shop.available)
    assert "p_002" not in r1, "recommends something the shopper already bought"
    assert r1[0] in ("p_001", "p_004"), f"their cart or what they viewed should go first: {r1[:3]}"
    assert service.category_best_sellers(requests[0], shop, shop.available)[0] == "p_030", "rule C: category first"
    print("local_test: all OK (modules import and the AP7 path gives valid answers)")


if __name__ == "__main__":
    main()
