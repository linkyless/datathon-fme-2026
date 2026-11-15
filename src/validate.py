"""Response validator: checks that a recommendation list is valid BEFORE sending it.

An invalid response is replaced by the fallback (scores 0 for that request) and lowers reliability.

    python -m src.validate      # runs the self-tests
"""
from __future__ import annotations

from collections.abc import Iterable


def response_errors(items: object, active: Iterable[str] | None = None) -> list[str]:
    """List of problems of a response. Empty list = valid response.

    `active`: set of product_ids that are active and in stock right now. If None, stock is not checked.
    """
    if not isinstance(items, list):
        return [f"items must be a list, it is {type(items).__name__}"]
    errors = []
    if len(items) != 10:
        errors.append(f"must have 10 products, it has {len(items)}")
    not_text = [i for i in items if not isinstance(i, str) or not i]
    if not_text:
        errors.append(f"IDs that are not text or are empty: {not_text[:3]}")
    repeated = sorted({i for i in items if isinstance(i, str) and items.count(i) > 1})
    if repeated:
        errors.append(f"repeated IDs: {repeated}")
    if active is not None:
        active = set(active)
        out = [i for i in items if isinstance(i, str) and i not in active]
        if out:
            errors.append(f"inactive or out-of-stock IDs: {out}")
    return errors


def batch_errors(responses: list[dict], active: Iterable[str] | None = None) -> dict[str, list[str]]:
    """Validates a batch of submissions. Returns {request_id: errors} for the invalid ones only."""
    active = set(active) if active is not None else None
    bad: dict[str, list[str]] = {}
    seen: set[str] = set()
    for r in responses:
        rid = r.get("request_id", "<no request_id>")
        errs = response_errors(r.get("items"), active)
        if rid in seen:
            errs.append("duplicate request_id in the batch")
        seen.add(rid)
        if errs:
            bad[rid] = errs
    return bad


def _self_test() -> None:
    ok = [f"p_{i}" for i in range(10)]
    assert response_errors(ok) == []
    assert response_errors(ok, active=ok) == []
    assert response_errors(ok[:9]) != []                      # products missing
    assert response_errors(ok[:9] + ["p_0"]) != []            # repeated
    assert response_errors(ok, active=ok[:9]) != []           # one out of stock
    assert response_errors("p_1") != []                       # not a list
    batch = [{"request_id": "r1", "items": ok}, {"request_id": "r1", "items": ok}]
    assert list(batch_errors(batch)) == ["r1"]                # duplicate
    print("validate: all self-tests pass")


if __name__ == "__main__":
    _self_test()
