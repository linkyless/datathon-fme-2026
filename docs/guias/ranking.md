# Guía de Ranking (Leonardo · Claude Code)

**Tu misión:** que los 10 productos que enseñamos sean los que más probabilidad tienen de venderse, y en el mejor
orden. Eres el dueño de la parte que más puntos da (conversión: 50).

**Tus archivos:** `src/ranking.py`, `src/covisitas.py`, `src/usuarios.py` y, más adelante, `src/tendencias.py`.
**No toques:** el resto. Para probar una idea NO lances el servicio: la mide Evaluación con el historial.

**Antes de empezar:** sigue la Parte 2 de `docs/GUIA.md`, lee `docs/guias/README.md` (el contrato) y abre
`src/ranking.py`: ya funciona y ya sabe usar tus piezas en cuanto existan. Mira también cómo hace las
co-visitas `starter/offline_eval.ipynb`.

**Con quién te cruzas (y con nadie más):**
- **Alex** mide tus versiones. Tú le avisas ("mide v1-covisitas"); él te devuelve números. No le esperes: mientras
  mide, sigue con la tarea siguiente.
- **Lester** solo si necesitas un campo nuevo en el `Estado` (tarea R4). Lo pides por tu archivo de estado.
- Con Artai no te cruzas: sus piezas (`populares_contexto`, `vecinos`) ya las usa `ranking.py` solas cuando llegan.

---

## R1 · Co-visitas — hasta las 14:30
Pega esto a tu agente:
> Lee docs/guias/README.md, src/estado.py, src/ranking.py y la celda de co-visitas de starter/offline_eval.ipynb.
> Rellena `cargar_covisitas(hasta)` en src/covisitas.py: para cada producto, los 20 productos que más aparecen en
> las mismas sesiones de shop.history_events (usa `src.rawtree.query`). Respeta `hasta` (`ts < hasta`). Pesa el
> tipo de evento del segundo producto: view 1, add_to_cart 3, purchase 6. Devuelve
> `{product_id: [(otro, peso), ...]}` ordenado de mayor a menor peso. La consulta es pesada (12M filas): usa
> timeout=600 y guarda el resultado en data/covisitas_<hasta>.json para no repetirla (data/ no se sube).
> Pruébala con el corte del notebook (último día de shop.history_sessions menos 7 días; calcúlalo con SQL), dime cuántos productos tienen co-visitas y enséñame las de uno.
> Después ejecuta `python -m src.prueba_local`.

**Hecho cuando:** devuelve co-visitas para la mayoría de productos y `prueba_local` pasa. Commit y push. Luego
escribe en tu estado: *"Evaluación: mide covisitas v1 contra el baseline"* y cambia `NOMBRE` en ranking.py a
`"v1-covisitas"`.

## R2 · No repetir lo ya comprado — hasta las 15:15
Pega esto a tu agente:
> Rellena `cargar_comprados(hasta)` en src/usuarios.py: `{user_id: set(product_id)}` con las compras
> (event_type = 'purchase') de shop.history_events con `ts < hasta`. Si `hasta` es None, añade también las compras
> de shop.events de las últimas 24 horas. `ranking.py` ya filtra con esto. Ejecuta `python -m src.prueba_local`.

**Hecho cuando:** devuelve compradores con sus compras y la prueba pasa. Commit, push y aviso a Evaluación.

## R3 · Afinar el orden — de 15:30 a 19:00
Una idea cada vez, medida por Evaluación antes de la siguiente. Cambia `NOMBRE` en cada versión (`v2-...`, `v3-...`).
Ideas en orden de facilidad:
1. `DECAIMIENTO` (cuánto pesa lo visto hace rato frente a lo último): probar 0.5 y 0.9.
2. Subir un poco los productos de la misma categoría o subcategoría que lo último que vio.
3. ¿Recomendar lo que acaba de ver? Probar a quitarlo y a dejarlo.
4. Desempate por precio: con puntuación parecida, mejor el más caro (el desempate oficial es el ingreso).

Pega a tu agente: *"Implementa la idea N en src/ranking.py sin tocar nada más, ejecuta prueba_local y dime en dos
frases qué cambia."*

## R4 · Tendencias de la última hora (nivel 2) — de 19:00 a 22:00
Pide al Operador el campo `tendencias` en el `Estado`. Después, pega a tu agente:
> Crea src/tendencias.py con `cargar_tendencias(hasta)`: los productos con más compras y vistas en la última
> hora de shop.events (consultas 2 y 3 de starter/queries.sql). Con `hasta` dado, la última hora antes de esa
> fecha en shop.history_events. En ranking.py, súmales puntos a los candidatos que estén en tendencia.

Esto es difícil de medir offline: díselo a Evaluación y medidlo en vivo con el panel (tarea E3).

---

## Fase 2 (tras el deep research)
_Vacío. Aquí irán las ideas avanzadas (reordenar con un modelo, mejores co-visitas, bandits…). No empezar hasta que esté escrito._
