# -*- coding: utf-8 -*-
"""Evolución del aprendiz: animación científica de la reconstrucción de pesos. Dueño: Datos y relato.

    python -m src.evolucion            # genera data/evolucion.json + data/evolucion.html + data/evolucion.mp4

**AVISO DE FIDELIDAD (léelo antes de usar el vídeo)**: la clave de RawTree se desactivó y `data/aprendiz/`
(vivía en la máquina del Operador) no está en este repo. Los puntos de esta serie son una **RECONSTRUCCIÓN
SINTÉTICA**: una curva de aprendizaje simulada (pesos que convergen de 0 a su valor final con ruido
√n, semilla fija) anclada a los TRES puntos reales documentados en docs/estado/alex.md —
AP2 (87.058 respuestas, 763 compras, pesos ×6,1/×5,9/×4,2/×0,12/×25...), AP3 (101.654, 975)
y AP7 (125.389, 1.367, + misma_sub ×1,39) — y al volumen real de tráfico (~10.755 respuestas/hora
desde las 12:27). Los anclajes son reales; TODO lo que hay entre ellos es interpolación simulada.
La arquitectura NO cambia: 12 señales fijas; lo único que "aparece" es `misma_sub`, que se añadió
de verdad el sábado por la noche (AP7), y su entrada está justificada estructuralmente.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SEMILLA = 29  # la misma que LightGBM: misma semilla, misma serie

# Señales reales de src/aprendiz.py, con su valor final documentado (×odds) y su tiempo de
# convergencia en respuestas (cuántos datos necesita cada señal para estabilizarse: las binarias
# de carrito/visto convergen rápido; la afinidad de categoría, que es continua y con rango enorme, tarda más).
SENALES = [
    ("carrito_hoy", 5.98, 6_000, "lo metió hoy en el carrito y no lo ha comprado"),
    ("visto_sesion", 5.76, 6_000, "está en recent_views de esta sesión"),
    ("carrito_antiguo", 4.54, 9_000, "lo metió en el carrito del historial y nunca lo compró"),
    ("afin_cat", 24.2, 30_000, "parte de sus vistas que caen en la categoría del producto"),
    ("tasa", 3.59, 12_000, "compras por vista del producto (suavizado)"),
    ("misma_cat", 2.6, 9_000, "misma categoría que lo último visto"),
    ("afin_sub", 2.5, 15_000, "parte de sus vistas que caen en la subcategoría"),
    ("popularidad", 1.6, 10_000, "log de la parte de las compras de la tienda que se lleva"),
    ("misma_sub", 1.39, 20_000, "misma subcategoría que lo último visto (AP7, se añade de noche)"),
    ("sin_vistas", 0.3, 8_000, "la petición no trae recent_views: ajusta el nivel"),
    ("nuevo", 0.35, 8_000, "comprador nuevo: ajusta el nivel"),
    ("ya_comprado", 0.12, 6_000, "ya lo compró: en vivo no se recompra (×0,12)"),
]

# Anclas REALES (documentadas en docs/estado/alex.md y EXPERIMENTOS): (respuestas acumuladas, compras, ×odds)
ANCLA_AP2 = {"n": 87_058, "compras": 763, "odds": {"carrito_hoy": 6.1, "visto_sesion": 5.9,
             "carrito_antiguo": 4.2, "ya_comprado": 0.12, "afin_cat": 25.0, "afin_sub": 2.5,
             "misma_cat": 2.6, "tasa": 3.4, "popularidad": 1.6}}
ANCLA_AP3 = {"n": 101_654, "compras": 975, "odds": {"carrito_hoy": 5.98, "visto_sesion": 5.76,
             "carrito_antiguo": 4.54, "afin_cat": 24.2, "tasa": 3.59}}
ANCLA_AP7 = {"n": 125_389, "compras": 1_367, "odds": {"misma_sub": 1.39}}
RESP_POR_HORA = 10_755  # medido en el leaderboard (3 peticiones/s)
INICIO_H = 12.45        # 12:27 BCN, cuando empezaron nuestras respuestas


def _odds_odds(w_log: float) -> float:
    return math.exp(w_log)


def serie() -> list[dict]:
    """Puntos temporales con los pesos (en log-odds y ×odds) de cada señal, más compras y error.

    Cada punto = un re-entreno con TODO el feedback acumulado hasta ese momento (curva de
    aprendizaje por acumulación, el mismo protocolo del aprendiz). La dinámica simulada es la
    teórica de un MLE con regularización L2: sesgo hacia 0 con pocos datos, convergencia √n y
    ruido decreciente — anclada a AP2/AP3/AP7.
    """
    import random
    rng = random.Random(SEMILLA)
    puntos = []
    n_total = ANCLA_AP7["n"]
    t_h = INICIO_H
    while True:
        n = int(min((t_h - INICIO_H) * RESP_POR_HORA, n_total))
        if n < 3_000:  # antes de esto el ajuste no es creíble (pocos cientos de compras)
            t_h += 0.25
            if n >= n_total or t_h > 24:
                break
            continue
        compras = round(n * (0.0086 + 0.0022 * min((n / n_total), 1.0)))  # mejora del día: 0,86 % -> 1,08 %
        mejora = 14.3 * min(n / ANCLA_AP3["n"], 1.0) ** 0.8  # curva de aprendizaje real: +14,3 % al final
        pesos = {}
        for nombre, w_final, tau, _txt in SENALES:
            if nombre == "misma_sub" and n < ANCLA_AP3["n"]:
                pesos[nombre] = 0.0  # la señal no existía hasta AP7 (sábado noche)
                continue
            # convergencia: sesgo L2 hacia 0 con pocos datos + ruido √n decreciente
            suavizado = n / (n + tau)
            ruido = rng.gauss(0, 0.35) * math.sqrt(max(tau, 1.0) / max(n, 1.0))
            w_log = math.log(w_final) * suavizado + ruido
            pesos[nombre] = max(_odds_odds(w_log), 1e-4)
        # anclas reales: se imponen sobre la simulación
        for ancla in (ANCLA_AP2, ANCLA_AP3, ANCLA_AP7):
            if n == ancla["n"]:
                for k, v in ancla["odds"].items():
                    pesos[k] = v
        hh = int(t_h) % 24
        mm = int(round((t_h - int(t_h)) * 60))
        dia = "14 nov" if t_h < 24 else "15 nov"
        puntos.append({"hora": f"{dia} {hh:02d}:{mm:02d}", "n": n, "compras": compras,
                       "mejora_pct": round(mejora, 1), "odds": pesos})
        if n >= n_total:
            break
        t_h += 0.25
    # anclas reales: el punto más cercano a cada una se SUSTITUYE por el ancla (n, compras y pesos)
    for ancla in (ANCLA_AP2, ANCLA_AP3, ANCLA_AP7):
        cerca = min(puntos, key=lambda p: abs(p["n"] - ancla["n"]))
        cerca["n"], cerca["compras"] = ancla["n"], ancla["compras"]
        cerca["odds"] = {**cerca["odds"], **{k: v for k, v in ancla["odds"].items()}}
    return puntos


# ---------------------------------------------------------------- salida JSON + HTML
_PLANTILLA = """<!doctype html><html lang='es'><head><meta charset='utf-8'>
<title>Evolución del aprendiz · mental_descent()</title><style>
body{margin:0;background:#0b1220;color:#e2e8f0;font-family:ui-monospace,Consolas,monospace;overflow:hidden}
#c{display:block;margin:0 auto}
.hud{position:absolute;font-size:13px;line-height:1.5}
#titulo{top:16px;left:24px}#titulo h1{margin:0;font-size:17px;color:#93c5fd}
#titulo .sub{color:#64748b;font-size:11px}
#kpi{top:16px;right:24px;text-align:right}#kpi b{font-size:20px;color:#f1f5f9}
#kpi div{margin-bottom:6px}#kpi span{color:#94a3b8;font-size:11px}
#pie{bottom:14px;left:24px;font-size:11px;color:#475569;max-width:900px}
#controles{bottom:14px;right:24px;text-align:right}
button{background:#1e293b;color:#93c5fd;border:1px solid #334155;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
button:hover{background:#334155}
#slider{width:340px;accent-color:#3b82f6}
.badge{display:inline-block;background:#7c2d12;color:#fdba74;font-size:10px;padding:2px 8px;border-radius:4px;margin-left:8px;vertical-align:middle}
</style></head><body>
<canvas id='c' width='1280' height='720'></canvas>
<div class='hud' id='titulo'><h1>Evolución interna del aprendiz <span class='badge'>RECONSTRUCCIÓN SINTÉTICA</span></h1>
<div class='sub'>regresión logística · 12 señales → P(compra) · cada punto = reentreno con el feedback acumulado hasta esa hora</div></div>
<div class='hud' id='kpi'></div>
<div class='hud' id='pie'>Cada punto es un reentreno con TODO el feedback acumulado hasta ese momento
(reconstrucción offline anclada a AP2/AP3/AP7 reales; lo que hay entre anclas es simulación).
Grosor y color de las conexiones: peso en ×odds — azul refuerza la compra, naranja la reduce.
El nodo se dimensiona por su peso absoluto; `misma_sub` aparece de noche (AP7), como pasó de verdad.</div>
<div class='hud' id='controles'><div><button id='play'>▶ reproducir</button> <button id='fin'>⏭ final</button></div>
<div style='margin-top:6px'><input id='slider' type='range' min='0' max='1000' value='0'></div></div>
<script>window.EV = __DATOS__;</script>
<script>
const E = window.EV, N = E.length, CV = document.getElementById('c'), X = CV.getContext('2d');
const POS = []; // 12 señales en arco alrededor del nodo central
const S = E[N-1].senales.map(s => s.nombre);
for (let i = 0; i < S.length; i++){
  const a = Math.PI * (0.08 + 0.84 * i / (S.length - 1));
  POS.push({nombre: S[i], x: 640 - 430 * Math.cos(a), y: 470 - 330 * Math.sin(a), ang: a});
}
let t = 0, play = false, velocidad = 1/900; // 30 s a 30 fps
function lerp(a, b, u){ return a + (b - a) * u; }
function estado(u){
  const p = u * (N - 1), i = Math.min(Math.floor(p), N - 2), f = p - i;
  const A = E[i], B = E[Math.min(i + 1, N - 1)], u2 = Math.min(Math.max((u*(N-1)-i) / 1, 0), 1);
  const w = {}, odds = {};
  for (const s of S){ w[s] = lerp(Math.log(A.odds[s]||1e-4), Math.log(B.odds[s]||1e-4), u2); odds[s] = Math.exp(w[s]); }
  return {u2, odds, hora: u2 < 0.5 ? A.hora : B.hora,
          n: Math.round(lerp(A.n, B.n, u2)), compras: Math.round(lerp(A.compras, B.compras, u2)),
          mejora: lerp(A.mejora_pct, B.mejora_pct, u2)};
}
function nodo(x, y, r, color, glow){
  const g = X.createRadialGradient(x, y, r * .2, x, y, r * 2.2);
  g.addColorStop(0, color + 'cc'); g.addColorStop(1, color + '00');
  X.fillStyle = g; X.beginPath(); X.arc(x, y, r * 2.2, 0, 7); X.fill();
  X.fillStyle = color; X.beginPath(); X.arc(x, y, r, 0, 7); X.fill();
  X.strokeStyle = glow || '#1e293b'; X.lineWidth = 1; X.stroke();
}
function dibujar(u){
  const st = estado(u);
  X.fillStyle = '#0b1220'; X.fillRect(0, 0, 1280, 720);
  X.fillStyle = '#0d1526'; X.fillRect(0, 0, 1280, 520); // zona de red
  const mx = Math.max(...Object.values(st.odds).map(Math.abs), 0.01);
  // conexiones (peso real: grosor ∝ |w|, azul positivo / naranja negativo)
  for (const p of POS){
    const w = Math.log(st.odds[p.nombre]), a = Math.min(Math.abs(w) / 3.4, 1) * 0.85 + 0.06;
    X.strokeStyle = w >= 0 ? `rgba(59,130,246,${a})` : `rgba(249,115,22,${a})`;
    X.lineWidth = 0.6 + 7 * Math.min(Math.abs(w) / 3.4, 1);
    X.beginPath(); X.moveTo(p.x, p.y); X.lineTo(640, 470); X.stroke();
  }
  // nodos: tamaño ∝ peso absoluto (importancia en ×odds)
  for (const p of POS){
    const o = st.odds[p.nombre], r = 6 + 22 * Math.min(Math.abs(Math.log(o)) / 3.4, 1);
    const neg = o < 1;
    nodo(p.x, p.y, r, neg ? '#fb923c' : (o >= 1.5 ? '#60a5fa' : '#7dd3fc'));
    X.fillStyle = '#cbd5e1'; X.font = '10px monospace'; X.textAlign = 'center';
    X.fillText(p.nombre, p.x, p.y + r + 14);
    X.fillStyle = neg ? '#fdba74' : '#93c5fd';
    X.fillText('×' + o.toFixed(2), p.x, p.y + r + 26);
  }
  // nodo central: P(compra)
  nodo(640, 470, 30, '#34d399');
  X.fillStyle = '#052e2b'; X.font = 'bold 12px monospace'; X.textAlign = 'center';
  X.fillText('P(compra)', 640, 474);
  // KPIs
  document.getElementById('kpi').innerHTML =
    `<div><b>${st.n.toLocaleString('es-ES')}</b><br><span>respuestas acumuladas</span></div>` +
    `<div><b>${st.compras.toLocaleString('es-ES')}</b><br><span>compras en el feedback</span></div>` +
    `<div><b>+${st.mejora.toFixed(1)} %</b><br><span>mejora de error frente al puesto</span></div>` +
    `<div><b>${st.hora}</b><br><span>hora del feedback (BCN)</span></div>`;
  X.fillStyle = '#334155'; X.font = '11px monospace'; X.textAlign = 'left';
  X.fillText('log-odds | peso | ×odds:grosores ∝ |peso| — interpolación entre anclas reales AP2/AP3/AP7', 24, 690);
}
function cuadro(){ dibujar(t); document.getElementById('slider').value = Math.round(t * 1000);
  if (play){ t = Math.min(t + velocidad, 1); if (t >= 1) play = false; requestAnimationFrame(cuadro); } }
document.getElementById('play').onclick = () => { play = !play; document.getElementById('play').textContent = play ? '⏸ pausa' : '▶ reproducir'; requestAnimationFrame(cuadro); };
document.getElementById('fin').onclick = () => { t = 1; dibujar(1); };
document.getElementById('slider').oninput = e => { t = e.target.value / 1000; dibujar(t); };
dibujar(0);
</script></body></html>"""


def _odds_odds(w_log: float) -> float:
    return math.exp(w_log)


def main() -> None:
    puntos = serie()
    DATA.mkdir(exist_ok=True)
    (DATA / "evolucion.json").write_text(json.dumps(puntos, ensure_ascii=False, indent=1), encoding="utf-8")
    html = _PLANTILLA.replace("__DATOS__", json.dumps(puntos, ensure_ascii=False))
    (DATA / "evolucion.html").write_text(html, encoding="utf-8")
    print(f"evolucion.json + evolucion.html: {len(puntos)} puntos "
          f"({puntos[0]['n']} -> {puntos[-1]['n']} respuestas)")
    _video(puntos)


def _video(puntos: list[dict], fps: int = 30, segundos: int = 30) -> None:
    """Versión vídeo 1920x1080@30fps con Pillow + imageio-ffmpeg (misma escena, fotograma a fotograma)."""
    try:
        import imageio
    except ImportError:
        print("imageio no disponible: solo HTML interactivo")
        return
    from PIL import Image, ImageDraw, ImageFont

    def fuente(sz):
        for n in ("DejaVuSansMono.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(n, sz)
            except Exception:
                pass
        return ImageFont.load_default()

    W, H = 1920, 1080
    CX, CY, RX, RY = W // 2, int(H * 0.62), 640, 480
    f_tit = fuente(26); f_sub = fuente(15); f_kpi = fuente(30); f_lbl = fuente(14); f_pie = fuente(13)
    azulp, azulf = (96, 165, 250), (147, 197, 253)
    naranjap, naranjaf = (251, 146, 60), (253, 186, 116)
    verdep = (52, 211, 153)

    def fondo(d, st, u):
        d.rectangle([0, 0, W, H], fill=(11, 18, 32))
        d.rectangle([0, 0, W, int(H * 0.72)], fill=(13, 21, 38))
        d.text((36, 24), "Evolución interna del aprendiz — RECONSTRUCCIÓN SINTÉTICA anclada a AP2/AP3/AP7",
               font=f_tit, fill=(147, 197, 253))
        d.text((36, 62), "regresión logística · 12 señales → P(compra) · cada punto = reentreno con el feedback acumulado hasta esa hora",
               font=f_sub, fill=(100, 116, 139))
        kpis = [(f"{st['n']:,}".replace(",", "."), "respuestas acumuladas"),
                (f"{st['compras']:,}".replace(",", "."), "compras en el feedback"),
                (f"+{st['mejora_pct']:.1f} %", "mejora de error vs solo puesto"),
                (st["hora"], "hora del feedback (BCN)")]
        x = W - 640
        for i, (v, t_) in enumerate(kpis):
            yy = 24 + i * 92
            d.text((x, yy), v, font=f_kpi, fill=(241, 245, 249))
            d.text((x, yy + 40), t_, font=f_sub, fill=(148, 163, 184))

    def posiciones(n_senales):
        out = []
        for i in range(n_senales):
            a = math.pi * (0.08 + 0.84 * i / max(n_senales - 1, 1))
            out.append((CX - RX * math.cos(a), CY - RY * math.sin(a)))
        return out

    def fotograma(u):
        st = interpol(u, puntos)
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img, "RGBA")
        fondo(d, st, u)
        mx = max(abs(math.log(o)) for o in st["odds"].values()) or 1.0
        pos = posiciones(len(st["odds"]))
        # conexiones: grosor ∝ |log-odds|, azul positivo / naranja negativo, alpha por magnitud
        for (nombre, o), (px, py) in zip(st["odds"].items(), pos):
            wl = math.log(o)
            a = min(abs(wl) / 3.4, 1.0) * 0.85 + 0.06
            color = azulp if wl >= 0 else naranjap
            d.line([px, py, CX, CY], fill=color + (int(255 * a),), width=int(1 + 6 * min(abs(wl) / 3.4, 1)))
        # nodos: radio ∝ |log-odds| (importancia en ×odds)
        for (nombre, o), (px, py) in zip(st["odds"].items(), pos):
            wl = math.log(o)
            r = int(6 + 22 * min(abs(wl) / 3.4, 1.0))
            colorp = naranjaf if o < 1 else (azulf if o >= 1.5 else (125, 211, 252))
            d.ellipse([px - r * 2, py - r * 2, px + r * 2, py + r * 2], fill=colorp + (36,))
            d.ellipse([px - r, py - r, px + r, py + r], fill=colorp + (230,))
            d.text((px, py + r + 8), nombre, font=f_lbl, fill=(203, 213, 225), anchor="ma")
            d.text((px, py + r + 26), "×" + f"{o:.2f}", font=f_lbl,
                   fill=naranjaf if o < 1 else azulf, anchor="ma")
        d.ellipse([CX - 60, CY - 60, CX + 60, CY + 60], fill=verdep + (40,))
        d.ellipse([CX - 30, CY - 30, CX + 30, CY + 30], fill=verdep + (235,))
        d.text((CX, CY - 6), "P(compra)", font=f_lbl, fill=(5, 46, 43), anchor="mm")
        d.text((36, H - 60), "cada punto = reentreno con todo el feedback acumulado (reconstrucción anclada a AP2/AP3/AP7; interpolación entre anclas)",
               font=f_pie, fill=(71, 85, 105))
        d.text((36, H - 36), f"log-odds → grosor ∝ |peso| · azul refuerza / naranja reduce · interpolación lineal entre estados reales",
               font=f_pie, fill=(71, 85, 105))
        return img

    def interpol(u, pts):
        p = u * (len(pts) - 1)
        i = min(int(p), len(pts) - 2)
        f = p - i
        A, B = pts[i], pts[min(i + 1, len(pts) - 1)]
        out = {"n": round(A["n"] + (B["n"] - A["n"]) * f),
               "compras": round(A["compras"] + (B["compras"] - A["compras"]) * f),
               "mejora_pct": A["mejora_pct"] + (B["mejora_pct"] - A["mejora_pct"]) * f,
               "hora": A["hora"] if f < 0.5 else B["hora"], "odds": {}}
        for k in A["odds"]:
            out["odds"][k] = math.exp(math.log(max(A["odds"][k], 1e-4))
                                      + f * (math.log(max(B["odds"][k], 1e-4)) - math.log(max(A["odds"][k], 1e-4))))
        return out

    marcos = fps * segundos
    ruta = DATA / "evolucion.mp4"
    w = imageio.get_writer(str(ruta), fps=fps, codec="libx264", quality=8,
                           macro_block_size=None, ffmpeg_params=["-pix_fmt", "yuv420p"])
    import sys
    for k in range(marcos):
        u = k / (marcos - 1)
        w.append_data(__import__("numpy").asarray(fotograma(u)))
        if k % 150 == 0:
            print(f"  fotograma {k}/{marcos}", flush=True)
    w.close()
    print(f"vídeo: {ruta} ({ruta.stat().st_size // 1024} kB, {segundos} s @ {fps} fps, {W}x{H})")


if __name__ == "__main__":
    main()
