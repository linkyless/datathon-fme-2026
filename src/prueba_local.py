"""Prueba rápida SIN RED que cada uno ejecuta antes de hacer commit. Dueño: Operador.

    python -m src.prueba_local

Comprueba que todos los módulos se importan (si alguien rompe un archivo, salta aquí) y que
`ranking.recommend()` devuelve respuestas válidas con un Estado de juguete, con y sin piezas rellenas.
"""
from __future__ import annotations

import time

from src import catalogo, coldstart, covisitas, estado, ranking, servicio, usuarios  # noqa: F401  (que importen)
from src.estado import Estado
from src.validar import errores_respuesta


def _estado_juguete(con_piezas: bool) -> Estado:
    ids = [f"p_{i:03d}" for i in range(40)]
    e = Estado(
        catalogo={p: {"category": "c", "price_eur": 10.0, "is_active": True} for p in ids},
        disponibles=set(ids[:35]),                 # p_035..p_039 agotados
        populares=list(reversed(ids)),             # los agotados van primero: deben saltarse
    )
    if con_piezas:
        e.covisitas = {"p_001": [("p_036", 9.0), ("p_002", 5.0), ("p_003", 1.0)]}
        e.comprados = {"u_1": {"p_002"}}
        e.populares_contexto = {("mobile", "ES"): ["p_010", "p_011"]}
        e.vecinos = {"p_nuevo": ["p_020", "p_021"]}
    e.catalogo["p_001"]["category"] = "zapatos"
    e.populares_categoria = {"zapatos": ["p_037", "p_030"]}   # p_037 agotado: debe saltarse
    return e


def main() -> None:
    peticiones = [
        {"request_id": "r1", "user_id": "u_1", "context": {"device": "mobile", "region": "ES"},
         "recent_views": [{"product_id": "p_001", "ts": "x"}]},
        {"request_id": "r2", "user_id": "u_2", "is_new_user": True, "context": {}, "recent_views": []},
        {"request_id": "r3", "user_id": "u_3", "recent_views": [{"product_id": "p_nuevo"}]},
        {"request_id": "r4"},                                                   # petición casi vacía
    ]
    for con_piezas in (False, True):
        e = _estado_juguete(con_piezas)
        for p in peticiones:
            inicio = time.perf_counter()
            # El filtro de comprados se pasa EXPLÍCITO: en producción está apagado por defecto (medido:
            # recomprar es lo normal), pero aquí se comprueba que sigue funcionando cuando se enciende.
            items = ranking.recommend(p, e, {"filtrar_comprados": True})
            ms = (time.perf_counter() - inicio) * 1000
            errs = errores_respuesta(items, e.disponibles)
            assert not errs, f"{p['request_id']} (piezas={con_piezas}): {errs}"
            assert ms < 50, f"{p['request_id']}: recommend tarda {ms:.1f} ms"
            if not con_piezas and p["request_id"] == "r1":
                assert items[0] == "p_030", f"sin piezas, primero los populares de su categoría: {items[:3]}"
            if con_piezas and p["request_id"] == "r1":
                assert "p_002" not in items, "recomienda algo que el usuario ya compró"
                assert items[0] == "p_003", f"la co-visita disponible debería ir primera: {items[:3]}"
    print("prueba_local: todo OK (módulos importan y recommend da respuestas válidas)")


if __name__ == "__main__":
    main()
