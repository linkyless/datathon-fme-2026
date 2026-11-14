"""EL CONTRATO del equipo. Dueño: Operador. Si cambias un campo, avisa a los cuatro.

`Estado` es todo lo que `ranking.recommend()` puede usar para decidir. Se calcula FUERA del bucle en vivo
(consultas pesadas), se guarda en memoria y se refresca cada cierto tiempo. Dentro de `recommend()` no se
hace ninguna consulta: solo se leen estos campos.

Cada campo tiene un dueño que escribe la función que lo rellena, siempre con la misma forma:
`cargar_xxx(hasta: str | None) -> valor`. `hasta` ('YYYY-MM-DD') corta los datos en esa fecha para la
evaluación offline (así no se hace trampa mirando el futuro). En vivo se llama con `hasta=None`.

Mientras una función no esté hecha devuelve un valor vacío y `recommend()` sigue funcionando con lo demás.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src import catalogo, coldstart, covisitas, usuarios


@dataclass
class Estado:
    # --- Operador (src/catalogo.py) ---
    catalogo: dict[str, dict] = field(default_factory=dict)
    """product_id -> {"category", "subcategory", "brand", "price_eur", "is_active", ...}"""
    disponibles: set[str] = field(default_factory=set)
    """Productos que se pueden recomendar AHORA (activos y en stock). Recomendar otro invalida la respuesta."""
    populares: list[str] = field(default_factory=list)
    """Más vendidos de siempre, de más a menos. Es el fallback oficial y el relleno de emergencia."""
    populares_categoria: dict[str, list[str]] = field(default_factory=dict)
    """category -> más vendidos de esa categoría. Es el baseline de las diapositivas (docs/apertura.md).
    Se calcula a partir de `catalogo` y `populares` (sin consultas): ver `por_categoria()`."""

    # --- Ranking (src/covisitas.py, src/usuarios.py) ---
    covisitas: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    """product_id -> [(otro_product_id, peso), ...] de mayor a menor peso. "Quien vio X vio/compró Y"."""
    comprados: dict[str, set[str]] = field(default_factory=dict)
    """user_id -> productos que ya compró (no se los volvemos a enseñar)."""

    # --- Datos y relato (src/coldstart.py) ---
    populares_contexto: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    """(device, region) -> más vendidos para ese tipo de comprador. Para usuarios nuevos."""
    vecinos: dict[str, list[str]] = field(default_factory=dict)
    """product_id -> productos con descripción parecida (embeddings). Para productos nuevos."""


def por_categoria(cat: dict[str, dict], populares: list[str]) -> dict[str, list[str]]:
    """Reparte la lista de populares por categoría, conservando el orden."""
    salida: dict[str, list[str]] = {}
    for pid in populares:
        categoria = cat.get(pid, {}).get("category")
        if categoria:
            salida.setdefault(str(categoria), []).append(pid)
    return salida


def construir(hasta: str | None = None) -> Estado:
    """Calcula el Estado completo. Offline: hasta='YYYY-MM-DD'. En vivo: hasta=None."""
    cat = catalogo.cargar_catalogo(hasta)
    pop = catalogo.cargar_populares(hasta)
    return Estado(
        catalogo=cat,
        disponibles=catalogo.disponibles(cat),
        populares=pop,
        populares_categoria=por_categoria(cat, pop),
        covisitas=covisitas.cargar_covisitas(hasta),
        comprados=usuarios.cargar_comprados(hasta),
        populares_contexto=coldstart.cargar_populares_contexto(hasta),
        vecinos=coldstart.cargar_vecinos(hasta),
    )
