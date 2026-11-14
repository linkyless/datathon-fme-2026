"""Arranque en frío: compradores nuevos y productos nuevos. Dueño: Datos y relato.

D3 y D4 de docs/guias/datos.md. Los embeddings en sí los calcula src/embeddings.py.
"""
from __future__ import annotations

from src.rawtree import query


def cargar_populares_contexto(hasta: str | None = None) -> dict[tuple[str, str], list[str]]:
    """(device, region) -> los más comprados por ese tipo de comprador, de más a menos (máx. 50).

    `hasta` ('YYYY-MM-DD'): usar solo datos con ts < hasta. None = todo (en vivo).
    """
    filtro = f" AND ts::DateTime64(3) < '{hasta}'" if hasta else ""
    filas = query(
        "SELECT device::String d, region::String r, product_id::String pid, count() c "
        "FROM shop.history_events "
        f"WHERE event_type = 'purchase'{filtro} "
        "GROUP BY d, r, pid "
        "ORDER BY d, r, c DESC, pid "          # pid desempata: orden estable
        "LIMIT 50 BY d, r"
    )
    salida: dict[tuple[str, str], list[str]] = {}
    for f in filas:
        salida.setdefault((f["d"], f["r"]), []).append(f["pid"])
    return salida


def cargar_vecinos(hasta: str | None = None) -> dict[str, list[str]]:
    """product_id -> los ~20 productos con descripción más parecida (por embeddings), del más al menos parecido.

    Lee los embeddings guardados en team_NN.scratch (los calcula src/embeddings.py).
    """
    return {}
