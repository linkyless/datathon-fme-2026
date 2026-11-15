"""Minimal RawTree client used by the whole project.

    from src.rawtree import query, insert, TEAM
    rows = query("SELECT count() AS n FROM shop.requests")   # -> [{"n": ...}]
    insert("scratch", [{"kind": "test", "x": 1}])            # writes to team_NN.scratch

Reads TEAM_ID and RAWTREE_API_KEY from .env (or from the environment). Never prints the key.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

Row = dict[str, Any]


def _load_env() -> None:
    """Loads .env from the repo root without overwriting variables that already exist."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()
API = os.environ.get("RAWTREE_API_URL", "https://api.rawtree.com")
TEAM = os.environ.get("TEAM_ID", "")
_http = requests.Session()
_http.headers["Authorization"] = f"Bearer {os.environ.get('RAWTREE_API_KEY', '')}"


def query(sql: str, timeout: float = 60) -> list[Row]:
    """Runs ONE SQL statement and returns the rows as dictionaries."""
    r = _http.post(f"{API}/v1/query", json={"sql": sql}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"RawTree {r.status_code}: {r.text[:300]}")
    return r.json()["data"]


def insert(table: str, rows: list[Row], timeout: float = 30) -> int:
    """Inserts JSON rows into team_NN.<table>. Returns how many were written."""
    r = _http.post(f"{API}/v1/tables/{table}", params={"database": TEAM}, json=rows, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"RawTree {r.status_code}: {r.text[:300]}")
    return int(r.json().get("written_rows", len(rows)))
