"""Co-visitas: "quien vio X vio/compró también Y". Dueño: Ranking.

ESTADO: vacío (devuelve {}). Tarea R1 de docs/guias/ranking.md.
"""
from __future__ import annotations


def cargar_covisitas(hasta: str | None = None) -> dict[str, list[tuple[str, float]]]:
    """product_id -> [(otro_product_id, peso), ...] ordenado de mayor a menor peso (máx. 20-50 por producto).

    `hasta` ('YYYY-MM-DD'): usar solo eventos con ts < hasta. None = todo (en vivo).
    """
    return {}
