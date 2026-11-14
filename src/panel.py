"""Panel en vivo: nuestro leaderboard para la final (allí el público está oculto). Dueño: Evaluación.

    python -m src.panel                  # últimas 3 horas en tramos de 15 min
    python -m src.panel --horas 1 --minutos 5

Une team_29.feedback (qué pasó con cada petición) con team_29.submissions (qué versión respondió) por request_id.
Solo lee: se puede ejecutar a la vez que el servicio sin molestarlo. Horas en hora de Barcelona.
"""
from __future__ import annotations

import argparse

from src.rawtree import TEAM, query

# "(sin respuesta)" = peticiones a las que no enviamos nada: el comprador vio el fallback (motivo "missing").
# "tarde" = sí enviamos, pero el feedback dice "missing": medido el sáb 16:25, "late" no aparece nunca; lo que llega
# después de los 3 s se registra como "missing" aunque esté en submissions.
_PANEL = """
SELECT {agrupar}
       if(s.mv = '', '(sin respuesta)', s.mv)                AS version,
       count()                                               AS peticiones,
       100 * countIf(f.served_by = 'team') / count()         AS pct_nuestro,
       countIf(f.fallback_reason = 'late'
               OR (f.fallback_reason = 'missing' AND s.mv != '')) AS tarde,
       countIf(f.fallback_reason = 'invalid')                AS invalidas,
       100 * countIf(f.clicked != '') / count()              AS clics_100,
       100 * countIf(f.purchased != '') / count()            AS compras_100,
       sum(f.revenue_eur) / count()                          AS ingreso_peticion,
       quantile(0.95)(f.latency_ms)                          AS p95_ms
FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts, served_by::String AS served_by,
             fallback_reason::String AS fallback_reason, clicked::String AS clicked,
             purchased::String AS purchased, revenue_eur::Float64 AS revenue_eur,
             latency_ms::Nullable(Float64) AS latency_ms
      FROM {team}.feedback
      WHERE ts > now() - INTERVAL {horas} HOUR AND request_id != '__init__') AS f
LEFT JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
           FROM {team}.submissions GROUP BY rid) AS s ON f.rid = s.rid
GROUP BY {grupos}
ORDER BY {grupos}"""

COLUMNAS = [  # (columna, título, formato)
    ("peticiones", "peticiones", "{:>10}"), ("pct_nuestro", "% nuestro", "{:>10.1f}"),
    ("tarde", "tarde", "{:>7}"), ("invalidas", "inválidas", "{:>10}"),
    ("clics_100", "clics/100", "{:>10.2f}"), ("compras_100", "compras/100", "{:>12.2f}"),
    ("ingreso_peticion", "€/petición", "{:>11.3f}"), ("p95_ms", "p95 ms", "{:>8.0f}"),
]


def panel(horas: int, minutos: int | None) -> list[dict]:
    """Filas por versión; con `minutos`, además por tramo de ese tamaño (en hora de Barcelona)."""
    if minutos:
        agrupar = (f"toStartOfInterval(toTimeZone(f.ts, 'Europe/Madrid'), INTERVAL {int(minutos)} MINUTE) "
                   "AS tramo,")
        grupos = "tramo, version"
    else:
        agrupar, grupos = "", "version"
    return query(_PANEL.format(agrupar=agrupar, grupos=grupos, team=TEAM, horas=int(horas)))


def imprimir(filas: list[dict], con_tramo: bool) -> None:
    cabecera = (f"{'tramo':<7}" if con_tramo else "") + f"{'versión':<22}" + "".join(
        f"{titulo:>{len(fmt.format(0))}}" for _, titulo, fmt in COLUMNAS)
    print(cabecera)
    anterior = None
    for f in filas:
        tramo = str(f.get("tramo", ""))[11:16]  # 'YYYY-MM-DD HH:MM:SS' -> 'HH:MM'
        inicio = (f"{tramo if tramo != anterior else '':<7}" if con_tramo else "")
        anterior = tramo
        celdas = "".join(fmt.format(f[col]) if f[col] is not None else f"{'-':>{len(fmt.format(0))}}"
                         for col, _, fmt in COLUMNAS)
        print(inicio + f"{f['version']:<22}" + celdas)


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--horas", type=int, default=3, help="cuántas horas hacia atrás (por defecto 3)")
    args.add_argument("--minutos", type=int, default=15, help="tamaño del tramo en minutos (por defecto 15)")
    a = args.parse_args()

    print(f"Últimas {a.horas} h, tramos de {a.minutos} min (hora de Barcelona)\n")
    imprimir(panel(a.horas, a.minutos), con_tramo=True)
    print(f"\nTotal de las últimas {a.horas} h por versión\n")
    imprimir(panel(a.horas, None), con_tramo=False)


if __name__ == "__main__":
    main()
