# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 20:40

## Haciendo ahora
- **Modelo nuevo listo para un A/B: `src/aprendiz.py`** (regresión logística que aprende SOLO de `team_29.feedback`).
  Entrenado y validado, NO está en vivo: falta conectarlo (Lester/Leonardo, ver "Necesito de alguien") y medirlo
  como brazo frente a C. Números en EXPERIMENTOS (sáb 20:30). Reentrenar: `python -m src.aprendiz --ensayo 3000`.
- Por qué: llevábamos horas cambiando QUÉ producto popular va primero. Lo que mueve la compra en el simulador es la
  relación previa del comprador con el producto (carrito, visto, ya comprado) y con su categoría. Ver "Descubierto".
- **CORRECCIÓN mía (sáb 20:30): el filtro de comprados SÍ es correcto.** Mi "CF vende −44 %" de abajo tenía un
  intervalo que incluía el 0, y el feedback de todo el día dice lo contrario: de 5.240 veces que enseñamos algo
  comprado hoy, 1 compra; de 35.746 de algo comprado en el historial, 4. Lo ya comprado no se enseña nunca.
- A/B en vivo, lectura de las 18:10 (cada brazo frente a C en los MISMOS minutos, mi panel + IC 95 %):
  | A/B | Min. | Compras/100 C → brazo | Clics | €/pet. C → brazo | Veredicto |
  |---|---|---|---|---|---|
  | C frente a D (co-visitas) | 16 | 1,01 → 0,75 (−26 %, suerte) | −41 %, **real** | 1,06 → 0,44 | Fuera |
  | C frente a E (re-visitas 0.3) | 12 | 0,88 → 0,88 | −13 %, suerte | 0,40 → 0,25 | No aportan |
  | **C frente a CF (quitar lo ya comprado)** | **42** | **1,18 → 0,66 (−44 %)**, IC [−1,07, +0,03] | −13 %, suerte | 0,19 → 0,22 | **Casi significativo: CF vende menos** |
  C/CF es el A/B con más datos (6.659 peticiones). La ventana de Lester (CF 1,19 % frente a 1,02 %) tenía solo 494 de CF.
- **Fiabilidad**: ~80 % a tiempo en C y CF (16:29-17:10 UTC). Causa: la wifi (estado de Lester).

## Terminado
- **`src/aprendiz.py` (sáb 20:30)**: modelo de 11 señales entrenado con 87.058 respuestas nuestras (763 compras).
  Pesos (× probabilidad relativa de compra): carrito de hoy sin comprar ×6,1 · visto en la sesión ×5,9 · carrito del
  historial sin comprar ×4,2 · ya comprado ×0,12 · afinidad con la categoría ×25 (de nada a todo) · afinidad con la
  subcategoría ×2,5 · misma categoría que lo último visto ×2,6 · tasa de compra del producto ×3,4 · popularidad ×1,6.
  Validación por tiempo (último 30 %, 26.118 respuestas, 261 compras): 14,1 % mejor que mirar solo el puesto;
  calibración en el 20 % de listas más prometedoras 3,41 predicho frente a 3,68 real. Ensayo de `recomendar()` sobre
  4.000 peticiones reales: 0 inválidas, 0 con algo ya comprado, p50 1,4 ms, máx 7,8 ms.
  Estimación DEL MODELO (no medición) en compras/100: lo que enseñamos 1,02 → lista nueva 1,82.
  Con 15.000 respuestas valida igual que con 61.000 (13,9 % frente a 14,1 %), y un LightGBM con las mismas señales
  da el mismo error (0,00658 frente a 0,00657): no hace falta complicarlo todavía.
- Análisis del feedback del día cruzado con lo que hizo cada comprador antes (77.680 respuestas, 652 compras): ver
  "Descubierto". Solo lectura.
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
**OJO (sáb 20:30): la fila "Ya comprado" de esta tabla ya NO vale** (en vivo ×0,12: se quita siempre) y la de
"precio relativo" tampoco (en vivo el precio no cambia la compra: ×1,0; lo de "mitad de precio" era porque lo
popular es barato). Las señales que más pesan en vivo no están en esta tabla: están en "Descubierto".
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
- **Lo que premia el simulador (feedback de hoy hasta las 18:28 UTC, 776.800 productos enseñados, 652 compras):**
  - Hueco 1, compra por producto enseñado: en su carrito de HOY sin comprar 2,5 % (785 casos) · en un carrito del
    historial sin comprar 1,18 % (2.796) · sin relación previa 0,22 %.
  - Afinidad de categoría (hueco 1, compradores con historial, productos sin relación previa): si la categoría es
    ≤ 5 % de sus vistas 0,01 % (12.715 casos); 10-20 % → 0,07 %; 20-40 % → 0,12 %; > 40 % → 0,83 % (1.806).
  - Visto en el historial y nunca metido al carrito: baja (×0,4 en la regresión). Lo descartó.
  - Cobertura: el 50 % de las peticiones tiene algún carrito de hoy sin comprar (48 % de las frías); el 95,5 % de
    las no frías tiene carritos del historial (9,5 de media); el 96,3 % tiene al menos una de estas señales.
- **"Frío" no es "sin vistas"**: el 83 % de las peticiones frías trae `recent_views`, y esos compradores tienen
  actividad de hoy en `shop.events`. En `evaluar.py` "frío" = sin recent_views: mide otra cosa.
- **El árbol empeora a los fríos** (API del leaderboard, 17:27-18:27 UTC): fríos C 1,93 · CF 1,57 · A6 1,35 ·
  A7 1,25. No fríos: C 0,59 · CF 0,62 · A6 0,88 · A7 0,76. Los punteros: fríos 2,3-3,1, no fríos 1,4-2,0.
- **El sesgo de puesto en bruto engaña**: a igual contenido el hueco 2 vale ×0,68 del 1.º y el 10.º ×0,35 (en bruto
  parecía 0,45 y 0,09). Los 10 huecos cuentan.
- **`populares` sale solo de `history_events`**: hoy `p_548697` está entre los más vendidos y tiene 0 ventas en el
  historial. En la final casi todo será nuevo.
- **`estado.comprados` se refresca cada 30 min** (`pesadas`, 1.800 s en `servicio.py`): durante ese rato seguimos
  enseñando lo recién comprado.
- **Fiabilidad**: 20.109 de 77.680 respuestas del día salieron como `missing`.
- En esta máquina `git config core.hooksPath` no está puesto y `.githooks/pre-commit` no es ejecutable: el bloqueo
  de claves no corre solo (lo he pasado a mano antes del commit).
- **Recompras, definición de mis cifras (para Artai):** de las 1.422 compras de mi muestra de test (100.000 visitas con
  ts >= 2026-11-07), 876 (61,6 %) son de un producto que ese usuario compró ANTES del corte según
  `usuarios.cargar_comprados(hasta='2026-11-07')` (compras de shop.history_events). Lester midió lo mismo sobre las 3.530
  compras del test: 62 %. El 100 % de Artai debe de usar otra ventana o tabla: comparar consultas.
- **Recompra: qué dice de verdad el organizador.** Su mensaje es una escala de confianza: feedback en vivo >
  documentación > CSV. No dice "en vivo no hay recompras" ni "el modelo premia recomprar": dice que mande el
  feedback. ~~Y nuestro feedback (C frente a CF, 42 min) dice: no quitar lo ya comprado (−44 %)~~ **CORREGIDO sáb
  20:30**: con el feedback de todo el día, lo ya comprado casi nunca se vuelve a comprar (comprado hoy: 1 compra
  en 5.240 veces enseñado; comprado en el historial: 4 en 35.746). Se quita siempre. Las métricas offline con recompras están infladas por el CSV.
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
- **Lester (Operador)**: conectar `src/aprendiz.py` como brazo A/B frente a C (propongo C al 25 % de control; ahora
  A7 va al 100 % y no se puede comparar con nada en la misma ventana). Son cinco líneas, están en la cabecera del
  archivo: `aprendiz.Mundo.al_dia(estado.catalogo)` al arrancar (19 s medido), `mundo.ponerse_al_dia()` cada 1-2 s
  en el hilo de refresco (una consulta de 0,2-1 s; deja el estado 2,7 s por detrás del servidor),
  `mundo.catalogo = estado.catalogo` al recargar el catálogo y `aprendiz.recomendar(peticion, mundo, modelo,
  estado.disponibles)`. Antes de arrancar, en tu máquina: `python -m src.aprendiz` (baja el historial una vez,
  ~2 min, y crea `data/aprendiz.json`). Probarlo primero con `--sin-envio`.
- **Lester**: retiro lo de "volver a C / invertir el reparto": el filtro de comprados es correcto (ver corrección).
  Y refrescar `comprados` más a menudo que cada 30 min (con `Mundo` va al día solo, evento a evento).
- **Leonardo (Ranking)**: retiro lo de "no filtrar nunca lo comprado". Si prefieres llamarlo desde `ranking.py` con
  una clave de `PARAMS` en vez de desde el servicio, dilo y lo adaptamos. Para el árbol: las señales que más pesan
  en vivo son carrito de hoy, carrito antiguo, visto en la sesión y afinidad de categoría/subcategoría (todas
  salen de `aprendiz.senales`); sus candidatos hoy son solo la categoría de lo último visto, así que un carrito de
  otra categoría no puede salir.
- **Artai**: 1) en tu análisis del leaderboard, CF es CON filtro (`_CF = {**_V0, "_filtrar_comprados": True}`,
  `src/servicio.py:59`), no "sin filtro". 2) El mensaje del organizador dice "fíate del feedback", no "el modelo
  premia recomprar": corrígelo en `docs/presentacion.md`. 3) `frio_historial = 1.2` encendido por defecto en
  `ranking.py` (archivo de Leonardo) empuja lo comprado al 1.º puesto en los fríos: no está medido y es lo que hundió
  a R5. Debería ir APAGADO por defecto hasta un A/B (C frente a C + frio_historial).
