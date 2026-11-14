"""Evaluación offline: pone nota a ranking.recommend() con la última semana del historial. Dueño: Evaluación.

    python -m src.evaluar                # usa el test y el Estado guardados en data/
    python -m src.evaluar --recalcular   # rehace el Estado (cuando cambia alguna pieza)

Reproduce starter/offline_eval.ipynb pero con nuestro contrato (src/estado.py). El "examen" son 100.000 visitas
al carrusel de la última semana; el Estado solo usa datos ANTERIORES a esa semana, para no mirar el futuro.
"""
from __future__ import annotations

import argparse
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from itertools import product
from pathlib import Path

import numpy as np

from src import ranking
from src.estado import Estado, construir
from src.rawtree import query
from src.validar import errores_respuesta

DATA = Path(__file__).resolve().parent.parent / "data"
N_TEST = 100_000
BOOTSTRAP = 200
"""Cuántos remuestreos hace el bootstrap para el intervalo de confianza (ver `bootstrap()`)."""


def fecha_corte() -> str:
    """Último día de shop.history_sessions menos 7 días, como en el cuaderno."""
    ultimo = query("SELECT toDate(max(ts::DateTime64(3))) AS d FROM shop.history_sessions")[0]["d"]
    return str(date.fromisoformat(ultimo) - timedelta(days=7))


def _a_peticion(f: dict) -> dict:
    """Fila de history_sessions -> mismo formato que una fila de shop.requests (lo que espera recommend)."""
    return {
        "request_id": f["request_id"], "ts": f["ts"], "user_id": f["user_id"],
        "context": {"device": f["device"], "region": f["region"], "page": f["page"]},
        "recent_views": f.get("recent_views") or [],
        # Lo que pasó de verdad (recommend no lo mira; solo lo usan las métricas).
        "shown": f["shown"], "propensity": f["propensity"],
        "clicked": f.get("clicked") or None, "purchased": f.get("purchased") or None,
    }


def cargar_test(corte: str) -> list[dict]:
    """Las 100.000 visitas del examen. Se descargan una vez y luego se leen de data/ (siempre las mismas)."""
    ruta = DATA / f"test_{corte}.pkl"
    if ruta.exists():
        return pickle.loads(ruta.read_bytes())
    print(f"Descargando {N_TEST} visitas de test (solo la primera vez)...")
    filas = query(f"""
        SELECT request_id::String AS request_id, toString(ts) AS ts, user_id::String AS user_id,
               `context.device`::String AS device, `context.region`::String AS region,
               `context.page`::String AS page, recent_views, shown, propensity,
               clicked::String AS clicked, purchased::String AS purchased
        FROM shop.history_sessions WHERE ts >= '{corte}' ORDER BY rand() LIMIT {N_TEST}""", timeout=600)
    test = [_a_peticion(f) for f in filas]
    DATA.mkdir(exist_ok=True)
    ruta.write_bytes(pickle.dumps(test))
    return test


def cargar_estado(corte: str, recalcular: bool) -> Estado:
    """Estado construido solo con datos anteriores al corte. Se guarda en data/ porque tarda en calcularse."""
    ruta = DATA / f"estado_{corte}.pkl"
    if ruta.exists() and not recalcular:
        return pickle.loads(ruta.read_bytes())
    print("Construyendo el Estado offline...")
    estado = construir(hasta=corte)
    DATA.mkdir(exist_ok=True)
    ruta.write_bytes(pickle.dumps(estado))
    return estado


INTERRUPTORES = ["usar_contexto", "usar_vecinos"]
"""Interruptores de ranking.PARAMS que se prueban en todas sus combinaciones (encendido/apagado)."""

VARIANTES = {  # nombre -> (params, piezas del Estado que se vacían)
    "actual +revisitas0.3": ({"revisitas": 0.3}, []),  # candidata a v2 (Leonardo)
    "actual sin covisitas": ({}, ["covisitas"]),        # cuánto aporta cada pieza por separado
    "actual sin comprados": ({}, ["comprados"]),
}
"""Variantes sueltas además de las combinaciones de INTERRUPTORES. Para medir otra idea, añade una línea aquí."""


def politicas(estado: Estado) -> dict[str, tuple[Estado, dict]]:
    """Los "alumnos": (libreta, interruptores). Todos usan ranking.recommend.

    Los dos baselines llevan los interruptores apagados; "actual" sale una vez por cada combinación de INTERRUPTORES.
    """
    base = dict(catalogo=estado.catalogo, disponibles=estado.disponibles, populares=estado.populares)
    apagados = {i: False for i in INTERRUPTORES}
    salida = {
        "baseline_starter": (Estado(**base), apagados),                                                    # más vendidos de siempre
        "baseline_categoria": (Estado(**base, populares_categoria=estado.populares_categoria), apagados),  # el de las diapositivas
    }
    for combinacion in product([False, True], repeat=len(INTERRUPTORES)):
        params = dict(zip(INTERRUPTORES, combinacion))
        encendidos = [i.removeprefix("usar_") for i, on in params.items() if on]
        salida["actual" + "".join(f" +{e}" for e in encendidos)] = (estado, params)
    for nombre, (params, vaciar) in VARIANTES.items():
        salida[nombre] = (replace(estado, **{pieza: type(getattr(estado, pieza))() for pieza in vaciar}),
                          {**apagados, **params})
    return salida


def metricas(test: list[dict], recs: list[list[str]]) -> dict[str, float]:
    """Las métricas del cuaderno más purchase_hit@10."""
    con_clic = [(t, r) for t, r in zip(test, recs) if t["clicked"]]
    con_compra = [(t, r) for t, r in zip(test, recs) if t["purchased"]]
    # IPS del clic en la posición 1: solo cuentan las filas donde nuestro 1º coincide con el 1º que se enseñó,
    # y cada una pesa 1/propensity (lo que el sistema antiguo enseñaba poco, pesa más). Guardia: propensity
    # es Dynamic y algún día puede llegar 0 o NULL — un peso infinito o un TypeError matarían la medición.
    pesos, premios = [], []
    for t, r in zip(test, recs):
        coincide = bool(r) and r[0] == t["shown"][0]
        prop = float(t["propensity"][0] or 0) if t.get("propensity") else 0.0
        pesos.append(coincide / prop if coincide and prop > 0 else 0.0)
        premios.append(float(t["clicked"] == t["shown"][0]))
    suma_pesos = sum(pesos)
    return {
        "n": len(test),
        "hit_rate@10": sum(t["clicked"] in r for t, r in con_clic) / max(len(con_clic), 1),
        "purchase_hit@10": sum(t["purchased"] in r for t, r in con_compra) / max(len(con_compra), 1),
        "IPS_ctr_slot1": sum(w * p for w, p in zip(pesos, premios)) / max(len(test), 1),
        "SNIPS_ctr_slot1": sum(w * p for w, p in zip(pesos, premios)) / max(suma_pesos, 1e-9),
        "slot1_matches": sum(w > 0 for w in pesos),
    }


def aciertos(test: list[dict], recs: list[list[str]]) -> np.ndarray:
    """Una fila por visita: [¿lo clicado está en nuestros 10?, ¿lo comprado está?]. 1/0, o NaN si no hubo clic/compra.

    La media de cada columna (sin los NaN) es el hit_rate@10 y el purchase_hit@10. Se guarda visita a visita
    para poder hacer el bootstrap."""
    salida = np.full((len(test), 2), np.nan)
    for i, (t, r) in enumerate(zip(test, recs)):
        if t["clicked"]:
            salida[i, 0] = t["clicked"] in r
        if t["purchased"]:
            salida[i, 1] = t["purchased"] in r
    return salida


def evaluar(test: list[dict], estado: Estado, params: dict) -> tuple[dict, dict, int, np.ndarray]:
    """Nota de una política en todo el test y en el segmento frío (sin vistas recientes), y sus aciertos."""
    recs = [ranking.recommend(t, estado, params) for t in test]
    invalidas = sum(bool(errores_respuesta(r, estado.disponibles)) for r in recs)
    frias = [i for i, t in enumerate(test) if not t["recent_views"]]
    return (metricas(test, recs), metricas([test[i] for i in frias], [recs[i] for i in frias]), invalidas,
            aciertos(test, recs))


def bootstrap(a: np.ndarray, b: np.ndarray, veces: int = BOOTSTRAP) -> list[tuple[float, float, float]]:
    """Diferencia a - b en hit_rate y purchase_hit, con su intervalo de confianza del 95 %.

    Bootstrap: se sortean, con repetición, tantas visitas como hay en el test y se recalcula la diferencia. Se
    repite `veces` veces; el 95 % central de esas diferencias es el intervalo. Las dos políticas usan SIEMPRE las
    mismas visitas sorteadas (bootstrap emparejado): así solo cuenta lo que cambia la política, no la suerte de
    qué visitas tocaron. Si el intervalo no incluye el 0, la diferencia no es casualidad."""
    azar = np.random.default_rng(0)  # semilla fija: el mismo resultado cada vez que se ejecuta
    diferencia = np.nanmean(a, axis=0) - np.nanmean(b, axis=0)
    muestras = []
    for _ in range(veces):
        idx = azar.integers(0, len(a), len(a))
        muestras.append(np.nanmean(a[idx], axis=0) - np.nanmean(b[idx], axis=0))
    bajo, alto = np.percentile(muestras, [2.5, 97.5], axis=0)
    return [(float(d), float(lo), float(hi)) for d, lo, hi in zip(diferencia, bajo, alto)]


def _evaluar_politica(nombre: str, corte: str) -> tuple[str, dict, dict, int, np.ndarray]:
    """Lo que hace cada proceso en paralelo: lee test y Estado de data/ y evalúa UNA política."""
    test = cargar_test(corte)
    estado, params = politicas(cargar_estado(corte, recalcular=False))[nombre]
    return (nombre, *evaluar(test, estado, params))


def imprimir(resultados: dict[str, dict]) -> None:
    columnas = ["n", "hit_rate@10", "purchase_hit@10", "IPS_ctr_slot1", "SNIPS_ctr_slot1", "slot1_matches", "inválidas"]
    print(f"{'política':<36}" + "".join(f"{c:>17}" for c in columnas))
    for nombre, fila in resultados.items():
        celdas = [f"{fila[c]:>17.4f}" if isinstance(fila[c], float) else f"{fila[c]:>17}" for c in columnas]
        print(f"{nombre:<36}" + "".join(celdas))


def imprimir_diferencias(aciertos_por_politica: dict[str, np.ndarray], contra: str) -> None:
    """Cada política frente a `contra`: diferencia e intervalo del 95 % en hit_rate y purchase_hit."""
    def veredicto(lo: float, hi: float) -> str:
        if lo == hi == 0:
            return "idéntico"
        return "sube" if lo > 0 else "baja" if hi < 0 else "puede ser suerte"

    print(f"\nDiferencia frente a {contra} (intervalo de confianza del 95 %, bootstrap de {BOOTSTRAP}):")
    print(f"{'política':<36}{'Δ hit_rate@10 [IC 95 %]':>32}{'':>18}{'Δ purchase_hit@10 [IC 95 %]':>32}")
    for nombre, a in aciertos_por_politica.items():
        if nombre == contra:
            continue
        celdas = ""
        for d, lo, hi in bootstrap(a, aciertos_por_politica[contra]):
            celdas += f"{d:>+10.4f} [{lo:+.4f}, {hi:+.4f}]  {veredicto(lo, hi):<16}"
        print(f"{nombre:<36}{celdas}")


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--recalcular", action="store_true", help="rehace el Estado offline")
    recalcular = args.parse_args().recalcular

    corte = fecha_corte()
    test = cargar_test(corte)
    estado = cargar_estado(corte, recalcular)
    clics = sum(bool(t["clicked"]) for t in test)
    compras = sum(bool(t["purchased"]) for t in test)
    print(f"Corte {corte} · test {len(test)} visitas · {clics} con clic · {compras} con compra · "
          f"versión medida: {ranking.NOMBRE}\n")

    # Cada política en su propio proceso (una por núcleo): mismo resultado, varias veces más rápido.
    # Límite de 4 procesos: cada uno carga su copia del test y del Estado (cientos de MB) y 9 procesos
    # simultáneos pueden quedarse sin RAM en un portátil.
    nombres = list(politicas(estado))
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        salidas = list(pool.map(_evaluar_politica, nombres, [corte] * len(nombres)))
    resultados, aciertos_por_politica = {}, {}
    for nombre, todo, frio, invalidas, acierto in salidas:
        resultados[nombre] = {**todo, "inválidas": invalidas}
        resultados[f"{nombre} (frío)"] = {**frio, "inválidas": "-"}
        aciertos_por_politica[nombre] = acierto
    imprimir({k: v for k, v in resultados.items() if "frío" not in k})
    print("\nSegmento frío (sin vistas recientes):")
    imprimir({k: v for k, v in resultados.items() if "frío" in k})
    for contra in ("baseline_categoria", "baseline_starter", "actual"):  # "actual": ¿qué aporta cada variante?
        imprimir_diferencias(aciertos_por_politica, contra)


if __name__ == "__main__":
    main()
