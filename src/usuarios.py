"""Lo que sabemos de cada comprador. Dueño: Ranking.

ESTADO: vacío (devuelve {}). Tarea R2 de docs/guias/ranking.md.
"""
from __future__ import annotations


def cargar_comprados(hasta: str | None = None) -> dict[str, set[str]]:
    """user_id -> productos que ya compró. No se los volvemos a recomendar.

    `hasta` ('YYYY-MM-DD'): usar solo compras con ts < hasta. None = historial + compras en vivo.
    """
    return {}
