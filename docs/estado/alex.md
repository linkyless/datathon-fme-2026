# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 18:20

## Haciendo ahora
- Siguiente (plan de la noche): test offline sin recompras y medir R6 (LightGBM) de Leonardo contra C, la puerta para
  ir en vivo. Después, reconciliar con Artai 61,6 % frente a 100 % de recompras.
- **A/B en vivo, lectura de las 18:10** (cada brazo frente a C en los MISMOS minutos, mi panel + IC 95 %):
  | A/B | Min. | Compras/100 C → brazo | Clics | €/pet. C → brazo | Veredicto |
  |---|---|---|---|---|---|
  | C frente a D (co-visitas) | 16 | 1,01 → 0,75 (−26 %, suerte) | −41 %, **real** | 1,06 → 0,44 | Fuera |
  | C frente a E (re-visitas 0.3) | 12 | 0,88 → 0,88 | −13 %, suerte | 0,40 → 0,25 | No aportan |
  | **C frente a CF (quitar lo ya comprado)** | **42** | **1,18 → 0,66 (−44 %)**, IC [−1,07, +0,03] | −13 %, suerte | 0,19 → 0,22 | **Casi significativo: CF vende menos** |
  C/CF es el A/B con más datos (6.659 peticiones). La ventana de Lester (CF 1,19 % frente a 1,02 %) tenía solo 494 de CF.
- **Fiabilidad**: ~80 % a tiempo en C y CF (16:29-17:10 UTC). Causa: la wifi (estado de Lester).

## Terminado
- IPS10 en evaluar.py (IPS en las 10 posiciones, tope 50, con bootstrap) y lectura del A/B por minutos comunes:
  C (v0) sigue siendo la mejor (EXPERIMENTOS 17:40).
- E4: `evaluar.py` imprime, para cada variante, la diferencia frente a v0, starter y la actual con su intervalo
  de confianza del 95 % (bootstrap emparejado, 200) y un veredicto: sube / baja / puede ser suerte / idéntico.
- E2 (v1): medida `e-samen-v1` y sus piezas por separado. **NO APTA**: purchase_hit 0.16 vs 0.86 de la v0.
  Culpable principal: el filtro de comprados. `evaluar.py` acepta ya variantes sueltas (`VARIANTES`).
- E3: `python -m src.panel` (`--horas`, `--minutos`): por versión y tramo, % servido, tarde, inválidas, clics,
  compras, €/petición y latencia p95. Solo lee; tarda ~1 s. Es nuestro leaderboard en la final.
- E2 (primera ronda): `evaluar.py` prueba ya TODAS las combinaciones de interruptores de `ranking.PARAMS`, en paralelo
  (~7 min). Veredicto: `usar_vecinos` y `usar_contexto` **NO APTOS** de momento (números en EXPERIMENTOS). Dejarlos apagados.
- `src/catalogo.py` probado contra RawTree (lectura): 5100 productos, 5063 disponibles; el filtro `hasta` funciona.
- E1: `src/evaluar.py` (`python -m src.evaluar`). Test y Estado se guardan en `data/` (no se sube). Primera fila en EXPERIMENTOS.

## Para el modelo de Leonardo (gradient boosting): qué sabemos de cada señal (medido)
Lo medido es si cada pieza sirve como REGLA para ordenar la lista entera. Un árbol decide él mismo los pesos: no
quitar variables con información.
| Señal (variable por producto candidato) | Qué hemos medido | Para el árbol |
|---|---|---|
| Ya comprado por este usuario | Histórico (CSV): 61,6 % de las compras del test son recompras. EN VIVO (lo que manda, según el organizador: "trust the feedback loop more than the docs, and the docs more than a CSV"): quitarlo (CF) vende un 44 % menos que C, casi significativo (42 min). Pero ponerlo 1.º tampoco funciona: R5 lo ponía 1.º en 366 de 600 peticiones y convirtió 0,47 % | **Incluirla, pero con el peso VIGILADO**: tiene señal real, y el CSV la exagera. Comprobar que el árbol no la pone siempre 1.ª (el fallo de R5). Nunca filtrarla |
| Popular en la categoría de lo último visto | La v0 (solo esto) gana a todo, offline y en vivo (A/B C) | **Imprescindible** |
| Popularidad global | Baseline del starter: 0.54 purchase_hit (v0: 0.86) | Útil (base) |
| Ya visto en esta sesión (re-visita) | Cuando se enseñaba, 1,96 compras/100 huecos vs 0,12 (Leonardo). Como regla: −4 % IPS10 offline, igual en vivo | Útil como variable, no como regla |
| Puntuación de co-visita | Como regla pierde (clics −41 % en vivo, real) | Dudosa: dejarla, pesará poco si no sirve |
| Contexto (device, región) | Tras la mezcla de Artai no cambia nada; antes, efecto no significativo | Probablemente poco útil |
| Vecinos por descripción | El historial no tiene productos nuevos: no medible offline | Sin datos para entrenar; útil solo en vivo (frío) |
| Tendencia (última media hora) | A/B T frente a C (13 min): compras 7 vs 8, clics −13 %, nada significativo | Sin datos suficientes |
| (Modelos M / MT / MTF, regresión logística) | En vivo por debajo de C en clics (MT −52 %, real); compras sin diferencia significativa (4-5 por brazo) | Aún no ganan a la v0 |
| Precio relativo (frente a lo visto y al precio habitual del comprador) | Medido por Lester (3.530 compras): se compra lo MÁS BARATO de lo afín (×0,55 de lo visto, ×0,53 del habitual). R5 aprendió "caro = mejor" (sesgo del carrusel antiguo) | **Útil, como precio RELATIVO**, no absoluto |
| Misma subcategoría / marca que lo visto | Medido por Lester: 97,6 % de las compras son de la categoría de lo último visto; dentro, misma subcategoría 34 % (azar ~11 %), misma marca 22 % | Útil |

**Cómo leerla:** las señales que fallaron EN VIVO fallaron como regla única (D, E) o por un peso exagerado (R5 con
`ya_comprado`). Para el árbol la puerta es la misma que para todo: medido offline con `python -m src.evaluar`
(con test sin recompras) y después A/B frente a C. C es la versión a batir.

**Ojo al entrenar:** el historial solo tiene etiquetas de lo que el carrusel antiguo enseñó (sesgado a lo popular).
Ponderar por 1/propensity (con tope, p. ej. 50), como hace la R5, y validar con `python -m src.evaluar` (IPS10 + bootstrap).

## Descubierto (cosas que los demás deben saber)
- **Recompras, definición de mis cifras (para Artai):** de las 1.422 compras de mi muestra de test (100.000 visitas con
  ts >= 2026-11-07), 876 (61,6 %) son de un producto que ese usuario compró ANTES del corte según
  `usuarios.cargar_comprados(hasta='2026-11-07')` (compras de shop.history_events). Lester midió lo mismo sobre las 3.530
  compras del test: 62 %. El 100 % de Artai debe de usar otra ventana o tabla: comparar consultas.
- **Recompra: qué dice de verdad el organizador.** Su mensaje es una escala de confianza: feedback en vivo >
  documentación > CSV. No dice "en vivo no hay recompras" ni "el modelo premia recomprar": dice que mande el
  feedback. Y nuestro feedback (C frente a CF, 42 min) dice: **no quitar lo ya comprado** (−44 %, casi
  significativo). Si conviene EMPUJARLO, no está medido. Las métricas offline con recompras están infladas por el CSV.
- El segmento frío del test solo tiene 35 compras: el purchase_hit frío es ruido; mirar el hit_rate frío.
- **En `feedback`, "late" no aparece nunca**: lo que enviamos tarde sale como `fallback_reason = 'missing'`
  aunque esté en `submissions`. El panel lo cuenta como "tarde".
- **Fiabilidad sáb 16:00-16:30: ~10 % de lo que enviamos llega tarde** (202 de 1890 en 16:15, p95 2251 ms) → 89-91 %
  servido, justo en el umbral del 90 % de la luz verde. Para Lester.
- `shop.history_sessions` no tiene `is_new_user` y el contexto viene aplanado (`context.device`, `context.region`,
  `context.page`). `clicked` y `purchased` son el product_id (o vacío). En evaluar.py "frío" = sin recent_views.
- Ojo con el hit_rate: el sistema antiguo enseñaba sobre todo populares de la categoría, así que solo se pudo clicar
  lo que enseñó. Por eso baseline_categoria saca 0.76 de hit_rate pero un SNIPS más bajo que baseline_starter.

## Necesito de alguien
- **Leonardo (R6, árbol)**: ver la tabla de señales de arriba. CORRIGE lo que puse antes: "ya comprado" SÍ va al
  árbol (en vivo quitarlo vende −44 %), pero vigila su peso (que no quede siempre 1.º, el fallo de R5). Precio
  RELATIVO, subcategoría y marca como señales nuevas. Ponderar por 1/propensity. No filtrar nunca lo comprado.
- **Lester**: CF (filtro) lleva el 75 % del tráfico y en el A/B con más datos vende un 44 % menos que C (casi
  significativo). Propongo volver a C, o al menos invertir el reparto (C 75 %, CF 25 %). Fila en EXPERIMENTOS 18:20.
- **Artai**: 1) en tu análisis del leaderboard, CF es CON filtro (`_CF = {**_V0, "_filtrar_comprados": True}`,
  `src/servicio.py:59`), no "sin filtro". 2) El mensaje del organizador dice "fíate del feedback", no "el modelo
  premia recomprar": corrígelo en `docs/presentacion.md`. 3) `frio_historial = 1.2` encendido por defecto en
  `ranking.py` (archivo de Leonardo) empuja lo comprado al 1.º puesto en los fríos: no está medido y es lo que hundió
  a R5. Debería ir APAGADO por defecto hasta un A/B (C frente a C + frio_historial).
