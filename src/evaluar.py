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
MEDIDAS_BOOTSTRAP = ["hit_rate@10", "purchase_hit@10", "IPS10_clics", "IPS10_compras"]
"""Medidas que lleva el bootstrap (columnas de `aciertos()`). Las IPS10 van por visita, no por 100."""
TOPE_IPS = 50
"""Peso máximo de una visita en ips10() (1/propensity puede ser enorme y hacer que la nota baile). Mismo tope que
usó Leonardo en su chequeo."""


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


INTERRUPTORES: list[str] = []
"""Interruptores de ranking.PARAMS que se prueban en todas sus combinaciones (encendido/apagado) sobre "actual".
Vacío desde el sáb 17:30: contexto y vecinos ya no cambian nada (EXPERIMENTOS, 16:35)."""

VARIANTES = {  # nombre -> (params, piezas del Estado que se vacían)
    # Sáb 17:30: co-visitas descartadas de momento (offline y A/B C/D). Re-visitas, con y sin filtro de recompra.
    "v0 sin filtro (= C en vivo)": ({}, ["covisitas", "comprados"]),
    "v0 +filtro": ({}, ["covisitas"]),
    "v0 +revisitas0.3 sin filtro (= E)": ({"revisitas": 0.3}, ["covisitas", "comprados"]),
    "v0 +revisitas0.3 +filtro": ({"revisitas": 0.3}, ["covisitas"]),
    "v0 +revisitas1.0 sin filtro": ({"revisitas": 1.0}, ["covisitas", "comprados"]),
}
"""Variantes sueltas además de las combinaciones de INTERRUPTORES. Para medir otra idea, añade una línea aquí."""

COMPARAR_CON = ("baseline_categoria", "v0 sin filtro (= C en vivo)")
"""Contra qué políticas se imprime la diferencia con su intervalo de confianza."""


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
    ips = [ips10(t, r) for t, r in zip(test, recs)]
    return {
        "IPS10_clics%": 100 * sum(c for c, _ in ips) / max(len(test), 1),
        "IPS10_compras%": 100 * sum(p for _, p in ips) / max(len(test), 1),
        "n": len(test),
        "hit_rate@10": sum(t["clicked"] in r for t, r in con_clic) / max(len(con_clic), 1),
        "purchase_hit@10": sum(t["purchased"] in r for t, r in con_compra) / max(len(con_compra), 1),
        "IPS_ctr_slot1": sum(w * p for w, p in zip(pesos, premios)) / max(len(test), 1),
        "SNIPS_ctr_slot1": sum(w * p for w, p in zip(pesos, premios)) / max(suma_pesos, 1e-9),
        "slot1_matches": sum(w > 0 for w in pesos),
    }


def ips10(t: dict, recs: list[str]) -> tuple[float, float]:
    """IPS en las 10 posiciones: (clics, compras) esperados en esta visita si hubiéramos enseñado `recs`.

    Por cada producto que el sistema antiguo enseñó y que también está en nuestros 10, si se clicó/compró suma
    1/propensity (con tope TOPE_IPS). Lo que el sistema antiguo enseñaba poco (p. ej. productos ya vistos) pesa
    más cuando aparece, y así se compensa que salga poco. No tiene en cuenta la posición dentro de la lista."""
    nuestros = set(recs)
    clics = compras = 0.0
    for pid, prop in zip(t["shown"], t.get("propensity") or []):
        prop = float(prop or 0)  # misma guardia que en metricas(): 0 o NULL no puede dar peso infinito
        if pid in nuestros and prop > 0:
            peso = min(1 / prop, TOPE_IPS)
            clics += peso * (t["clicked"] == pid)
            compras += peso * (t["purchased"] == pid)
    return clics, compras


def aciertos(test: list[dict], recs: list[list[str]]) -> np.ndarray:
    """Una fila por visita, con las 4 columnas de MEDIDAS_BOOTSTRAP.

    hit y purchase_hit: 1/0, o NaN si no hubo clic/compra (su media sin NaN es el hit_rate@10 y el
    purchase_hit@10). IPS: el valor de ips10() en esa visita. Se guarda visita a visita para el bootstrap."""
    salida = np.full((len(test), len(MEDIDAS_BOOTSTRAP)), np.nan)
    for i, (t, r) in enumerate(zip(test, recs)):
        if t["clicked"]:
            salida[i, 0] = t["clicked"] in r
        if t["purchased"]:
            salida[i, 1] = t["purchased"] in r
        salida[i, 2:] = ips10(t, r)
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
    columnas = ["n", "hit_rate@10", "purchase_hit@10", "IPS10_clics%", "IPS10_compras%", "IPS_ctr_slot1",
                "SNIPS_ctr_slot1", "slot1_matches", "inválidas"]
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
    print(f"{'política':<36}" + "".join(f"{'Δ ' + m + ' [IC 95 %]':<52}" for m in MEDIDAS_BOOTSTRAP))
    for nombre, a in aciertos_por_politica.items():
        if nombre == contra:
            continue
        celdas = ""
        for d, lo, hi in bootstrap(a, aciertos_por_politica[contra]):
            celdas += f"{d:>+9.4f} [{lo:+.4f}, {hi:+.4f}] {veredicto(lo, hi):<17}"
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
    for contra in COMPARAR_CON:
        imprimir_diferencias(aciertos_por_politica, contra)


if __name__ == "__main__":
    main()
