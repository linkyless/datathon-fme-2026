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
import dataclasses
import queue
import subprocess
import threading
import time
import zlib
from pathlib import Path

from src import aprendiz, catalogo, coldstart, covisitas, eleccion, ranking, tendencias, usuarios
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

HILOS_ENVIO = 3
"""Trabajadores de envío en paralelo (medido: un insert tarda 0,3-1 s)."""
_CANDADO = threading.Lock()

_V0 = {"_sin_covisitas": True, "_sin_comprados": True}
_CF = {**_V0, "_filtrar_comprados": True}
VARIANTES: list[tuple[str, dict]] = [
    ("-C", _V0),     # control (1/4): v0, populares de su categoría
    ("-CF", _CF),    # 3/4: v0 sin lo que el comprador ya compró
    ("-CF", _CF),
    ("-CF", _CF),
]
VARIANTES_V2: list[tuple[str, dict]] = [
    ("-A7", {"_combo": True}),
]
"""100 % (sáb 19:05). Con vistas: árbol R6 de Leonardo. Sin vistas: lo de CF (más vendidos de siempre sin lo comprado).
A6 (18:51-19:05, mismo árbol + categoría favorita para los sin vistas) frente a CF (API, 1.976 pet.): total 1,089 % vs
1,057 %, €/pet. 0,579 vs 0,404, NO fríos ~0,98 vs ~0,74 (+32 %), pero fríos 1,28 vs 1,62 (−21 %): la "favorita" se
midió con FUGA (actividad posterior a la respuesta) y en vivo resta. Por eso A7 se queda el árbol y quita la favorita."""
"""Decisión de Lester (sáb 18:45): la más fuerte se lo lleva todo, sin reparto. Se compara con el baseline de la misma
ventana del leaderboard y con CF de antes (~1,06 %)."""
VARIANTES_V3: list[tuple[str, dict]] = [                  # PARA MÁS ADELANTE (fríos a su categoría favorita)
    ("-CF", _CF),
    ("-V3", {"_eleccion": True, "_filtrar": True, "_favoritas": True}),
    ("-V3N", {"_eleccion": True, "_filtrar": False, "_favoritas": True}),
]
"""PREPARADO, NO EN VIVO: se activa con `python -m src.servicio --v2` cuando Evaluación dé el ok a la v2.
Responde a la vez: ¿v2 > CF? y ¿hay que quitar lo ya comprado? (sin depender de lo que digan los organizadores)."""
"""Reparto A/B (tarea O6). Cada comprador cae SIEMPRE en la misma variante: hash(user_id) % len(VARIANTES).
Cada variante = (sufijo del nombre, params para ranking.recommend). Ejemplo para probar re-visitas:
    VARIANTES = [("-A", {}), ("-B", {"revisitas": True})]   ->  e-samen-v2-A y e-samen-v2-B en el leaderboard
Con una sola variante ("", {}) todo el mundo recibe lo mismo y el nombre no lleva sufijo.
Claves especiales (las aplica el servicio, no ranking): "_sin_covisitas" y "_sin_comprados" vacían esa pieza del Estado.
A/B 16:41-16:55: A = v1 sin co-visitas, B = v1 completa (las dos con filtro de comprados: A no era la v0).
A/B 16:51-17:10: C = v0 real · D = co-visitas sin filtro -> D pierde (0,78 % vs 1,00 %).
A/B 17:06-17:20: C vs E (re-visitas) -> E por debajo (0,74 % vs 0,97 %, 458 pet.).
A/B 17:17-17:28: C, T, M, MT -> M y MT hundidos: ponían lo YA COMPRADO arriba (61 % de los 1.º) y los organizadores
confirman que en vivo no hay recompras.
A/B actual (sáb 17:30): C · CF · MF · MTF. "_filtrar_comprados" quita de los disponibles de ESE comprador lo que ya compró
(sirve igual para las reglas y para el modelo, que si no lo pondría arriba).
Desde sáb 17:36: CF 3/4 (arrancó 1,58 % vs C 1,00 %, 205 pet.) y C 1/4 de control. MF/MTF fuera hasta reentrenar R5."""


VARIANTES_AP: list[tuple[str, dict]] = [
    ("-C", _V0),                       # control (1/4): v0, populares de su categoría
    ("-AP2", {"_aprendiz": True}),     # 3/4: aprendiz de Alex (pesos aprendidos de NUESTRO feedback en vivo)
    ("-AP2", {"_aprendiz": True}),
    ("-AP2", {"_aprendiz": True}),
]
"""A/B sáb 20:50 (`--ap`): aprendiz (src/aprendiz.py) frente a C. Mientras el Mundo no está listo (~1 min al arrancar),
el brazo responde como C y esas respuestas se etiquetan "-C" (así la fila -AP2 solo tiene aprendiz de verdad).
-AP (20:38-20:51) mezclaba 4 min "como C" con aprendiz: por eso se renombró a -AP2."""

MUNDO: "aprendiz.Mundo | None" = None
MODELO_APRENDIZ: "aprendiz.Modelo | None" = None


def preparar_aprendiz(estado: Estado, parar: threading.Event) -> None:
    """Hilo propio: carga el Mundo (historial + eventos de hoy) y lo pone al día cada 1,5 s. Nunca toca el stock."""
    global MUNDO, MODELO_APRENDIZ
    while not parar.is_set() and MUNDO is None:
        try:
            t = time.monotonic()
            modelo = aprendiz.Modelo.cargar()
            if modelo is None:  # se activa solo en cuanto exista (python -m src.aprendiz lo crea)
                log("aprendiz: aún no hay data/aprendiz.json; el brazo -AP responde como C (reintento en 30 s)")
                parar.wait(30)
                continue
            mundo = aprendiz.Mundo.al_dia(estado.catalogo)
            MODELO_APRENDIZ = modelo
            MUNDO = mundo
            log(f"aprendiz listo en {time.monotonic() - t:.1f} s ({len(mundo.comprados)} compradores con compras)")
        except Exception as e:
            log(f"aprendiz: no se pudo cargar el Mundo (reintento en 30 s): {e}")
            parar.wait(30)
    fallos = 0
    while not parar.is_set():
        try:
            if MUNDO.catalogo is not estado.catalogo:
                MUNDO.catalogo = estado.catalogo
            MUNDO.ponerse_al_dia()
            fallos = 0
        except Exception as e:
            fallos += 1
            if fallos in (1, 10, 100):
                log(f"aprendiz: ponerse_al_dia falló ({fallos} seguidos): {e}")
        parar.wait(1.5)


MODELO_ELECCION = eleccion.Modelo.cargar()
"""e-samen-v2 (src/eleccion.py). Necesita data/eleccion.json (python -m src.eleccion). Si falta, la variante
"_eleccion" falla en cada petición y el servicio responde con los populares (nunca se queda sin responder)."""


def variante(peticion: dict) -> tuple[str, dict]:
    """Elige la variante del comprador. zlib.crc32 da el mismo número en cada reinicio (hash() de Python no)."""
    if len(VARIANTES) == 1:
        return VARIANTES[0]
    clave = str(peticion.get("user_id") or peticion.get("request_id", ""))
    return VARIANTES[zlib.crc32(clave.encode()) % len(VARIANTES)]


def log(msg: str) -> None:
    linea = f"{time.strftime('%H:%M:%S')} {msg}"
    print(linea, flush=True)
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass


def commit_actual() -> str:
    """Hash corto del commit que corre. Solo para el log (NO va en el nombre público)."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return h.stdout.strip() or "sin-git"
    except Exception:
        return "sin-git"


def version_modelo() -> str:
    """model_version = solo el nombre público (e-samen-v0). Sale en el leaderboard: nada de hashes."""
    return ranking.NOMBRE


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

    def intentar(nombre: str, cada_s: float, accion) -> None:
        """Cada pieza por separado: si una falla (red, HelmCode...), las demás siguen, y la que falló se
        reintenta en 60 s (no en su periodo normal, que para las pesadas son 30 min)."""
        if not toca(nombre, cada_s):
            return
        try:
            accion()
        except (Exception, SystemExit) as e:  # SystemExit: embeddings.py lo lanza si falta la clave de HelmCode
            ultimo[nombre] = time.monotonic() - cada_s + min(60, cada_s)
            log(f"refresco de {nombre} falló (sigo con lo anterior): {e}")

    def stock() -> None:
        nonlocal agotados
        agotados = catalogo.cargar_agotados()
        estado.disponibles = catalogo.disponibles(estado.catalogo, agotados)

    def cat() -> None:
        estado.catalogo = catalogo.cargar_catalogo()
        estado.disponibles = catalogo.disponibles(estado.catalogo, agotados)
        estado.populares_categoria = por_categoria(estado.catalogo, estado.populares)

    def populares() -> None:
        estado.populares = catalogo.cargar_populares()
        estado.populares_categoria = por_categoria(estado.catalogo, estado.populares)

    def nuevos() -> None:
        n = coldstart.embeber_nuevos()
        log(f"embeber_nuevos: {n}")
        if n:
            ultimo["pesadas"] = -1e9  # recargar vecinos enseguida con los nuevos embeddings

    def tend() -> None:
        estado.tendencias = tendencias.cargar_tendencias(None)

    def pesadas() -> None:  # co-visitas, compras, cold start: tardan, pero no bloquean nada
        t = time.monotonic()
        estado.covisitas = covisitas.cargar_covisitas(None)
        estado.comprados = usuarios.cargar_comprados(None)
        estado.populares_contexto = coldstart.cargar_populares_contexto(None)
        estado.vecinos = coldstart.cargar_vecinos(None)
        estado.categorias_usuario = eleccion.cargar_categorias_usuario(None)
        estado.perfiles_comprador, estado.perfiles_producto = coldstart.cargar_perfiles(None)
        log(f"piezas pesadas cargadas en {time.monotonic() - t:.1f} s: covisitas={len(estado.covisitas)} "
            f"comprados={len(estado.comprados)} contexto={len(estado.populares_contexto)} "
            f"vecinos={len(estado.vecinos)} categorias_usuario={len(estado.categorias_usuario)}")

    def bucle_lento() -> None:
        """Hilo aparte para lo que tarda (pesadas: 13 s con buena red, 90 s con mala; HelmCode: minutos si hay
        429). Así el stock se sigue actualizando cada segundo pase lo que pase aquí."""
        while not parar.is_set():
            intentar("pesadas", 1800, pesadas)
            intentar("tendencias", 90, tend)
            intentar("nuevos", 300, nuevos)
            parar.wait(1)

    threading.Thread(target=bucle_lento, name="lento", daemon=True).start()
    while not parar.is_set():
        intentar("stock", 1, stock)
        intentar("catalogo", 15, cat)
        intentar("populares", 600, populares)
        parar.wait(0.2)


# ---------------------------------------------------------------- Respuesta a una petición
def respuesta_segura(peticion: dict, estado: Estado, mv: str) -> tuple[dict, bool]:
    """Devuelve (submission, fue_sustituida). Si recommend() falla o es inválida, populares disponibles."""
    disponibles = estado.disponibles  # una sola lectura: misma versión para recomendar y para validar
    sufijo, params = variante(peticion)
    mv = mv + sufijo
    try:
        if params.get("_filtrar_comprados"):
            ya = estado.comprados.get(str(peticion.get("user_id", "")))
            if ya:
                estado = dataclasses.replace(estado, disponibles=disponibles - ya)
            params = {k: v for k, v in params.items() if k != "_filtrar_comprados"}
        vaciar = {"_sin_covisitas": "covisitas", "_sin_comprados": "comprados"}
        if any(params.get(k) for k in vaciar):
            estado = dataclasses.replace(estado, **{campo: {} for k, campo in vaciar.items() if params.get(k)})
            params = {k: v for k, v in params.items() if k not in vaciar}
        if params.get("_aprendiz"):
            if MUNDO is not None and MODELO_APRENDIZ is not None:
                items = aprendiz.recomendar(peticion, MUNDO, MODELO_APRENDIZ, disponibles)
                if not errores_respuesta(items, disponibles):
                    return {"request_id": peticion["request_id"], "items": items, "model_version": mv}, False
            params = _V0  # aún sin Mundo (o respuesta rara): como C, y etiquetado como C
            mv = mv[: len(mv) - len(sufijo)] + "-C"
            estado = dataclasses.replace(estado, covisitas={}, comprados={})
        tiene_vistas = any(v.get("product_id") for v in (peticion.get("recent_views") or []))
        if params.get("_combo") and tiene_vistas:
            items = ranking.recommend(peticion, estado, {"usar_arbol": True, "filtrar_comprados": True})
        elif params.get("_combo"):  # sin vistas: lo de CF (más vendidos de siempre sin lo ya comprado, 1,62 % medido)
            items = ranking.recommend(peticion, estado, {"filtrar_comprados": True})
        elif params.get("_eleccion"):
            items = eleccion.recomendar(peticion, estado, MODELO_ELECCION, filtrar_comprados=params.get("_filtrar", True),
                                        favoritas_frio=params.get("_favoritas", False))
        else:
            items = ranking.recommend(peticion, estado, params)
        if not errores_respuesta(items, disponibles):
            return {"request_id": peticion["request_id"], "items": items, "model_version": mv}, False
    except Exception as e:
        log(f"recommend falló en {peticion.get('request_id')}: {e}")
    # Fallback: populares disponibles. Si son menos de 10, se completa con cualquiera
    # disponible (enviar <10 es inválida; los populares disponibles pueden quedar por
    # debajo de 10 si hay muchos agotados). Se valida antes de salir, por si acaso.
    items = [p for p in estado.populares if p in disponibles][:10]
    if len(items) < 10:
        for p in sorted(disponibles):
            if len(items) == 10:
                break
            if p not in items:
                items.append(p)
    if errores_respuesta(items, disponibles):
        log(f"fallback inválido en {peticion.get('request_id')}: disponibles={len(disponibles)}")
    return {"request_id": peticion["request_id"], "items": items, "model_version": mv}, True


# ---------------------------------------------------------------- Envío (hilo 2)
def _sin_los_ya_enviados(lote: list[dict]) -> list[dict] | None:
    """Las del lote que NO están en submissions. None si la consulta falla."""
    ids = ", ".join("'" + str(f["request_id"]).replace("'", "") + "'" for f in lote)
    try:
        ya = {f["r"] for f in query(f"SELECT request_id::String r FROM {TEAM}.submissions WHERE request_id::String IN ({ids})",
                                    timeout=2)}
    except Exception:
        return None
    return [f for f in lote if f["request_id"] not in ya]


def enviar(cola: queue.Queue, sin_envio: bool, stats: dict) -> None:
    while True:
        lote = cola.get()
        if lote is None:
            return
        t = time.perf_counter()
        enviado = False
        for intento in (1, 2):
            try:
                if not sin_envio:
                    insertar("submissions", lote, timeout=3)
                enviado = True
                with _CANDADO:  # varios hilos suman a la vez: sin candado se perderían cuentas
                    stats["enviadas"] += len(lote)
                break
            except Exception as e:
                log(f"envío falló (intento {intento}): {e}")
                if intento == 1 and "Read timed out" in str(e):
                    # El lote SALIÓ y no llegó la confirmación: puede haber entrado. Reenviar a ciegas duplicaba
                    # respuestas (medido sáb 20:45: 7 duplicadas en 20 s). Se reenvía solo lo que no esté ya.
                    lote = _sin_los_ya_enviados(lote)
                    if lote is None:  # no se pudo comprobar: mejor perderlas que mandarlas dos veces
                        log("no se pudo comprobar qué entró: no se reintenta (evita duplicados)")
                        enviado = True
                        break
                    if not lote:
                        enviado = True
                        log("el lote sí había entrado: no se reenvía")
                        break
        if not enviado:  # dos intentos fallidos: esas peticiones quedan como "missing" en el feedback
            with _CANDADO:
                stats["perdidas"] += len(lote)
            log(f"lote perdido tras 2 intentos: {len(lote)} peticiones")
        with _CANDADO:
            stats["ms_envio"].append((time.perf_counter() - t) * 1000)


# ---------------------------------------------------------------- Sondeo (hilo 1, el principal)
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-envio", action="store_true", help="hace todo menos insertar en submissions")
    ap.add_argument("--relevo", action="store_true", help="prepararse y esperar Enter antes de empezar")
    ap.add_argument("--v2", action="store_true", help="usar VARIANTES_V2 (A/B C / V2 / V3, 1/3 cada uno)")
    ap.add_argument("--ap", action="store_true", help="usar VARIANTES_AP (C 1/4, aprendiz 3/4)")
    ap.add_argument("--duracion", type=float, default=0, help="segundos hasta pararse solo (0 = nunca)")
    args = ap.parse_args()
    global VARIANTES
    if args.v2:
        VARIANTES = VARIANTES_V2
    if args.ap:
        VARIANTES = VARIANTES_AP

    mv = version_modelo()
    log(f"arrancando {mv} | equipo {TEAM} | {'SIN ENVÍO (prueba)' if args.sin_envio else 'EN VIVO'} | variantes {[v for v, _ in VARIANTES]}")
    estado = estado_inicial()
    log(f"estado inicial listo: {len(estado.catalogo)} productos, {len(estado.disponibles)} disponibles")
    parar = threading.Event()
    threading.Thread(target=refrescar, args=(estado, parar), daemon=True, name="refresco").start()
    if any(p.get("_aprendiz") for _, p in VARIANTES):
        threading.Thread(target=preparar_aprendiz, args=(estado, parar), daemon=True, name="aprendiz").start()

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

    stats = {"enviadas": 0, "sustituidas": 0, "perdidas": 0, "ms_envio": [], "edad_ms": [], "fallos_sondeo": 0}
    cola: queue.Queue = queue.Queue()
    # Varios trabajadores de envío: un insert tarda hasta ~1 s y, con uno solo, el lote siguiente esperaba en cola.
    hilos_envio = [threading.Thread(target=enviar, args=(cola, args.sin_envio, stats), daemon=True, name=f"envio{i}")
                   for i in range(HILOS_ENVIO)]
    for h in hilos_envio:
        h.start()

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
        for _ in hilos_envio:
            cola.put(None)
        for h in hilos_envio:
            h.join(timeout=5)
        log(f"parado. Respondidas en total: {stats['enviadas']}, sustituidas: {stats['sustituidas']}, "
            f"perdidas por envío fallido: {stats['perdidas']}")


if __name__ == "__main__":
    main()
