"""Lo que sabemos de cada comprador. Dueño: Ranking.

Por ahora una sola cosa: qué ha comprado ya cada usuario. La pista oficial dice que quien ya compró un producto
suele no quererlo otra vez, así que `ranking.recommend()` no se lo vuelve a enseñar.
"""
from __future__ import annotations

import re

from src.rawtree import query

HORAS_EN_VIVO = 24
"""En vivo, cuántas horas hacia atrás de shop.events se miran para añadir las compras recientes."""

# Una fila por comprador con la lista (sin repetidos) de lo que compró.
_SQL = """
SELECT user_id::String AS user_id, groupUniqArray(product_id::String) AS productos
FROM {tabla}
WHERE event_type::String = 'purchase' {filtro}
GROUP BY user_id"""


def _compras(tabla: str, filtro: str) -> dict[str, set[str]]:
    filas = query(_SQL.format(tabla=tabla, filtro=filtro), timeout=300)
    return {f["user_id"]: set(f["productos"]) for f in filas}


def cargar_comprados(hasta: str | None = None) -> dict[str, set[str]]:
    """user_id -> productos que ya compró. No se los volvemos a recomendar.

    `hasta` ('YYYY-MM-DD'): usar solo compras con ts < hasta. None = historial + compras en vivo
    (shop.events de las últimas HORAS_EN_VIVO horas).
    """
    if hasta is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", hasta):
            raise ValueError(f"hasta debe ser 'YYYY-MM-DD', no {hasta!r}")
        return _compras("shop.history_events", f"AND ts::DateTime64(3) < '{hasta}'")
    comprados = _compras("shop.history_events", "")
    en_vivo = _compras("shop.events", f"AND ts::DateTime64(3) > now() - INTERVAL {HORAS_EN_VIVO} HOUR")
    for usuario, productos in en_vivo.items():
        comprados.setdefault(usuario, set()).update(productos)
    return comprados
