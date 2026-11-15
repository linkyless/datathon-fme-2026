# -*- coding: utf-8 -*-
"""Evolución de los pesos del modelo en producción. Dueño: Datos y relato.

    python -m src.evolucion            # data/evolucion.json + data/evolucion.html + data/evolucion.mp4

La animación muestra los pesos REALES de las tres versiones del aprendiz que sirvieron en producción
(documentados en docs/estado/alex.md y docs/archive/EXPERIMENTS.md):

    AP2 (20:47)  87.058 respuestas · 763 compras · carrito_hoy ×6,1 · visto ×5,9 · carrito_antiguo ×4,2
                 ya_comprado ×0,12 · afin_cat ×25 · afin_sub ×2,5 · misma_cat ×2,6 · tasa ×3,4 · popularidad ×1,6
    AP3 (21:43)  101.654 respuestas · 975 compras · reentreno con todo el día (mismas señales)
    AP7 (00:15)  125.389 respuestas · 1.367 compras · + señal misma_sub ×1,39 (pesos heredados de AP3)

Las transiciones entre estados son animación (interpolación de dos instantáneas reales); los valores
que se muestran en cada estado son los documentados. Las señales de nivel (sin_vistas, nuevo) no se
dibujan: ajustan la constante y no cambian el orden (como dice su docstring en src/aprendiz.py).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# Los tres estados REALES, con los pesos documentados (×odds). "—" = sin dato documentado: no se dibuja.
ESTADOS = [
    {"version": "AP2", "hora": "20:47", "fecha": "14 nov", "resp": 87_058, "compras": 763,
     "mejora": "+14,1 %",
     "nota": "primer modelo aprendido de nuestro feedback en vivo",
     "odds": {"carrito_hoy": 6.1, "visto_sesion": 5.9, "carrito_antiguo": 4.2,
              "afin_cat": 25.0, "tasa": 3.4, "misma_cat": 2.6, "afin_sub": 2.5,
              "popularidad": 1.6, "ya_comprado": 0.12}},
    {"version": "AP3", "hora": "21:43", "fecha": "14 nov", "resp": 101_654, "compras": 975,
     "mejora": "+14,3 %",
     "nota": "reentreno con todo el feedback del día (pesos casi iguales: <10 % de cambio)",
     "odds": {"carrito_hoy": 5.98, "visto_sesion": 5.76, "carrito_antiguo": 4.54,
              "afin_cat": 24.2, "tasa": 3.59, "misma_cat": 2.6, "afin_sub": 2.5,
              "popularidad": 1.6, "ya_comprado": 0.12}},
    {"version": "AP7", "hora": "00:15", "fecha": "15 nov", "resp": 125_389, "compras": 1_367,
     "mejora": "+14,3 % (estable)",
     "nota": "nueva señal misma_sub ×1,39; pesos heredados de AP3 reentrenados con 125.389 respuestas",
     "odds": {"carrito_hoy": 5.98, "visto_sesion": 5.76, "carrito_antiguo": 4.54,
              "afin_cat": 24.2, "tasa": 3.59, "misma_cat": 2.6, "afin_sub": 2.5,
              "popularidad": 1.6, "ya_comprado": 0.12, "misma_sub": 1.39}},
]
SENAL_TEXTO = {
    "carrito_hoy": "carrito de HOY", "visto_sesion": "visto en la sesión",
    "carrito_antiguo": "carrito del historial", "afin_cat": "afinidad de categoría",
    "tasa": "tasa de compra del producto", "misma_cat": "misma categoría que lo último visto",
    "afin_sub": "afinidad de subcategoría", "popularidad": "popularidad",
    "ya_comprado": "ya lo compró", "misma_sub": "misma subcategoría (AP7)",
}
# Fases del vídeo (segundos): estado · transición · estado · ...
FASES = [("hold", 6), ("trans", 4), ("hold", 6), ("trans", 4), ("hold", 7), ("resumen", 3)]


def estados_json() -> list[dict]:
    return ESTADOS


# ---------------------------------------------------------------- vídeo (Pillow + imageio-ffmpeg)
def _video(fps: int = 30, ruta: Path | None = None) -> None:
    import sys

    import imageio
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1920, 1080
    CX, CY, RX, RY = W // 2, int(H * 0.66), 600, 380
    try:
        f_tit = ImageFont.truetype("DejaVuSansMono.ttf", 26)
        f_sub = ImageFont.truetype("DejaVuSans.ttf", 15)
        f_kpi = ImageFont.truetype("DejaVuSansMono.ttf", 30)
        f_lbl = ImageFont.truetype("DejaVuSans.ttf", 14)
        f_pie = ImageFont.truetype("DejaVuSans.ttf", 13)
        f_big = ImageFont.truetype("DejaVuSansMono.ttf", 34)
    except Exception:
        f_tit = f_kpi = f_big = ImageFont.load_default()
        f_sub = f_lbl = f_pie = ImageFont.load_default()
    azulp, azulf = (96, 165, 250), (147, 197, 253)
    naranjap, naranjaf = (251, 146, 60), (253, 186, 116)
    verdep = (52, 211, 153)
    gris = (100, 116, 139)

    def posiciones(n_senales):
        out = []
        for i in range(n_senales):
            a = math.pi * (0.08 + 0.84 * i / max(n_senales - 1, 1))
            out.append((CX - RX * math.cos(a), CY - RY * math.sin(a)))
        return out

    def peso_log(o):
        return math.log(max(o, 1e-4))

    def mezclar(a: dict, b: dict, f: float) -> dict:
        claves = set(a) | set(b)
        return {k: math.exp(peso_log(a.get(k, 1.0)) * (1 - f) + peso_log(b.get(k, 1.0)) * f) for k in claves}

    def fondo(st, u_fase, fase, meta, estado_idx, total):
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img, "RGBA")
        d.rectangle([0, 0, W, H], fill=(11, 18, 32))
        d.rectangle([0, 0, W, int(H * 0.78)], fill=(13, 21, 38))
        d.text((36, 24), "Modificación de los pesos del modelo en producción", font=f_tit, fill=(147, 197, 253))
        d.text((36, 62), f"regresión logística · 12 señales → P(compra) · versión {st['version']} en vivo desde las {st['hora']} ({st['fecha']})",
               font=f_sub, fill=(100, 116, 139))
        kpis = [(f"{st['resp']:,}".replace(",", "."), "respuestas en el entrenamiento"),
                (f"{st['compras']:,}".replace(",", "."), "compras en el feedback"),
                (st["mejora"], "mejora de error vs solo puesto"),
                (f"{st['hora']} ({st['fecha']})", "momento de la versión")]
        x = W - 560
        for i, (v, t_) in enumerate(kpis):
            yy = 24 + i * 92
            d.text((x, yy), str(v), font=f_kpi, fill=(241, 245, 249))
            d.text((x, yy + 40), t_, font=f_sub, fill=(148, 163, 184))
        d.rounded_rectangle([36, 108, 36 + 720, 164], radius=8, fill=(30, 41, 59), outline=(51, 65, 85))
        d.text((52, 122), f"{st['version']} · {meta}", font=f_sub, fill=(203, 213, 225))
        # leyenda inferior izquierda
        d.ellipse([36, H - 132, 56, H - 112], fill=azulf + (255,))
        d.text((64, H - 132), "nodo azul = señal que REFUERZA la compra (peso positivo, ×odds > 1)",
               font=f_lbl, fill=(148, 163, 184))
        d.ellipse([36, H - 104, 56, H - 84], fill=naranjaf + (255,))
        d.text((64, H - 104), "nodo naranja = señal que la REDUCE (peso negativo, ×odds < 1)",
               font=f_lbl, fill=(148, 163, 184))
        d.text((36, H - 76), "grosor de la línea ∝ |peso| · radio del nodo ∝ importancia (×odds)",
               font=f_lbl, fill=(71, 85, 105))
        d.text((36, H - 48), "señales de nivel (sin_vistas, nuevo) no dibujadas: ajustan la constante, no el orden",
               font=f_pie, fill=(71, 85, 105))
        # barra de progreso de fases
        duraciones = [d_ for _, d_ in FASES]
        transcurrido = sum(duraciones[:estado_idx]) + u_fase * duraciones[estado_idx]
        d.rectangle([0, H - 8, W * transcurrido / total, H], fill=(59, 130, 246))
        return img

    def red(d, odds, version):
        mx = max(abs(peso_log(o)) for o in odds.values()) or 1.0
        pos = posiciones(len(odds))
        for (nombre, o), (px, py) in zip(odds.items(), pos):
            wl = peso_log(o)
            a = min(abs(wl) / 3.4, 1.0) * 0.85 + 0.06
            color = azulp if wl >= 0 else naranjap
            d.line([px, py, CX, CY], fill=color + (int(255 * a),), width=int(1 + 6 * min(abs(wl) / 3.4, 1)))
        for (nombre, o), (px, py) in zip(odds.items(), pos):
            wl = peso_log(o)
            r = int(6 + 22 * min(abs(wl) / 3.4, 1.0))
            colorp = naranjaf if o < 1 else (azulf if o >= 1.5 else (125, 211, 252))
            d.ellipse([px - r * 2, py - r * 2, px + r * 2, py + r * 2], fill=colorp + (36,))
            d.ellipse([px - r, py - r, px + r, py + r], fill=colorp + (230,))
            d.text((px, py + r + 8), SENAL_TEXTO.get(nombre, nombre), font=f_lbl,
                   fill=(203, 213, 225), anchor="ma")
            etiqueta = ("≈" if version == "AP7" and nombre != "misma_sub" else "") + "×" + f"{o:.2f}"
            d.text((px, py + r + 26), etiqueta, font=f_lbl,
                   fill=naranjaf if o < 1 else azulf, anchor="ma")
        d.ellipse([CX - 60, CY - 60, CX + 60, CY + 60], fill=verdep + (40,))
        d.ellipse([CX - 30, CY - 30, CX + 30, CY + 30], fill=verdep + (235,))
        d.text((CX, CY - 6), "P(compra)", font=f_lbl, fill=(5, 46, 43), anchor="mm")

    def resumen(d):
        d.text((CX, int(H * 0.30)), "Convergió: mandan las señales de RELACIÓN", font=f_big,
               fill=(241, 245, 249), anchor="mm")
        lineas = ["carrito de hoy ×6 · visto en la sesión ×5,8 · carrito antiguo ×4,5 · afinidad ×24",
                  "recompra ×0,12 (en vivo no se recompra: lo confirmó el feedback)",
                  "misma subcategoría ×1,39 (AP7: añadida de noche y medida en el A/B)"]
        for i, l in enumerate(lineas):
            d.text((CX, int(H * 0.42) + i * 40), l, font=f_sub, fill=(148, 163, 184), anchor="mm")

    def fotograma(k: int, fps: int):
        total = sum(d_ for _, d_ in FASES)
        t = k % total
        estado_idx = 0
        for i, (fase, dur) in enumerate(FASES):
            if t < dur:
                estado_idx, fase_nombre, u_fase = i, fase, t / dur
                break
            t -= dur
        estado_idx = min(estado_idx, len(ESTADOS) - 1)  # la fase "resumen" muestra el último estado
        st = ESTADOS[estado_idx]
        meta = st["nota"]
        if fase_nombre == "trans":
            siguiente = ESTADOS[min(estado_idx + 1, len(ESTADOS) - 1)]
            odds = mezclar(st["odds"], siguiente["odds"], u_fase)
            version = st["version"] + " → " + siguiente["version"]
        else:
            odds = st["odds"]
            version = st["version"]
        img = fondo(st if fase_nombre != "trans" else {**st, "version": version}, u_fase, fase, meta,
                    estado_idx, total)
        d = ImageDraw.Draw(img, "RGBA")
        red(d, odds, version)
        if fase_nombre == "resumen":
            resumen(d)
        return __import__("numpy").asarray(img)

    writer = imageio.get_writer(str(ruta or (DATA / "evolucion.mp4")), fps=fps, codec="libx264",
                                quality=8, macro_block_size=None, ffmpeg_params=["-pix_fmt", "yuv420p"])
    total_marcos = fps * sum(d_ for _, d_ in FASES)
    for k in range(total_marcos):
        writer.append_data(fotograma(k, fps))
        if k % 150 == 0:
            print(f"  fotograma {k}/{total_marcos}", flush=True)
    writer.close()
    print(f"vídeo: {(ruta or (DATA / 'evolucion.mp4'))} ({sum(d_ for _, d_ in FASES)} s @ {fps} fps, {W}x{H})")


# ---------------------------------------------------------------- versión interactiva (HTML)
_PLANTILLA = """<!doctype html><html lang='es'><head><meta charset='utf-8'>
<title>Pesos del modelo en producción · mental_descent()</title><style>
body{margin:0;background:#0b1220;color:#e2e8f0;font-family:ui-monospace,Consolas,monospace;overflow:hidden}
#c{display:block;margin:0 auto}
.hud{position:absolute;font-size:13px;line-height:1.5}
#titulo{top:16px;left:24px}#titulo h1{margin:0;font-size:17px;color:#93c5fd}
#titulo .sub{color:#64748b;font-size:11px}
#kpi{top:16px;right:24px;text-align:right}#kpi b{font-size:20px;color:#f1f5f9}
#kpi div{margin-bottom:6px}#kpi span{color:#94a3b8;font-size:11px}
#pie{bottom:14px;left:24px;font-size:11px;color:#475569;max-width:900px}
#leyenda{bottom:64px;left:24px;font-size:12px}
#controles{bottom:14px;right:24px;text-align:right}
button{background:#1e293b;color:#93c5fd;border:1px solid #334155;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
button:hover{background:#334155}
#slider{width:340px;accent-color:#3b82f6}
</style></head><body>
<canvas id='c' width='1280' height='720'></canvas>
<div class='hud' id='titulo'><h1>Modificación de los pesos del modelo en producción</h1>
<div class='sub'>regresión logística · señales → P(compra) · los tres modelos servidos en vivo: AP2 → AP3 → AP7</div></div>
<div class='hud' id='kpi'></div>
<div class='hud' id='leyenda'><span style='color:#93c5fd'>●</span> azul = refuerza la compra (×odds &gt; 1)
&nbsp;&nbsp;<span style='color:#fb923c'>●</span> naranja = la reduce (×odds &lt; 1)
&nbsp;&nbsp;grosor ∝ |peso| · señales de nivel (sin_vistas, nuevo) no dibujadas: ajustan la constante</div>
<div class='hud' id='pie'>Los pesos mostrados son los de cada versión servida en vivo (AP2/AP3/AP7, documentados
en el estado del equipo). Las transiciones son animación entre esas tres instantáneas reales.</div>
<div class='hud' id='controles'><div><button id='play'>▶ reproducir</button> <button id='fin'>⏭ final</button></div>
<div style='margin-top:6px'><input id='slider' type='range' min='0' max='1000' value='0'></div></div>
<script>window.EV = __DATOS__;</script>
<script>
const E = window.EV, FASES = [["hold",6],["trans",4],["hold",6],["trans",4],["hold",7],["resumen",3]];
const TOTAL = FASES.reduce((s, f) => s + f[1], 0);
const CV = document.getElementById('c'), X = CV.getContext('2d');
const S = Object.keys(E[0].odds);
const POS = [];
for (let i = 0; i < S.length; i++){
  const a = Math.PI * (0.08 + 0.84 * i / (S.length - 1));
  POS.push({nombre: S[i], x: 640 - 430 * Math.cos(a), y: 470 - 330 * Math.sin(a)});
}
function lerp(a, b, u){ return a + (b - a) * u; }
function peso_log(o){ return Math.log(Math.max(o, 1e-4)); }
function estadoEn(u){
  let t = u * TOTAL, idx = 0, f = 0, fase = "hold";
  for (let i = 0; i < FASES.length; i++){
    if (t < FASES[i][1]){ idx = i; fase = FASES[i][0]; f = t / FASES[i][1]; break; }
    t -= FASES[i][1];
  }
  const a = E[Math.min(idx, E.length - 1)], b = E[Math.min(idx + 1, E.length - 1)];
  const fT = fase === "trans" ? f : 0;
  const odds = {};
  for (const s of S) odds[s] = Math.exp(lerp(peso_log(a.odds[s] ?? 1), peso_log(b.odds[s] ?? 1), fT));
  const st = fT > 0.5 ? b : a;
  return {odds, version: fT > 0.5 ? b.version : a.version, hora: st.hora, fecha: st.fecha,
          resp: Math.round(lerp(a.resp, b.resp, fT)), compras: Math.round(lerp(a.compras, b.compras, fT)),
          mejora: fT > 0.5 ? b.mejora : a.mejora, nota: st.nota, idx};
}
let t = 0, play = false, velocidad = 1/900;
function nodo(x, y, r, color){
  const g = X.createRadialGradient(x, y, r * .2, x, y, r * 2.2);
  g.addColorStop(0, color + 'cc'); g.addColorStop(1, color + '00');
  X.fillStyle = g; X.beginPath(); X.arc(x, y, r * 2.2, 0, 7); X.fill();
  X.fillStyle = color; X.beginPath(); X.arc(x, y, r, 0, 7); X.fill();
}
function dibujar(u){
  const st = estadoEn(u);
  X.fillStyle = '#0b1220'; X.fillRect(0, 0, 1280, 720);
  X.fillStyle = '#0d1526'; X.fillRect(0, 0, 1280, 562);
  const mx = Math.max(...Object.values(st.odds).map(o => Math.abs(peso_log(o))), 0.01);
  for (const p of POS){
    const w = peso_log(st.odds[p.nombre]), a = Math.min(Math.abs(w) / 3.4, 1) * 0.85 + 0.06;
    X.strokeStyle = w >= 0 ? `rgba(59,130,246,${a})` : `rgba(249,115,22,${a})`;
    X.lineWidth = 0.6 + 7 * Math.min(Math.abs(w) / 3.4, 1);
    X.beginPath(); X.moveTo(p.x, p.y); X.lineTo(640, 470); X.stroke();
  }
  for (const p of POS){
    const o = st.odds[p.nombre], r = 6 + 22 * Math.min(Math.abs(peso_log(o)) / 3.4, 1);
    nodo(p.x, p.y, r, o < 1 ? '#fb923c' : (o >= 1.5 ? '#60a5fa' : '#7dd3fc'));
    X.fillStyle = '#cbd5e1'; X.font = '10px monospace'; X.textAlign = 'center';
    X.fillText(p.nombre, p.x, p.y + r + 14);
    X.fillStyle = o < 1 ? '#fdba74' : '#93c5fd';
    X.fillText((st.version === 'AP7' && p.nombre !== 'misma_sub' ? '≈' : '') + '×' + o.toFixed(2), p.x, p.y + r + 26);
  }
  nodo(640, 470, 30, '#34d399');
  X.fillStyle = '#052e2b'; X.font = 'bold 12px monospace'; X.textAlign = 'center';
  X.fillText('P(compra)', 640, 474);
  document.getElementById('kpi').innerHTML =
    `<div><b>${st.version}</b><br><span>versión en producción (${st.fecha} ${st.hora})</span></div>` +
    `<div><b>${st.resp.toLocaleString('es-ES')}</b><br><span>respuestas en el entrenamiento</span></div>` +
    `<div><b>${st.compras.toLocaleString('es-ES')}</b><br><span>compras en el feedback</span></div>` +
    `<div><b>${st.mejora}</b><br><span>mejora de error vs solo puesto</span></div>`;
  const nota = st.nota; // se muestra bajo el grafo
  X.fillStyle = '#94a3b8'; X.font = '12px monospace'; X.textAlign = 'left';
  X.fillText(st.version + ' · ' + nota, 24, 596);
}
function cuadro(){ dibujar(t); document.getElementById('slider').value = Math.round(t * 1000);
  if (play){ t = Math.min(t + velocidad, 1); if (t >= 1) play = false; requestAnimationFrame(cuadro); } }
document.getElementById('play').onclick = () => { play = !play; document.getElementById('play').textContent = play ? '⏸ pausa' : '▶ reproducir'; requestAnimationFrame(cuadro); };
document.getElementById('fin').onclick = () => { t = 1; dibujar(1); };
document.getElementById('slider').oninput = e => { t = e.target.value / 1000; dibujar(t); };
dibujar(0);
</script></body></html>"""


def main() -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / "evolucion.json").write_text(json.dumps(estados_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    html = _PLANTILLA.replace("__DATOS__", json.dumps(estados_json(), ensure_ascii=False))
    (DATA / "evolucion.html").write_text(html, encoding="utf-8")
    print(f"evolucion.json + evolucion.html: {len(ESTADOS)} estados reales (AP2/AP3/AP7)")
    _video()


if __name__ == "__main__":
    main()
