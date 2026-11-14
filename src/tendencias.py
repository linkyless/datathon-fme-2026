"""Qué se está moviendo AHORA en la tienda. Dueño: Ranking.

    cargar_tendencias(hasta) -> {product_id: lift}

"Lift" (subida) = cuánta actividad tiene un producto en los últimos VENTANA_CORTA minutos comparado con su ritmo
normal de las VENTANA_LARGA horas anteriores. 1.0 = como siempre; 2.0 = el doble de lo normal; 0.5 = la mitad.
Así una moda que empieza en la final se nota en minutos, aunque el producto no fuera de los más vendidos.

Actividad = vistas·1 + carritos·6 + compras·3 (los mismos pesos que las co-visitas, docs/investigacion.md punto 5).
Para que un producto con 2 vistas no salte a lift 3 por casualidad se suma un PRIOR (actividad "imaginaria" igual
en los dos lados), que empuja el lift hacia 1 cuando hay pocos datos. Al final se recorta a [LIFT_MIN, LIFT_MAX].
Idea del punto 9 de docs/investigacion.md (lift = corto / (largo + prior)).
"""
from __future__ import annotations

import re

from src.rawtree import query

VENTANA_CORTA = 30
"""Minutos que cuentan como "ahora"."""
VENTANA_LARGA = 180
"""Minutos ANTERIORES a la ventana corta que cuentan como "lo normal"."""
PRIOR = 10.0
"""Actividad imaginaria (en unidades de la ventana corta) que se suma arriba y abajo: con poca señal, lift ≈ 1."""
LIFT_MIN, LIFT_MAX = 0.5, 3.0

_SQL = """
SELECT product_id::String AS product_id,
       sumIf(peso, t >= fin - INTERVAL {corta} MINUTE) AS corto,
       sumIf(peso, t <  fin - INTERVAL {corta} MINUTE) AS largo
FROM (
    SELECT product_id, ts::DateTime64(3) AS t, {fin} AS fin,
           multiIf(event_type::String = 'add_to_cart', 6, event_type::String = 'purchase', 3, 1) AS peso
    FROM {tabla}
    WHERE ts::DateTime64(3) >= {fin} - INTERVAL {total} MINUTE AND ts::DateTime64(3) < {fin}
)
GROUP BY product_id"""


def calcular_lift(corto: float, largo: float) -> float:
    """Ritmo de ahora / ritmo normal, con el prior y recortado. `largo` se pasa a la escala de la ventana corta."""
    normal = largo * VENTANA_CORTA / VENTANA_LARGA
    lift = (corto + PRIOR) / (normal + PRIOR)
    return min(max(lift, LIFT_MIN), LIFT_MAX)


def cargar_tendencias(hasta: str | None = None) -> dict[str, float]:
    """product_id -> lift. Solo se guardan los que se salen de lo normal (lift != 1).

    `hasta` ('YYYY-MM-DD'): las ventanas acaban en esa fecha y se leen de shop.history_events (para evaluar offline).
    None = en vivo: acaban ahora y se leen de shop.events. Se recomienda refrescar cada 1-2 minutos (tarda < 1 s).
    """
    if hasta is None:
        tabla, fin = "shop.events", "now64(3)"
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", hasta):
            raise ValueError(f"hasta debe ser 'YYYY-MM-DD', no {hasta!r}")
        tabla, fin = "shop.history_events", f"toDateTime64('{hasta}', 3)"
    filas = query(_SQL.format(tabla=tabla, fin=fin, corta=VENTANA_CORTA, total=VENTANA_CORTA + VENTANA_LARGA),
                  timeout=60)
    salida = {}
    for f in filas:
        lift = calcular_lift(float(f["corto"] or 0), float(f["largo"] or 0))
        if lift != 1.0:
            salida[f["product_id"]] = lift
    return salida
