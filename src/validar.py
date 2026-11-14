"""Validador de respuestas: comprueba que una lista de recomendaciones es válida ANTES de enviarla.

Una respuesta inválida se sustituye por el fallback (puntúa 0 en esa petición) y baja la fiabilidad.

    python -m src.validar      # ejecuta los autotests
"""
from __future__ import annotations

from collections.abc import Iterable


def errores_respuesta(items: object, activos: Iterable[str] | None = None) -> list[str]:
    """Lista de problemas de una respuesta. Lista vacía = respuesta válida.

    `activos`: conjunto de product_id activos y en stock ahora mismo. Si es None, no se comprueba el stock.
    """
    if not isinstance(items, list):
        return [f"items debe ser una lista, es {type(items).__name__}"]
    errores = []
    if len(items) != 10:
        errores.append(f"debe tener 10 productos, tiene {len(items)}")
    no_texto = [i for i in items if not isinstance(i, str) or not i]
    if no_texto:
        errores.append(f"IDs que no son texto o están vacíos: {no_texto[:3]}")
    repetidos = sorted({i for i in items if isinstance(i, str) and items.count(i) > 1})
    if repetidos:
        errores.append(f"IDs repetidos: {repetidos}")
    if activos is not None:
        activos = set(activos)
        fuera = [i for i in items if isinstance(i, str) and i not in activos]
        if fuera:
            errores.append(f"IDs no activos o agotados: {fuera}")
    return errores


def errores_lote(respuestas: list[dict], activos: Iterable[str] | None = None) -> dict[str, list[str]]:
    """Valida un lote de submissions. Devuelve {request_id: errores} solo de las inválidas."""
    activos = set(activos) if activos is not None else None
    malos: dict[str, list[str]] = {}
    vistos: set[str] = set()
    for r in respuestas:
        rid = r.get("request_id", "<sin request_id>")
        errs = errores_respuesta(r.get("items"), activos)
        if rid in vistos:
            errs.append("request_id duplicado en el lote")
        vistos.add(rid)
        if errs:
            malos[rid] = errs
    return malos


def _autotest() -> None:
    ok = [f"p_{i}" for i in range(10)]
    assert errores_respuesta(ok) == []
    assert errores_respuesta(ok, activos=ok) == []
    assert errores_respuesta(ok[:9]) != []                      # faltan productos
    assert errores_respuesta(ok[:9] + ["p_0"]) != []            # repetido
    assert errores_respuesta(ok, activos=ok[:9]) != []          # uno agotado
    assert errores_respuesta("p_1") != []                       # no es lista
    lote = [{"request_id": "r1", "items": ok}, {"request_id": "r1", "items": ok}]
    assert list(errores_lote(lote)) == ["r1"]                   # duplicado
    print("validar: todos los autotests pasan")


if __name__ == "__main__":
    _autotest()
