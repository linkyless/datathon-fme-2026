"""Catalog embeddings with qwen3-embedding (HelmCode). Owner: Data.

    python -m src.embeddings             # whole catalog (~5k products)
    python -m src.embeddings --limit 64  # quick test with 64 products

What it does:
1. Reads product_id, category, subcategory, brand and description from shop.catalog.
2. Builds the text "{category} > {subcategory} | {brand} | {description}".
3. Asks https://api.helmcode.com/v1/embeddings for embeddings (OpenAI format):
   max 32 texts per request, 60 requests/min (1.1 s between requests);
   on a 429 it waits 20 s and retries.
4. Normalises every vector (length 1) and saves data/embeddings.npy +
   data/embeddings_ids.json. If it stops halfway, relaunching it picks up
   where it left off (what's already saved isn't requested again).
5. Uploads the vectors to team_NN.scratch with insert("scratch", rows), in
   batches of 100, as {"kind": "emb_v1", "product_id": ..., "vec": float16
   in base64}. product_ids already in scratch aren't sent again.

HELMCODE_API_KEY is read from .env just like in src/rawtree.py; it's never printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

import numpy as np
import requests

from src.rawtree import insert, query

EMBEDDINGS_URL = "https://api.helmcode.com/v1/embeddings"
MODEL = "qwen3-embedding"
MAX_TEXTS = 32         # API limit per request
PAUSE_S = 1.1          # 60 requests/min -> ~1 s of margin
WAIT_429_S = 20.0
RETRIES_429 = 5        # if HelmCode is still saturated after these, give up and retry later
SCRATCH_BATCH = 100

DATA = Path(__file__).resolve().parent.parent / "data"
VEC_PATH = DATA / "embeddings.npy"
IDS_PATH = DATA / "embeddings_ids.json"

_helm = requests.Session()


def _load_env() -> None:
    """Loads .env from the repo root without overwriting variables that already exist."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key_name, _, value = line.partition("=")
            os.environ.setdefault(key_name.strip(), value.strip())


def product_text(p: dict) -> str:
    """Text that gets embedded for each catalog product."""
    category = str(p.get("category") or "").strip()
    subcategory = str(p.get("subcategory") or "").strip()
    brand = str(p.get("brand") or "").strip()
    desc_text = str(p.get("description") or "").strip()
    return f"{category} > {subcategory} | {brand} | {desc_text}"


def read_catalog() -> list[dict]:
    """Catalog rows, one per product, in a stable order."""
    rows = query(
        "SELECT product_id, category, subcategory, brand, description "
        "FROM shop.catalog ORDER BY product_id"
    )
    viewed: set[str] = set()
    uniques: list[dict] = []
    for f in rows:
        pid = str(f["product_id"])
        if pid in viewed:
            continue
        viewed.add(pid)
        uniques.append(f)
    return uniques


def request_embeddings(texts: list[str]) -> list[list[float]]:
    """Gets embeddings for one batch (<= 32); retries with a wait on 429.

    429s aren't retried forever: after RETRIES_429 it raises so that the service (or the next run) retries later and
    the State refresh thread never gets stuck here.
    """
    key_name = os.environ.get("HELMCODE_API_KEY", "")
    if not key_name:
        # No trace of the key in the message: it only says it's missing.
        raise SystemExit("HELMCODE_API_KEY missing from .env (don't put it in the code).")
    failures_429 = 0
    while True:
        r = _helm.post(
            EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {key_name}"},
            json={"model": MODEL, "input": texts},
            timeout=120,
        )
        if r.status_code == 429:
            failures_429 += 1
            if failures_429 > RETRIES_429:
                raise RuntimeError(f"HelmCode 429 after {RETRIES_429} retries (saturated)")
            print(f"    429 ({failures_429}/{RETRIES_429}): waiting {WAIT_429_S:.0f} s...")
            time.sleep(WAIT_429_S)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HelmCode {r.status_code}: {r.text[:300]}")
        payload = r.json()["data"]
        # the API may return the indices out of order: put them back
        payload.sort(key=lambda e: e["index"])
        return [e["embedding"] for e in payload]


def _save(ids: list[str], matrix: np.ndarray) -> None:
    """Writes the .npy and .json so we can resume if it stops."""
    DATA.mkdir(exist_ok=True)
    np.save(VEC_PATH, matrix)
    IDS_PATH.write_text(
        json.dumps({"ids": ids, "model": MODEL}),
        encoding="utf-8",
    )


def compute_embedding(
    rows: list[dict], texts: dict[str, str]
) -> tuple[list[str], np.ndarray]:
    """Requests (and saves after every batch) whatever embeddings are missing."""
    saved_ids: list[str] = []
    matrix = np.zeros((0, 1), dtype=np.float32)
    if VEC_PATH.exists() and IDS_PATH.exists():
        saved_ids = json.loads(IDS_PATH.read_text())["ids"]
        matrix = np.load(VEC_PATH).astype(np.float32)
        print(f"Resuming: {len(saved_ids)} vectors already saved.")

    done = set(saved_ids)
    pending = [p for p in rows if str(p["product_id"]) not in done and texts]

    for i in range(0, len(pending), MAX_TEXTS):
        batch = pending[i : i + MAX_TEXTS]
        batch_ids = [str(p["product_id"]) for p in batch]
        vecs = request_embeddings([texts[pid] for pid in batch_ids])
        m = np.asarray(vecs, dtype=np.float32)
        # normalise to length 1 (dot product = cosine similarity)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        m = m / norms
        saved_ids.extend(batch_ids)
        matrix = m if matrix.size == 0 else np.vstack([matrix, m])
        _save(saved_ids, matrix)
        print(f"  {len(saved_ids)}/{len(rows)} vectors saved.")
        if i + MAX_TEXTS < len(pending):
            time.sleep(PAUSE_S)  # stay under 60 requests/min
    return saved_ids, matrix


def already_uploaded() -> set[str]:
    """product_ids with emb_v1 already in scratch (don't send them again).

    If scratch is still empty it has no columns yet and the query fails: then nothing has been uploaded.
    """
    try:
        rows = query("SELECT product_id FROM team_29.scratch WHERE kind = 'emb_v1'")
    except RuntimeError:
        return set()
    return {str(f["product_id"]) for f in rows}


def upload_to_scratch(ids: list[str], matrix: np.ndarray) -> int:
    """Uploads the vectors to scratch in batches of 100; returns how many were new."""
    by_index = {pid: i for i, pid in enumerate(ids)}
    existing = already_uploaded()
    new_ids = [pid for pid in ids if pid not in existing]
    total = 0
    for i in range(0, len(new_ids), SCRATCH_BATCH):
        batch_pids = new_ids[i : i + SCRATCH_BATCH]
        rows = [
            {
                "kind": "emb_v1",
                "product_id": pid,
                "vec": base64.b64encode(matrix[by_index[pid]].astype(np.float16).tobytes()).decode("ascii"),
            }
            for pid in batch_pids
        ]
        written = insert("scratch", rows)
        total += written
        print(f"  scratch: {total}/{len(new_ids)} new rows.")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalog embeddings (D1).")
    parser.add_argument("--limit", type=int, default=0, help="only try N products")
    args = parser.parse_args()

    _load_env()
    rows = read_catalog()
    if args.limit > 0:
        rows = rows[: args.limit]
    print(f"Catalog: {len(rows)} products.")

    texts = {str(p["product_id"]): product_text(p) for p in rows}
    ids, matrix = compute_embedding(rows, texts)
    print(f"Embeddings: {matrix.shape[0]} vectors of {matrix.shape[1]} dimensions.")

    total = upload_to_scratch(ids, matrix)
    print(f"Uploaded to scratch: {total} new (kind emb_v1).")


if __name__ == "__main__":
    main()
