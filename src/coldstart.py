"""Arranque en frío: compradores nuevos y productos nuevos. Dueño: Datos y relato.

D3 y D4 de docs/guias/datos.md. Los embeddings en sí los calcula src/embeddings.py.
"""
from __future__ import annotations

import base64
import time

import numpy as np

from src.embeddings import MAX_TEXTOS, PAUSA_S, pedir_embeddings, texto_producto
from src.rawtree import insertar, query

VECINOS = 20


def cargar_populares_contexto(hasta: str | None = None) -> dict[tuple[str, str], list[str]]:
    """(device, region) -> los más comprados por ese tipo de comprador, de más a menos (máx. 50).

    `hasta` ('YYYY-MM-DD'): usar solo datos con ts < hasta. None = todo (en vivo).
    """
    filtro = f" AND ts::DateTime64(3) < '{hasta}'" if hasta else ""
    filas = query(
        "SELECT device::String d, region::String r, product_id::String pid, count() c "
        "FROM shop.history_events "
        f"WHERE event_type = 'purchase'{filtro} "
        "GROUP BY d, r, pid "
        "ORDER BY d, r, c DESC, pid "          # pid desempata: orden estable
        "LIMIT 50 BY d, r"
    )
    salida: dict[tuple[str, str], list[str]] = {}
    for f in filas:
        salida.setdefault((f["d"], f["r"]), []).append(f["pid"])
    return salida


def cargar_vecinos(hasta: str | None = None) -> dict[str, list[str]]:
    """product_id -> los 20 productos con descripción más parecida (por embeddings), del más al menos parecido.

    Lee los embeddings de team_NN.scratch (los calcula src/embeddings.py), los renormaliza
    y devuelve los 20 vecinos con mayor producto escalar (descartando el propio producto).
    `hasta` ('YYYY-MM-DD'): solo considera productos con created_at < hasta (los embeddings
    de scratch son de hoy, pero así la evaluación offline no ve productos futuros).
    """
    filas = query("SELECT product_id::String pid, vec FROM team_29.scratch WHERE tipo = 'emb_v1'")
    if not filas:
        return {}
    decodificados = {
        f["pid"]: np.frombuffer(base64.b64decode(f["vec"]), dtype=np.float16).astype(np.float32)
        for f in filas
        if f["pid"] and f["vec"]  # ignora filas rotas (p. ej. product_id vacío) por si quedara alguna
    }
    if hasta:  # quitar productos creados después del corte (created_at < hasta)
        creados = {
            f["product_id"]
            for f in query(f"SELECT product_id FROM shop.catalog WHERE created_at < '{hasta}'")
        }
        decodificados = {pid: v for pid, v in decodificados.items() if pid in creados}
    ids = list(decodificados)
    matriz = np.vstack([decodificados[p] for p in ids])
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    matriz = matriz / normas
    # Similitud = producto escalar (vectores de longitud 1). 5000x5000 en un golpe.
    sim = matriz @ matriz.T
    k = min(VECINOS + 1, len(ids))
    vecinos: dict[str, list[str]] = {}
    for i, pid in enumerate(ids):
        candidatos = np.argpartition(-sim[i], k - 1)[:k]
        candidatos = candidatos[candidatos != i]
        orden = candidatos[np.argsort(-sim[i][candidatos])][:VECINOS]
        vecinos[pid] = [ids[j] for j in orden]
    return vecinos


def embeber_nuevos() -> int:
    """Embebe los 'new_product' de catalog_updates que aún no están en scratch y los sube.

    Devuelve cuántos productos nuevos se embeberon. El servicio (Operador) la llama
    cada ~5 minutos para que los productos nuevos tengan vecinos en caliente.
    """
    nuevos = [
        f
        for f in query(
            "SELECT product_id::String pid, category, subcategory, brand, description, ts "
            "FROM shop.catalog_updates WHERE change = 'new_product' "
            "ORDER BY ts::DateTime64(3) ASC"  # un producto puede tener varias filas: gana la última
        )
    ]
    ultimo: dict[str, dict] = {}
    for f in nuevos:
        ultimo[f["pid"]] = f
    ya_estan = {
        f["product_id"]
        for f in query("SELECT product_id::String product_id FROM team_29.scratch WHERE tipo = 'emb_v1'")
    }
    pendientes = [f for pid, f in ultimo.items() if pid and pid not in ya_estan]
    if not pendientes:
        return 0

    filas_out = []
    for i in range(0, len(pendientes), MAX_TEXTOS):
        lote = pendientes[i : i + MAX_TEXTOS]
        vectores = pedir_embeddings([texto_producto(f) for f in lote])
        for f, v in zip(lote, vectores):
            vec = np.asarray(v, dtype=np.float32)
            norma = float(np.linalg.norm(vec)) or 1.0
            vec = vec / norma
            filas_out.append((f, vec))
        if i + MAX_TEXTOS < len(pendientes):
            time.sleep(PAUSA_S)
    insertar(
        "scratch",
        [
            {
                "tipo": "emb_v1",
                "product_id": f["pid"],
                "vec": base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii"),
            }
            for f, v in filas_out
        ],
    )
    return len(filas_out)
