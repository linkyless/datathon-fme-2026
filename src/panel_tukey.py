"""Panel Tukey: dashboard de decisiones del reto principal. Dueño: Datos y relato.

    python -m src.panel_tukey            # genera data/panel.html (se abre en el navegador)
    python -m src.panel_tukey --loop 60  # lo refresca cada 60 s (para la demo)

Un panel por pregunta del reto; cada panel acaba en la decisión que alimenta
(criterio 1 de Tukey). Todos los paneles leen de las mismas tablas con las
mismas definiciones que el evaluador y el servicio (criterio 3: coherencia).
Sin dependencias nuevas: el HTML es autocontenido (SVG a mano, sin CDN).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.rawtree import query

DATA = Path(__file__).resolve().parent.parent / "data"
PANEL = DATA / "panel.html"

# Desde cuándo responde nuestra versión (e-samen-v0), en UTC; el panel la marca en los gráficos.
VERSION_DESDE = "2026-11-14 13:08:00"

# Cada panel: pregunta -> indicador -> decisión. El gráfico concreto se elige por la forma de los datos
# (líneas para series temporales, barras para categorías: criterio 2 de Tukey, adecuación).
PANELS_SQL: dict[str, str] = {
    # 1) ¿Convertimos mejor? POR VERSIÓN (los brazos del A/B se ven por separado; unión con submissions)
    "conversion_hora": """
        SELECT toStartOfHour(f.ts::DateTime64(3)) AS h,
               if(s.mv = '', '(sin respuesta)', s.mv) AS version, count() AS n,
               round(100*countIf(f.clicked::String != '')/count(), 2) AS pct_clic,
               round(100*countIf(f.purchased::String != '')/count(), 2) AS pct_compra
        FROM (SELECT request_id::String AS rid, ts::DateTime64(3) AS ts,
                     clicked::String AS clicked, purchased::String AS purchased
              FROM team_29.feedback
              WHERE ts > now() - INTERVAL 6 HOUR AND request_id::String != '__init__') f
        LEFT JOIN (SELECT request_id::String AS rid, any(model_version::String) AS mv
                   FROM team_29.submissions GROUP BY rid) s ON f.rid = s.rid
        GROUP BY h, version ORDER BY h, version
    """,
    # 2) ¿Perdemos peticiones? ¿por qué? (últimos 60 min por trozos de 10)
    "fallback_motivo": """
        SELECT fallback_reason::String motivo, count() n
        FROM team_29.feedback
        WHERE ts > now() - INTERVAL 60 MINUTE AND request_id::String != '__init__'
        GROUP BY motivo ORDER BY n DESC
    """,
    # 3) ¿Dónde miran y dónde compran? (carrusel histórico, últimos 3 días)
    "posiciones": """
        SELECT z.2 AS pos, count() imps,
               countIf(z.1 = c) clics, countIf(z.1 = p) compras
        FROM (SELECT arrayJoin(arrayZip(shown, range(1, 11))) AS z,
                     clicked::String AS c, purchased::String AS p
              FROM shop.history_sessions WHERE ts > now() - INTERVAL 3 DAY)
        GROUP BY pos ORDER BY pos
    """,
    # 4) ¿Cuánto pesa el mundo frío? (peticiones por trozos de 30 min, últimas 3 h)
    "frio": """
        SELECT toStartOfInterval(ts::DateTime64(3), INTERVAL 30 MINUTE) t,
               count() n,
               round(100*countIf(is_new_user::Bool = true)/count(), 1) pct_nuevos,
               round(100*countIf(length(recent_views) = 0 AND recent_views IS NOT NULL)/count(), 1) pct_sin_vistas
        FROM shop.requests
        WHERE ts > now() - INTERVAL 3 HOUR
        GROUP BY t ORDER BY t
    """,
    # 4b) conversión del segmento frio vs cálido (feedback unido a peticiones, últimas 3 h)
    "frio_conversion": """
        SELECT if(r.es_nuevo = 1 OR r.es_vacias = 1, 'frio', 'calido') tipo, count() n,
               round(100*countIf(f.purchased::String != '')/count(), 2) pct_compra,
               round(100*countIf(f.clicked::String != '')/count(), 2) pct_clic
        FROM team_29.feedback f
        INNER JOIN (
            SELECT request_id::String rid,
                   if(is_new_user::Bool = true, 1, 0) AS es_nuevo,
                   if(length(recent_views) = 0 AND recent_views IS NOT NULL, 1, 0) AS es_vacias
            FROM shop.requests WHERE ts > now() - INTERVAL 3 HOUR
        ) r ON f.request_id::String = r.rid
        WHERE f.ts > now() - INTERVAL 3 HOUR AND f.request_id::String != '__init__'
        GROUP BY tipo ORDER BY n DESC
    """,
    # 5) ¿Qué se está moviendo? (vistas por subcategoría: últimas 2 h vs 2 h previas)
    "tendencias": """
        SELECT ifNull(if(c.sub = '', 'producto_nuevo', c.sub), 'producto_nuevo') sub,
               sum(v2.n) n_reciente, sum(v0.n) n_antes,
               round(sum(v2.n) / (sum(v0.n) + 1), 2) ratio
        FROM (SELECT product_id::String pid, count() n FROM shop.events
              WHERE event_type = 'view' AND ts > now() - INTERVAL 2 HOUR GROUP BY pid) v2
        LEFT JOIN (SELECT product_id::String pid, count() n FROM shop.events
              WHERE event_type = 'view' AND ts > now() - INTERVAL 4 HOUR AND ts <= now() - INTERVAL 2 HOUR GROUP BY pid) v0
              ON v2.pid = v0.pid
        LEFT JOIN (SELECT product_id::String pid, subcategory::String sub FROM shop.catalog) c
              ON v2.pid = c.pid
        GROUP BY sub ORDER BY ratio DESC LIMIT 12
    """,
    # 6) ¿Recomprar es lo normal? (compras del carrusel de los últimos 7 días que son de un
    #    producto que el usuario ya había comprado antes en los eventos de los últimos 60 días)
    "recompras": """
        SELECT count() AS compras, countIf(h.u IS NOT NULL AND h.primero < s.t) AS recompras
        FROM (SELECT user_id::String AS u, purchased::String AS p, ts::DateTime64(3) AS t
              FROM shop.history_sessions
              WHERE ts > now() - INTERVAL 7 DAY AND purchased::String != '') s
        LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p,
                          min(ts::DateTime64(3)) AS primero
                   FROM shop.history_events WHERE event_type::String = 'purchase' GROUP BY u, p) h
              ON s.u = h.u AND s.p = h.p
    """,
    # 7) ¿Cómo evoluciona la tienda? (catalog_updates acumulados)
    "catalogo": """
        SELECT change::String tipo, count() n
        FROM shop.catalog_updates
        GROUP BY tipo ORDER BY n DESC
    """,
}

# La consulta de fiabilidad por trozos con quantiles sobre Dynamic es delicada:
# p50/p95 por trozo de 10 min con casteo explicito.
PANELS_SQL["fiabilidad"] = """
    SELECT toStartOfInterval(ts::DateTime64(3), INTERVAL 10 MINUTE) t, count() n,
           round(100*countIf(served_by::String = 'team')/count(), 1) pct_nuestro,
           round(quantile(0.5)(latency_ms::Float64), 0) lat_p50,
           round(quantile(0.95)(latency_ms::Float64), 0) lat_p95
    FROM team_29.feedback
    WHERE ts > now() - INTERVAL 60 MINUTE AND request_id::String != '__init__'
    GROUP BY t ORDER BY t
"""

# Texto de decisión por panel (aparece en el propio panel: criterio 1 de Tukey).
DECISIONES = {
    "conversion_hora": "Si nuestra versión (marcada en el eje) no supera la conversión de antes de salir, se revierte o se ajusta antes de subirla más.",
    "fiabilidad": "Si el p95 se acerca a 3.000 ms o cae el % servido: tocar el servicio (timeout, más hilos, fallback) en vez del modelo.",
    "fallback_motivo": "'missing' = llegó tarde o no la vimos; 'invalid' = respuesta mal formada. Cada motivo tiene arreglo distinto.",
    "posiciones": "La caída por posición es sesgo de mirar, no de producto: alimenta la corrección IPS y el orden del ranking.",
    "frio": "Si crece el mundo frio, sube la prioridad de populares por contexto y de productos nuevos embebidos.",
    "frio_conversion": "Decide si al frio le servimos populares por contexto (con conversion propia) o el ranking general.",
    "tendencias": "Subcategorías con ratio > 1: candidatas a entrada del ranker por tendencia y a exploracion (slots 8-10).",
    "recompras": "62 % de las compras del HISTORIAL son recompras, pero EN VIVO no hay recompras (organizadores 17:40). Consecuencias: (1) Alex quita las filas de recompra del test offline; (2) el A/B dice: total CF 1,06 vs C 1,02, fríos C 1,73 vs CF 1,41 — diferencias pequeñas, seguir midiendo antes de fijar el filtro.",
    "catalogo": "Cuánto se mueve la tienda: cadencia de refresco del catálogo y de re-embebido de productos nuevos.",
}

_FOOT = """
<div class="foot"><b>Fuentes y definiciones</b> (criterio de coherencia): conversion y latencia de
<code>team_29.feedback</code> (exclude request_id='__init__'; <i>compra</i> = purchased != ''); posiciones del
carrusel historico de <code>shop.history_sessions</code> (<i>imps</i> = shows por posicion; <i>clicked/purchased</i>
guardan product_id); frio de <code>shop.requests</code>; tendencias de <code>shop.events</code> vistas 2 h vs 2 h
previas; tienda de <code>shop.catalog_updates</code>. Todos los ts en UTC. Version actual marcada desde
<b>%(version_desde)s</b> UTC. Generado por <code>python -m src.panel_tukey</code>.
</div>"""

_CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
header{padding:18px 24px;border-bottom:1px solid #1e293b}
header h1{margin:0 0 4px;font-size:20px} header .sub{color:#94a3b8;font-size:13px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:16px;padding:16px 24px}
.panel{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:12px 14px}
.panel h2{margin:0 0 2px;font-size:15px;color:#f1f5f9}
.panel .pregunta{margin:0 0 8px;font-size:12px;color:#94a3b8}
.panel .decision{margin:8px 0 0;font-size:12.5px;color:#fbbf24}
svg{display:block;width:100%;height:auto}
svg text{fill:#94a3b8;font-size:10px}
.legend{font-size:11px;color:#94a3b8;margin-top:4px}
.foot{padding:10px 24px;color:#64748b;font-size:11.5px;line-height:1.5}
.foot code{color:#94a3b8}
.kpi{font-size:26px;font-weight:600;color:#f1f5f9} .kpi small{font-size:12px;color:#94a3b8;font-weight:400}
.kpis{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 2px}
"""

_JS = """
const COL = {nuestro:'#2563eb', otros:'#64748b', clic:'#22d3ee', compra:'#34d399', rojo:'#f87171',
             ambar:'#fbbf24', eje:'#475569', texto:'#94a3b8', marca:'#f97316',
             p1:'#3b82f6', p2:'#34d399', p3:'#fbbf24', p4:'#c084fc'};
function esc(s){return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function svgOpen(w){return '<svg viewBox="0 0 '+w+' 210" preserveAspectRatio="xMidYMid meet">'}

def lineChart(rows, series, opts){
  const W = 520, H = 190, L = 44, R = 8, T = 12, B = 26;
  if (!rows || rows.length < 1) return svgOpen(W)+'<text x="280" y="105" text-anchor="middle">sin datos aún</text></svg>';
  const xs = rows.map(r => r[opts.x]);
  const all = series.flatMap(s => rows.map(r => +r[s.y]));
  let mx = Math.max(...all), mn = Math.min(0, ...all);
  if (mx === mn) mx = mn + 1;
  const X = i => xs.length < 2 ? (L+W)/2 : L + i*(W-L-R)/(xs.length-1);
  const Y = v => T + (H-T-B)*(1-(v-mn)/(mx-mn));
  let out = svgOpen(W);
  for (let k=0;k<=4;k++){ const v = mn+(mx-mn)*k/4, yy = Y(v);
    out += `<line x1='${L}' y1='${yy}' x2='${W-R}' y2='${yy}' stroke='${COL.eje}' stroke-width='0.6'/>`;
    out += `<text x='${L-4}' y='${yy+3}' text-anchor='end'>${(+v).toFixed(opts.dec ?? 1)}</text>`; }
  const done = new Set();
  xs.forEach((x, i) => { const lab = String(x).slice(5, 16); if (done.has(lab)) return; done.add(lab);
    out += `<text x='${X(i)}' y='${H-8}' text-anchor='middle'>${esc(lab)}</text>`; });
  if (opts.marca){ const i = xs.findIndex(x => String(x) >= opts.marca);
    if (i > 0) out += `<line x1='${X(i)}' y1='${T}' x2='${X(i)}' y2='${H-B}' stroke='${COL.marca}' stroke-dasharray='4 3'/>`
      + `<text x='${X(i)}' y='${T+9}' fill='${COL.marca}'>nuestra v</text>`; }
  for (const s of series){
    const pts = [], circ = [];
    rows.forEach((r, i) => { const y = r[s.y];
      if (y === null || y === undefined) return;
      pts.push(X(i).toFixed(1) + ',' + Y(+y).toFixed(1));
      circ.push(`<circle cx='${X(i)}' cy='${Y(+y)}' r='2.2' fill='${COL[s.color || 'nuestro']}'/>`); });
    if (pts.length > 1)
      out += `<polyline fill='none' stroke='${COL[s.color || 'nuestro']}' stroke-width='1.8' points='${pts.join(' ')}'/>`;
    out += circ.join('');
  }
  return out + '</svg>';
}

function barChart(rows, opts){
  const W = 520, H = 190, L = opts.labelsLeft ?? 120, T = 10, B = 18, R = 10;
  const mx = Math.max(...rows.map(r => Math.max(r.a, r.b ?? 0)), 1);
  const bw = (H-T-B)/rows.length - 3;
  let out = svgOpen(W);
  rows.forEach((r, i) => {
    const y = T + i*((H-T-B)/rows.length) + 1.5;
    const w1 = (W-L-R)*(r.a/mx);
    out += `<rect x='${L}' y='${y}' width='${w1}' height='${bw}' rx='2' fill='${COL[opts.colorA || 'nuestro']}'/>`;
    out += `<text x='${L+w1+4}' y='${y+bw-1}' fill='${COL.texto}'>${r.a.toLocaleString('es-ES')}${opts.suf ?? ''}</text>`;
    if (r.b !== undefined){ const w2 = (W-L-R)*((r.b ?? 0)/mx);
      out += `<rect x='${L}' y='${y+bw+1.5}' width='${w2}' height='${Math.max(bw-2,1)}' rx='2' fill='${COL.otros}'/>`;
      out += `<text x='${L+w2+4}' y='${y+2*bw}' fill='${COL.texto}'>${(r.b ?? 0).toLocaleString('es-ES')}${opts.suf ?? ''}</text>`; }
    out += `<text x='${L-6}' y='${y+bw}' text-anchor='end'>${esc(r.label)}</text>`;
  });
  return out + '</svg>';
}

function kpi(v, sub){ return `<div class='kpi'>${esc(v)} <small>${esc(sub)}</small></div>` }

window.PANELS.forEach(p => {
  const el = document.getElementById('p_' + p.id);
  let html = `<h2>${esc(p.titulo)}</h2><p class='pregunta'>${esc(p.pregunta)}</p>`;
  const rows = p.rows || [];
  if (p.tipo === 'linea') html += lineChart(rows, p.series, p.opts || {});
  else if (p.tipo === 'barras') html += barChart(rows, p.opts || {});
  else if (p.tipo === 'kpi'){
    html += `<div class='kpis'>${p.kpis.map(k => kpi(k[0], k[1])).join('')}</div>`;
    if (rows.length && p.opts2) html += lineChart(rows, p.series, p.opts2);
  }
  html += `<div class='legend'>${p.leyenda || ''}</div><p class='decision'>Decisión: ${esc(p.decision)}</p>`;
  el.innerHTML = html;
});
"""


def _filas(nombre: str, sql: str) -> list[dict]:
    try:
        return query(sql)
    except Exception as e:  # un panel no debe tumbar el dashboard entero
        print(f"panel {nombre}: ERROR {e}", file=sys.stderr)
        return []


def _regenerar() -> None:
    datos = {k: _filas(k, sql) for k, sql in PANELS_SQL.items()}
    conv = datos["conversion_hora"]
    frio = {f["tipo"]: f for f in datos["frio_conversion"]} if datos["frio_conversion"] else {}
    fiab = datos["fiabilidad"]
    fb = datos["fallback_motivo"]

    # Conversión POR VERSIÓN (los brazos del A/B son decisiones distintas y no se mezclan).
    por_version: dict[str, list[dict]] = {}
    for r in conv:
        por_version.setdefault(r["version"], []).append(r)
    horas = sorted({str(r["h"]) for r in conv})
    top = sorted(por_version, key=lambda v: -sum(r["n"] for r in por_version[v]))[:4]
    filas_conv = []
    for h in horas:
        fila = {"h": h}
        for i, v in enumerate(top):
            dato = next((r for r in por_version[v] if str(r["h"]) == h), None)
            fila[f"pc{i}"] = dato["pct_compra"] if dato else None
        filas_conv.append(fila)
    series_conv = [{"y": f"pc{i}", "color": f"p{(i % 4) + 1}", "nombre": v} for i, v in enumerate(top)]

    rec = datos["recompras"][0] if datos["recompras"] else None
    pct_rec = round(100 * rec["recompras"] / rec["compras"], 1) if rec else None
    kpis_rec = [(f"{pct_rec} %", "% de compras que son RECOMPRA"),
                (rec["compras"], "compras del carrusel en 7 días")] if rec else []

    def _pct(rs: list[dict], campo: str) -> float | None:
        n = sum(r["n"] for r in rs)
        return round(sum(r[campo] * r["n"] for r in rs) / n, 2) if n else None

    kpis_conv = [(f"{v}: {_pct(por_version[v], 'pct_compra')} %", "% compras 6 h") for v in top]
    kpis_fiab = [("p95 última hora", max((r["lat_p95"] for r in fiab), default=None), "ms"),
                 ("servido", max((r["pct_nuestro"] for r in fiab), default=None), "%")]

    paneles = [
        {"id": "conv", "tipo": "linea", "titulo": "Compras por hora y por VERSIÓN (A/B)",
         "pregunta": "¿Qué versión convierte mejor? (feedback ⋈ submissions, últimas 6 h)",
         "series": series_conv, "opts": {"x": "h", "dec": 2},
         "leyenda": "una línea por model_version: " + " · ".join(top),
         "decision": DECISIONES["conversion_hora"], "rows": filas_conv},
        {"id": "kpi", "tipo": "kpi",
         "titulo": "Resumen de decisión por versión",
         "pregunta": "Un vistazo: ¿qué versión convierte mejor, y a qué precio de fiabilidad?",
         "kpis": kpis_conv + kpis_fiab,
         "decision": "Cada cambio entra solo si mejora esto en 30-60 min en vivo; si no, se revierte."},
        {"id": "fiab", "tipo": "linea", "titulo": "Fiabilidad de la respuesta",
         "pregunta": "¿Perdemos peticiones? (últimos 60 min, trozos de 10 min)",
         "series": [{"y": "lat_p95", "color": "rojo"}, {"y": "lat_p50", "color": "otros"}],
         "opts": {"x": "t", "dec": 0},
         "leyenda": f"servido por nosotros: {max((r['pct_nuestro'] for r in fiab), default='—')} % · p50 y p95 de latencia (ms) · 3.000 ms = fuera de tiempo",
         "decision": DECISIONES["fiabilidad"], "rows": fiab},
        {"id": "fb", "tipo": "barras", "titulo": "Motivos de fallback (60 min)",
         "pregunta": "Si se perdió una petición, ¿por qué?",
         "rows": [{"label": r["motivo"], "a": r["n"]} for r in fb],
         "opts": {"labelsLeft": 90, "colorA": "rojo"},
         "leyenda": "fallbacks en la última hora",
         "decision": DECISIONES["fallback_motivo"]},
        {"id": "pos", "tipo": "barras", "titulo": "Clics y compras por posición (histórico, 3 días)",
         "pregunta": "¿Dónde miran y dónde compran? (carrusel viejo, 10 slots)",
         "rows": [{"label": "pos " + str(r["pos"]), "a": r["clics"], "b": r["compras"]} for r in datos["posiciones"]],
         "opts": {"labelsLeft": 60, "colorA": "clic"},
         "leyenda": "azul = clics en esa posición · gris = compras · impresiones por posición: "
                    + str(next((r["imps"] for r in datos["posiciones"]), "—")),
         "decision": DECISIONES["posiciones"]},
        {"id": "frio", "tipo": "linea", "titulo": "Mundo frío: petición a petición (3 h)",
         "pregunta": "¿Cuántos compradores llegan sin contexto? (requests)",
         "series": [{"y": "pct_nuevos", "color": "ambar"}, {"y": "pct_sin_vistas", "color": "otros"}],
         "opts": {"x": "t", "dec": 1},
         "leyenda": "% de peticiones con is_new_user=true · % con recent_views vacío",
         "decision": DECISIONES["frio"], "rows": datos["frio"]},
        {"id": "recompras", "tipo": "kpi", "titulo": "Recompras: el comprador repite producto",
         "pregunta": "¿Cuántas compras del carrusel son de algo que el usuario YA había comprado? (7 días)",
         "kpis": kpis_rec,
         "leyenda": "compras del carrusel (history_sessions) de un producto que el mismo usuario ya compró antes en shop.events (60 días)",
         "decision": DECISIONES["recompras"]},
        {"id": "frio_conv", "tipo": "kpi", "titulo": "Conversión frío vs cálido (3 h)",
         "pregunta": "¿Al frío hay que servirle otra cosa? (feedback ⋈ requests por request_id)",
         "kpis": [("frío: " + str(frio.get("frio", {}).get("pct_compra", "—")) + " %", "% compras frío"),
                  ("cálido: " + str(frio.get("calido", {}).get("pct_compra", "—")) + " %", "% compras cálido")],
         "leyenda": "frío = usuario nuevo o sin vistas; conversión sobre " + str(sum(r["n"] for r in datos["frio_conversion"])) + " peticiones",
         "decision": DECISIONES["frio_conversion"]},
        {"id": "trend", "tipo": "barras", "titulo": "Tendencias: vistas últimas 2 h vs 2 h previas",
         "pregunta": "¿Qué se está moviendo ahora mismo? (shop.events)",
         "rows": [{"label": r["sub"], "a": r["n_reciente"], "b": r["n_antes"]} for r in datos["tendencias"]],
         "opts": {"labelsLeft": 130, "colorA": "compra"},
         "leyenda": "verde = vistas últimas 2 h · gris = 2 h previas · subcategorías con mayor ratio",
         "decision": DECISIONES["tendencias"]},
        {"id": "cat", "tipo": "barras", "titulo": "La tienda se mueve (catalog_updates)",
         "pregunta": "¿Cuántas altas, roturas y cambios de precio hay?",
         "rows": [{"label": r["tipo"], "a": r["n"]} for r in datos["catalogo"]],
         "opts": {"labelsLeft": 110, "colorA": "ambar"},
         "leyenda": "acumulado desde el inicio del evento; impone la cadencia de refresco y re-embebido",
         "decision": DECISIONES["catalogo"]},
    ]

    html = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<title>Panel Tukey · mental_descent()</title><style>" + _CSS + "</style></head><body>"
        "<header><h1>Panel Tukey · mental_descent() · recomendador en tiempo real</h1>"
        "<div class='sub'>Un panel = una pregunta del reto → una decisión. Generado "
        + time.strftime("%H:%M:%S") + " (BCN, datos UTC) · <code>python -m src.panel_tukey</code></div></header><main>"
        + "".join("<div class='panel' id='p_%s'></div>" % p["id"] for p in paneles)
        + "</main>" + _FOOT % {"version_desde": VERSION_DESDE}
        + "<script>window.PANELS = " + json.dumps(paneles, default=str, ensure_ascii=False) + "</script>"
        + "<script>" + _JS + "</script></body></html>"
    )
    DATA.mkdir(exist_ok=True)
    PANEL.write_text(html, encoding="utf-8")
    print(f"panel listo: {PANEL} ({PANEL.stat().st_size//1024} kB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera el dashboard de decisiones (Tukey).")
    parser.add_argument("--loop", type=int, default=0, help="regenerar cada N segundos (demo)")
    args = parser.parse_args()
    while True:
        _regenerar()
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
