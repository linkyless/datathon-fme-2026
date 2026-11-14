"""Cliente mínimo de RawTree que usa todo el equipo. Dueño: Operador.

    from src.rawtree import query, insertar, TEAM
    filas = query("SELECT count() AS n FROM shop.requests")   # -> [{"n": ...}]
    insertar("scratch", [{"tipo": "prueba", "x": 1}])          # escribe en team_NN.scratch

Lee TEAM_ID y RAWTREE_API_KEY del .env (o del entorno). Nunca imprime la clave.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

Fila = dict[str, Any]


def _cargar_env() -> None:
    """Carga .env de la raíz del repo sin pisar variables que ya existan."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()
API = os.environ.get("RAWTREE_API_URL", "https://api.rawtree.com")
TEAM = os.environ.get("TEAM_ID", "")
_http = requests.Session()
_http.headers["Authorization"] = f"Bearer {os.environ.get('RAWTREE_API_KEY', '')}"


def query(sql: str, timeout: float = 60) -> list[Fila]:
    """Ejecuta UNA sentencia SQL y devuelve las filas como diccionarios."""
    r = _http.post(f"{API}/v1/query", json={"sql": sql}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"RawTree {r.status_code}: {r.text[:300]}")
    return r.json()["data"]


def insertar(tabla: str, filas: list[Fila], timeout: float = 30) -> int:
    """Inserta filas JSON en team_NN.<tabla>. Devuelve cuántas se escribieron."""
    r = _http.post(f"{API}/v1/tables/{tabla}", params={"database": TEAM}, json=filas, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"RawTree {r.status_code}: {r.text[:300]}")
    return int(r.json().get("written_rows", len(filas)))
