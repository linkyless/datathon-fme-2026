"""El servicio EN VIVO del equipo. Dueño: Operador. SOLO lo lanza el Operador.

    python -m src.servicio --sin-envio            # prueba: hace todo menos enviar (se puede usar siempre)
    python -m src.servicio --sin-envio --duracion 120
    python -m src.servicio --relevo               # EN VIVO: se prepara y espera a que pulses Enter para enviar
    python -m src.servicio                        # EN VIVO: envía en cuanto está listo

Tres hilos (trabajadores en paralelo dentro del mismo programa):
  1. SONDEO (el principal): pide a RawTree las peticiones de los últimos 15 s, responde las nuevas con
     ranking.recommend(), valida cada respuesta y deja el lote en la cola de envío. Sin pausas.
  2. ENVÍO: saca lotes de la cola y los inserta en team_NN.submissions. Así el sondeo nunca espera al envío.
  3. REFRESCO: mantiene el Estado al día sin bloquear a los otros dos (stock cada 1 s, catálogo cada 15 s,
     populares cada 10 min, piezas pesadas cada 30 min). Si un refresco falla, se queda con lo anterior.

Reglas que garantiza: una sola respuesta por request_id (también tras reiniciar), siempre 10 IDs válidos
(si recommend() falla o devuelve algo inválido se envían los populares disponibles) y nunca se cae.
Medido el sáb 13:55: cada viaje a RawTree cuesta ~300 ms y el reloj local va ~8 s por detrás del servidor, así que
la edad de cada petición se calcula EN EL SERVIDOR (columna edad_ms), nunca con el reloj del portátil.
"""
from __future__ import annotations

import argparse
import queue
import subprocess
import threading
import time
from pathlib import Path

from src import catalogo, coldstart, covisitas, ranking, usuarios
from src.estado import Estado, por_categoria
from src.rawtree import TEAM, insertar, query
from src.validar import errores_respuesta

SONDEO = """
SELECT request_id::String AS request_id, ts, user_id, is_new_user, context, recent_views,
       dateDiff('millisecond', ts::DateTime64(3), now64(3)) AS edad_ms
FROM shop.requests
WHERE ts > now() - INTERVAL 15 SECOND
ORDER BY ts DESC
LIMIT 300"""
"""Solo los últimos 15 s: la consulta del starter (sin filtro de tiempo) llegó a tardar 10 s."""

YA_ENVIADAS = """
SELECT request_id::String AS request_id
FROM {team}.submissions
WHERE request_id::String IN (
    SELECT request_id::String FROM shop.requests WHERE ts > now() - INTERVAL 10 MINUTE)"""
"""Lo que ya se respondió (por nosotros o por el baseline): al arrancar no se repite. Sin ::String falla."""

LOG = Path(__file__).resolve().parent.parent / "logs" / "servicio.log"


def log(msg: str) -> None:
    linea = f"{time.strftime('%H:%M:%S')} {msg}"
    print(linea, flush=True)
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass


def version_modelo() -> str:
    """model_version = nombre público + hash corto del commit (p. ej. e-samen-v0-585b849)."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return f"{ranking.NOMBRE}-{h.stdout.strip() or 'sin-git'}"
    except Exception:
        return f"{ranking.NOMBRE}-sin-git"


# ---------------------------------------------------------------- Estado y su refresco (hilo 3)
def estado_inicial() -> Estado:
    """Lo mínimo para responder bien (catálogo, stock, populares). Lo pesado llega luego en segundo plano."""
    while True:
        try:
            cat = catalogo.cargar_catalogo()
            agotados = catalogo.cargar_agotados()
            pop = catalogo.cargar_populares()
            return Estado(catalogo=cat, disponibles=catalogo.disponibles(cat, agotados), populares=pop,
                          populares_categoria=por_categoria(cat, pop))
        except Exception as e:
            log(f"estado inicial falló, reintento en 3 s: {e}")
            time.sleep(3)


def refrescar(estado: Estado, parar: threading.Event) -> None:
    """Hilo 3. Cada pieza se sustituye ENTERA (nunca se modifica a medias): el hilo de sondeo siempre ve
    una versión completa, la vieja o la nueva."""
    ultimo: dict[str, float] = {}
    agotados: set[str] = set()

    def toca(nombre: str, cada_s: float) -> bool:
        if time.monotonic() - ultimo.get(nombre, -1e9) >= cada_s:
            ultimo[nombre] = time.monotonic()
            return True
        return False

    while not parar.is_set():
        try:
            if toca("stock", 1):
                agotados = catalogo.cargar_agotados()
                estado.disponibles = catalogo.disponibles(estado.catalogo, agotados)
            if toca("catalogo", 15):
                estado.catalogo = catalogo.cargar_catalogo()
                estado.disponibles = catalogo.disponibles(estado.catalogo, agotados)
                estado.populares_categoria = por_categoria(estado.catalogo, estado.populares)
            if toca("populares", 600):
                estado.populares = catalogo.cargar_populares()
                estado.populares_categoria = por_categoria(estado.catalogo, estado.populares)
            if toca("pesadas", 1800):  # co-visitas, compras, cold start: tardan, pero no bloquean nada
                t = time.monotonic()
                estado.covisitas = covisitas.cargar_covisitas(None)
                estado.comprados = usuarios.cargar_comprados(None)
                estado.populares_contexto = coldstart.cargar_populares_contexto(None)
                estado.vecinos = coldstart.cargar_vecinos(None)
                log(f"piezas pesadas cargadas en {time.monotonic() - t:.1f} s: covisitas={len(estado.covisitas)} "
                    f"comprados={len(estado.comprados)} contexto={len(estado.populares_contexto)} "
                    f"vecinos={len(estado.vecinos)}")
        except Exception as e:  # nos quedamos con lo anterior; se reintenta en la siguiente vuelta
            log(f"refresco falló (sigo con lo anterior): {e}")
        parar.wait(0.2)


# ---------------------------------------------------------------- Respuesta a una petición
def respuesta_segura(peticion: dict, estado: Estado, mv: str) -> tuple[dict, bool]:
    """Devuelve (submission, fue_sustituida). Si recommend() falla o es inválida, populares disponibles."""
    disponibles = estado.disponibles  # una sola lectura: misma versión para recomendar y para validar
    try:
        items = ranking.recommend(peticion, estado)
        if not errores_respuesta(items, disponibles):
            return {"request_id": peticion["request_id"], "items": items, "model_version": mv}, False
    except Exception as e:
        log(f"recommend falló en {peticion.get('request_id')}: {e}")
    items = [p for p in estado.populares if p in disponibles][:10]
    return {"request_id": peticion["request_id"], "items": items, "model_version": mv + "-fallback"}, True


# ---------------------------------------------------------------- Envío (hilo 2)
def enviar(cola: queue.Queue, sin_envio: bool, stats: dict) -> None:
    while True:
        lote = cola.get()
        if lote is None:
            return
        t = time.perf_counter()
        for intento in (1, 2):
            try:
                if not sin_envio:
                    insertar("submissions", lote, timeout=3)
                stats["enviadas"] += len(lote)
                break
            except Exception as e:
                log(f"envío falló (intento {intento}): {e}")
        stats["ms_envio"].append((time.perf_counter() - t) * 1000)


# ---------------------------------------------------------------- Sondeo (hilo 1, el principal)
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-envio", action="store_true", help="hace todo menos insertar en submissions")
    ap.add_argument("--relevo", action="store_true", help="prepararse y esperar Enter antes de empezar")
    ap.add_argument("--duracion", type=float, default=0, help="segundos hasta pararse solo (0 = nunca)")
    args = ap.parse_args()

    mv = version_modelo()
    log(f"arrancando {mv} | equipo {TEAM} | {'SIN ENVÍO (prueba)' if args.sin_envio else 'EN VIVO'}")
    estado = estado_inicial()
    log(f"estado inicial listo: {len(estado.catalogo)} productos, {len(estado.disponibles)} disponibles")
    parar = threading.Event()
    threading.Thread(target=refrescar, args=(estado, parar), daemon=True, name="refresco").start()

    if args.relevo and not args.sin_envio:
        input(">>> LISTO. Para el baseline (Ctrl+C en su terminal) y pulsa Enter AQUÍ enseguida... ")

    vistas: set[str] = set()
    while True:  # las ya respondidas se leen JUSTO antes de empezar: así el relevo no duplica
        try:
            vistas = {f["request_id"] for f in query(YA_ENVIADAS.format(team=TEAM), timeout=10)}
            break
        except Exception as e:
            log(f"no pude leer las ya enviadas, reintento: {e}")
            time.sleep(1)
    log(f"{len(vistas)} peticiones de los últimos 10 min ya estaban respondidas: no se repiten")

    stats = {"enviadas": 0, "sustituidas": 0, "ms_envio": [], "edad_ms": [], "fallos_sondeo": 0}
    cola: queue.Queue = queue.Queue()
    hilo_envio = threading.Thread(target=enviar, args=(cola, args.sin_envio, stats), daemon=True, name="envio")
    hilo_envio.start()

    inicio = ultimo_log = time.monotonic()
    try:
        while not args.duracion or time.monotonic() - inicio < args.duracion:
            try:
                filas = query(SONDEO, timeout=2)
            except Exception as e:  # una consulta colgada no puede parar el servicio
                stats["fallos_sondeo"] += 1
                if stats["fallos_sondeo"] % 10 == 1:
                    log(f"sondeo falló ({stats['fallos_sondeo']} en total): {e}")
                time.sleep(0.05)
                continue
            nuevas = [f for f in filas if f["request_id"] not in vistas]
            lote = []
            for p in nuevas:
                vistas.add(p["request_id"])
                sub, sustituida = respuesta_segura(p, estado, mv)
                stats["sustituidas"] += sustituida
                stats["edad_ms"].append(float(p.get("edad_ms") or 0))
                lote.append(sub)
            if lote:
                cola.put(lote)
            if time.monotonic() - ultimo_log >= 10:
                ultimo_log = time.monotonic()
                edad = sorted(stats["edad_ms"]) or [0]
                envio = sorted(stats["ms_envio"]) or [0]
                log(f"{'(sin envío) ' if args.sin_envio else ''}respondidas={stats['enviadas']} "
                    f"sustituidas={stats['sustituidas']} fallos_sondeo={stats['fallos_sondeo']} | "
                    f"edad al verlas p50={edad[len(edad) // 2]:.0f} p95={edad[int(len(edad) * .95)]:.0f} ms | "
                    f"envío p95={envio[int(len(envio) * .95)]:.0f} ms | cola={cola.qsize()}")
                stats["edad_ms"].clear()
                stats["ms_envio"].clear()
    except KeyboardInterrupt:
        log("parando (Ctrl+C)...")
    finally:
        parar.set()
        cola.put(None)
        hilo_envio.join(timeout=5)
        log(f"parado. Respondidas en total: {stats['enviadas']}, sustituidas: {stats['sustituidas']}")


if __name__ == "__main__":
    main()
