# Guía de Ranking (Leonardo · Claude Code)

**Tu misión:** que los 10 productos que enseñamos sean los que más probabilidad tienen de venderse, y en el mejor
orden. Eres el dueño de la parte que más puntos da (conversión: 50).

**Tus archivos:** `src/ranking.py`, `src/covisitas.py`, `src/usuarios.py` y, más adelante, `src/tendencias.py`.
**No toques:** el resto. Para probar una idea NO lances el servicio: la mide Evaluación con el historial.

**Antes de empezar:** sigue la Parte 2 de `docs/GUIA.md`, lee `docs/guias/README.md` (el contrato) y abre
`src/ranking.py`: ya funciona y ya sabe usar tus piezas en cuanto existan. Mira también cómo hace las
co-visitas `starter/offline_eval.ipynb`.

**Con quién te cruzas (y con nadie más):**
- **Alex** mide tus versiones. Tú le avisas ("mide e-samen-v1"); él te devuelve números. No le esperes: mientras
  mide, sigue con la tarea siguiente.
- **Lester** solo si necesitas un campo nuevo en el `Estado` (tarea R4). Lo pides por tu archivo de estado.
- Con Artai no te cruzas: sus piezas (`populares_contexto`, `vecinos`) ya las usa `ranking.py` solas cuando llegan.

---

## R1 · Co-visitas — hasta las 14:30
Pega esto a tu agente:
> Lee docs/guias/README.md, src/estado.py, src/ranking.py y la celda de co-visitas de starter/offline_eval.ipynb.
> Rellena `cargar_covisitas(hasta)` en src/covisitas.py: para cada producto, los 20 productos que más aparecen en
> las mismas sesiones de shop.history_events (usa `src.rawtree.query`). Respeta `hasta` (`ts < hasta`). Pesa el
> tipo de evento del segundo producto como en OTTO (docs/investigacion.md, punto 5): view 1, add_to_cart 6,
> purchase 3. Solo pares a menos de 24 h el uno del otro. Divide cada peso por (popularidad del segundo producto)^0.5
> para no recomendar siempre lo popular. Devuelve `{product_id: [(otro, peso), ...]}` ordenado de mayor a menor peso. La consulta es pesada (12M filas): usa
> timeout=600 y guarda el resultado en data/covisitas_<hasta>.json para no repetirla (data/ no se sube).
> Pruébala con el corte del notebook (último día de shop.history_sessions menos 7 días; calcúlalo con SQL), dime cuántos productos tienen co-visitas y enséñame las de uno.
> Después ejecuta `python -m src.prueba_local`.

**Hecho cuando:** devuelve co-visitas para la mayoría de productos y `prueba_local` pasa. Commit y push. Luego
escribe en tu estado: *"Evaluación: mide e-samen-v1 (co-visitas) contra el baseline"* y cambia `NOMBRE` en ranking.py a
`"e-samen-v1"` (y apunta en docs/EXPERIMENTOS.md que v1 = co-visitas).

## R2 · No repetir lo ya comprado — hasta las 15:15
Pega esto a tu agente:
> Rellena `cargar_comprados(hasta)` en src/usuarios.py: `{user_id: set(product_id)}` con las compras
> (event_type = 'purchase') de shop.history_events con `ts < hasta`. Si `hasta` es None, añade también las compras
> de shop.events de las últimas 24 horas. `ranking.py` ya filtra con esto. Ejecuta `python -m src.prueba_local`.

**Hecho cuando:** devuelve compradores con sus compras y la prueba pasa. Commit, push y aviso a Evaluación.

## R3 · Re-visitas y afinidad fina — de 15:30 a 18:00 (lo que más sube según el deep research)
Una idea cada vez, medida por Evaluación antes de la siguiente. Cambia `NOMBRE` en cada versión (`e-samen-v2`, `e-samen-v3`…) y apunta en EXPERIMENTOS qué es cada una. El nombre sale en el leaderboard público: nunca pongas en él lo que hace.
Añade cada idea como una clave de `PARAMS` (p. ej. `"revisitas": True`) para que el Operador pueda probarla en A/B.
1. **Re-visitas:** los productos que ya ha visto en esta sesión y NO ha comprado, como candidatos con mucho peso
   (los más recientes primero). En OTTO era la señal más fuerte.
2. **Misma subcategoría y marca:** puntos extra a los candidatos que comparten subcategoría (90 posibles) o marca
   con lo visto. El informe sospecha que el modelo oculto premia esa afinidad fina más que la categoría.
3. **Precio coherente:** puntos extra si el precio se parece al de lo que ha visto. Y desempate:
   `puntos × (1 + 0.05 · log(precio))` (el desempate oficial es el ingreso; la referencia enseña cosas caras).
4. `decaimiento`: probar 0.5 y 0.9.

Pega a tu agente: *"Lee el punto 5 de docs/investigacion.md. Implementa la idea N en src/ranking.py como una clave
nueva de PARAMS, sin tocar nada más. Ejecuta prueba_local y dime en dos frases qué cambia."*
## R4 · Tendencias de la última hora (nivel 2) — de 19:00 a 22:00
Pide al Operador el campo `tendencias` en el `Estado`. Después, pega a tu agente:
> Crea src/tendencias.py con `cargar_tendencias(hasta)`: los productos con más compras y vistas en la última
> hora de shop.events (consultas 2 y 3 de starter/queries.sql). Usa el "lift" del punto 9 de docs/investigacion.md:
> media de los últimos 15-30 min dividida por la media larga (+ un prior). Con `hasta` dado, lo mismo antes de esa
> fecha en shop.history_events. En ranking.py, multiplica la puntuación de los candidatos por lift^0.3 (recortado).

Esto es difícil de medir offline: díselo a Evaluación y medidlo en vivo con el panel (tarea E3).

---

## Fase 2 (del deep research: docs/investigacion.md, puntos 6 y fichas T6/T10)
**Solo cuando R1-R3 estén en vivo y Alex tenga el evaluador calibrado.** En este orden:

**R5 · Una puntuación explicable: regresión logística (sáb noche).** En vez de sumar puntos a ojo, un modelo
pequeño aprende cuánto vale cada señal. Es una sola "neurona" con sigmoide: la conoces de PyTorch.
> Lee el punto 3 y la ficha T6 de docs/investigacion.md. Crea src/puntuacion.py: una regresión logística
> (scikit-learn) que prediga si un candidato se compra a partir de features que existen también para productos
> NUEVOS: misma subcategoría/marca que lo visto, puntos de co-visita, ¿es re-visita?, log(precio), precio relativo
> al de la sesión, device, region, page. NUNCA el product_id como feature. Entrena con datos anteriores al corte.
> Enséñame los coeficientes y explícame qué significa cada uno.

Los coeficientes son oro para el jurado: "esto es lo que hemos aprendido de cómo compra la gente".

**R6 · El "modelo grande": LightGBM (dom madrugada, solo si R5 funciona).** Lo mismo con árboles en vez de una
neurona: capta combinaciones ("caro pero de lo que miro"). Mismas features, mismos candidatos.
**Riesgo alto** (los equipos que fueron directos a esto van por debajo del baseline). Solo entra si Alex dice que
mejora con intervalos de confianza que no se solapan.

**No hacer:** redes neuronales de secuencias (GRU4Rec, SASRec) ni bandits por producto. El informe concluye que
con este tiempo y estos datos no compensan.