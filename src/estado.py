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

from src import catalogo, coldstart, covisitas, tendencias, usuarios


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
    tendencias: dict[str, float] = field(default_factory=dict)
    """product_id -> lift (src/tendencias.py): >1 = más actividad que de costumbre en la última media hora."""

    # --- Datos y relato (src/coldstart.py) ---
    populares_contexto: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    """(device, region) -> más vendidos para ese tipo de comprador. Para usuarios nuevos."""
    vecinos: dict[str, list[str]] = field(default_factory=dict)
    """product_id -> productos con descripción parecida (embeddings). Para productos nuevos."""
    perfiles_comprador: dict[str, dict] = field(default_factory=dict)
    """user_id -> {"categorias", "subcategorias", "marcas", "precio_mediano", "compras"}: perfil de R6
    (lo rellena coldstart.cargar_perfiles desde team_29.scratch; construir_perfiles lo sube)."""
    perfiles_producto: dict[str, dict] = field(default_factory=dict)
    """product_id -> {"ensenado", "comprado", "tasa_ips"}: tasa de compra corregida por propensity (R6)."""


def por_categoria(cat: dict[str, dict], populares: list[str]) -> dict[str, list[str]]:
    """Reparte la lista de populares por categoría, conservando el orden."""
    salida: dict[str, list[str]] = {}
    for pid in populares:
        categoria = cat.get(pid, {}).get("category")
        if categoria:
            salida.setdefault(str(categoria), []).append(pid)
    return salida


def construir(hasta: str | None = None) -> Estado:
    """Calcula el Estado completo. Offline: hasta='YYYY-MM-DD'. En vivo: hasta=None.

    Los perfiles de R6 vienen de team_29.scratch; si el corte no está construido ahí,
    ejecuta antes `python -m src.coldstart` (en vivo) o `construir_perfiles(hasta)` (offline).
    """
    cat = catalogo.cargar_catalogo(hasta)
    pop = catalogo.cargar_populares(hasta)
    perfiles_comprador, perfiles_producto = coldstart.cargar_perfiles(hasta)
    if not perfiles_comprador and not perfiles_producto:
        # este corte no está construido en scratch: se construye AHORA con el mismo corte
        # (respeta `hasta`, no mira el futuro) y se recarga; solo pasa la primera vez.
        perfiles_comprador, perfiles_producto = coldstart.construir_perfiles(hasta)
    return Estado(
        catalogo=cat,
        disponibles=catalogo.disponibles(cat),
        populares=pop,
        populares_categoria=por_categoria(cat, pop),
        covisitas=covisitas.cargar_covisitas(hasta),
        comprados=usuarios.cargar_comprados(hasta),
        tendencias=tendencias.cargar_tendencias(hasta),
        populares_contexto=coldstart.cargar_populares_contexto(hasta),
        vecinos=coldstart.cargar_vecinos(hasta),
        perfiles_comprador=perfiles_comprador,
        perfiles_producto=perfiles_producto,
    )
