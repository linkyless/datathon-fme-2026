"""Arranque en frío: compradores nuevos y productos nuevos. Dueño: Datos y relato.

ESTADO: vacío (devuelve {}). Tareas D3 y D4 de docs/guias/datos.md.
"""
from __future__ import annotations


def cargar_populares_contexto(hasta: str | None = None) -> dict[tuple[str, str], list[str]]:
    """(device, region) -> los más comprados por ese tipo de comprador, de más a menos (máx. 50).

    `hasta` ('YYYY-MM-DD'): usar solo datos con ts < hasta. None = todo (en vivo).
    """
    return {}


def cargar_vecinos(hasta: str | None = None) -> dict[str, list[str]]:
    """product_id -> los ~20 productos con descripción más parecida (por embeddings), del más al menos parecido.

    Lee los embeddings guardados en team_NN.scratch (los calcula src/embeddings.py).
    """
    return {}
