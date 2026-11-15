"""Embeddings del catálogo con qwen3-embedding (HelmCode). Dueño: Datos y relato.

    python -m src.embeddings             # catálogo entero (~5k productos)
    python -m src.embeddings --limit 64  # prueba con 64 productos

Qué hace:
1. Lee product_id, category, subcategory, brand y description de shop.catalog.
2. Forma el texto "{category} > {subcategory} | {brand} | {description}".
3. Pide embeddings a https://api.helmcode.com/v1/embeddings (formato OpenAI):
   máx. 32 textos por petición, 60 peticiones/min (1,1 s entre peticiones);
   si responde 429, espera 20 s y reintenta.
4. Normaliza cada vector (longitud 1) y guarda data/embeddings.npy +
   data/embeddings_ids.json. Si se corta a medias, al relanzarlo continúa
   donde iba (lo ya guardado no se vuelve a pedir).
5. Sube los vectores a team_NN.scratch con insertar("scratch", filas), en
   lotes de 100, como {"tipo": "emb_v1", "product_id": ..., "vec": float16
   en base64}. Los product_id que ya están en scratch no se reenvían.

La clave HELMCODE_API_KEY se lee del .env igual que en src/rawtree.py;
nunca se imprime.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

from src.rawtree import insert as insertar, query

URL_EMBEDDINGS = "https://api.helmcode.com/v1/embeddings"
MODELO = "qwen3-embedding"
MAX_TEXTOS = 32          # límite de la API por petición
PAUSA_S = 1.1            # 60 peticiones/min -> ~1 s de margen
ESPERA_429_S = 20.0
REINTENTOS_429 = 5       # si HelmCode sigue saturado tras estos, se suelta y se reintenta luego
LOTE_SCRATCH = 100

DATA = Path(__file__).resolve().parent.parent / "data"
RUTA_VEC = DATA / "embeddings.npy"
RUTA_IDS = DATA / "embeddings_ids.json"

_helm = requests.Session()


def _cargar_env() -> None:
    """Carga .env de la raíz del repo sin pisar variables que ya existan."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


def texto_producto(p: dict) -> str:
    """Texto que se embebe para cada producto del catálogo."""
    categoria = str(p.get("category") or "").strip()
    subcategoria = str(p.get("subcategory") or "").strip()
    marca = str(p.get("brand") or "").strip()
    descripcion = str(p.get("description") or "").strip()
    return f"{categoria} > {subcategoria} | {marca} | {descripcion}"


def leer_catalogo() -> list[dict]:
    """Filas del catálogo, una por producto, en orden estable."""
    filas = query(
        "SELECT product_id, category, subcategory, brand, description "
        "FROM shop.catalog ORDER BY product_id"
    )
    vistos: set[str] = set()
    unicos: list[dict] = []
    for f in filas:
        pid = str(f["product_id"])
        if pid in vistos:
            continue
        vistos.add(pid)
        unicos.append(f)
    return unicos


def pedir_embeddings(textos: list[str]) -> list[list[float]]:
    """Pide embeddings de un lote (<= 32); reintenta con espera si hay 429.

    Los 429 no se reintentan para siempre: tras REINTENTOS_429 se suelta con excepción
    para que el servicio (o la siguiente ejecución) lo reintente más tarde y el hilo
    de refresco del Estado nunca se quede atascado aquí.
    """
    clave = os.environ.get("HELMCODE_API_KEY", "")
    if not clave:
        # Sin rastro de la clave en el mensaje: solo avisa de que falta.
        raise SystemExit("Falta HELMCODE_API_KEY en el .env (no la escribas en el código).")
    fallos_429 = 0
    while True:
        r = _helm.post(
            URL_EMBEDDINGS,
            headers={"Authorization": f"Bearer {clave}"},
            json={"model": MODELO, "input": textos},
            timeout=120,
        )
        if r.status_code == 429:
            fallos_429 += 1
            if fallos_429 > REINTENTOS_429:
                raise RuntimeError(f"HelmCode 429 tras {REINTENTOS_429} reintentos (saturado)")
            print(f"    429 ({fallos_429}/{REINTENTOS_429}): esperando {ESPERA_429_S:.0f} s...")
            time.sleep(ESPERA_429_S)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HelmCode {r.status_code}: {r.text[:300]}")
        datos = r.json()["data"]
        # La API puede devolver los índices desordenados: los restauramos.
        datos.sort(key=lambda e: e["index"])
        return [e["embedding"] for e in datos]


def _guardar(ids: list[str], matriz: np.ndarray) -> None:
    """Escribe .npy y .json para poder continuar si se corta."""
    DATA.mkdir(exist_ok=True)
    np.save(RUTA_VEC, matriz)
    RUTA_IDS.write_text(
        json.dumps({"ids": ids, "model": MODELO}),
        encoding="utf-8",
    )


def calcular_embebiendo(
    filas: list[dict], textos: dict[str, str]
) -> tuple[list[str], np.ndarray]:
    """Pide (y guarda tras cada lote) los embeddings que falten."""
    ids_guardados: list[str] = []
    matriz = np.zeros((0, 1), dtype=np.float32)
    if RUTA_VEC.exists() and RUTA_IDS.exists():
        ids_guardados = json.loads(RUTA_IDS.read_text())["ids"]
        matriz = np.load(RUTA_VEC).astype(np.float32)
        print(f"Continuando: {len(ids_guardados)} vectores ya guardados.")

    hechos = set(ids_guardados)
    pendientes = [p for p in filas if str(p["product_id"]) not in hechos and textos]

    for i in range(0, len(pendientes), MAX_TEXTOS):
        lote = pendientes[i : i + MAX_TEXTOS]
        ids_lote = [str(p["product_id"]) for p in lote]
        vecs = pedir_embeddings([textos[pid] for pid in ids_lote])
        m = np.asarray(vecs, dtype=np.float32)
        # Normalizar: longitud 1 (producto escalar = similitud coseno).
        normas = np.linalg.norm(m, axis=1, keepdims=True)
        normas[normas == 0] = 1.0
        m = m / normas
        ids_guardados.extend(ids_lote)
        matriz = m if matriz.size == 0 else np.vstack([matriz, m])
        _guardar(ids_guardados, matriz)
        print(f"  {len(ids_guardados)}/{len(filas)} vectores guardados.")
        if i + MAX_TEXTOS < len(pendientes):
            time.sleep(PAUSA_S)  # respetar 60 peticiones/min
    return ids_guardados, matriz


def ya_subidos() -> set[str]:
    """product_id con emb_v1 ya presentes en scratch (no reenviar).

    Si scratch está todavía vacía, aún no tiene columnas y la consulta
    falla: entonces no hay nada subido.
    """
    try:
        filas = query("SELECT product_id FROM team_29.scratch WHERE tipo = 'emb_v1'")
    except RuntimeError:
        return set()
    return {str(f["product_id"]) for f in filas}


def subir_a_scratch(ids: list[str], matriz: np.ndarray) -> int:
    """Sube los vectores a scratch en lotes de 100; devuelve cuántos nuevos."""
    por_indice = {pid: i for i, pid in enumerate(ids)}
    existentes = ya_subidos()
    nuevos = [pid for pid in ids if pid not in existentes]
    total = 0
    for i in range(0, len(nuevos), LOTE_SCRATCH):
        lote_pids = nuevos[i : i + LOTE_SCRATCH]
        filas = [
            {
                "tipo": "emb_v1",
                "product_id": pid,
                "vec": base64.b64encode(matriz[por_indice[pid]].astype(np.float16).tobytes()).decode("ascii"),
            }
            for pid in lote_pids
        ]
        escritas = insertar("scratch", filas)
        total += escritas
        print(f"  scratch: {total}/{len(nuevos)} filas nuevas.")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Embeddings del catálogo (D1).")
    parser.add_argument("--limit", type=int, default=0, help="probar solo con N productos")
    args = parser.parse_args()

    _cargar_env()
    filas = leer_catalogo()
    if args.limit > 0:
        filas = filas[: args.limit]
    print(f"Catálogo: {len(filas)} productos.")

    textos = {str(p["product_id"]): texto_producto(p) for p in filas}
    ids, matriz = calcular_embebiendo(filas, textos)
    print(f"Embeddings: {matriz.shape[0]} vectores de {matriz.shape[1]} dimensiones.")

    total = subir_a_scratch(ids, matriz)
    print(f"Subidos a scratch: {total} nuevos (tipo emb_v1).")


if __name__ == "__main__":
    main()
