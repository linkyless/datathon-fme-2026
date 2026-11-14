# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 17:50

## Haciendo ahora
- Siguiente, por orden (lo que me habéis pedido):
  1. **Lester**: test offline SIN las compras de productos ya comprados (en vivo no hay recompras) y re-medir todo.
  2. **Leonardo**: medir R5 (`usar_modelo`) y, cuando la reentrenes sin `ya_comprado`, la nueva.
  3. **Artai**: reconciliar 61,6 % (mío) frente a 100 % (tuyo) de recompras.
- **A/B C frente a CF (respuesta a Lester): NO se puede confirmar CF > C.** Mi panel, 15 min comunes (sáb ~17:30-17:43):
  C 603 pet. / CF 1322. Compras C 7 (1,16/100) frente a CF 8 (0,61/100), IC 95 % de la diferencia [−1,51, +0,40]
  → puede ser suerte. Clics −27 % (suerte). €/petición C 0,156 frente a CF 0,314. Tu ventana 16:39-17:39 daba lo
  contrario (CF 1,19 % frente a C 1,02 %): con 7-8 compras por brazo, cada ventana dice una cosa. Hace falta ≥ 1 h más.
- **Fiabilidad, URGENTE (Lester)**: en esos mismos 15 min, servido a tiempo C 79,6 % y CF 72,7 %. Por debajo del 90 %.

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
| Ya comprado por este usuario | En el HISTÓRICO, 61,6 % de las compras del test son recompras y filtrarlas hunde purchase_hit offline 0.86 → 0.32. Pero el organizador avisa: "trust the feedback loop more than the docs, and the docs more than a CSV" → la recompra del histórico es un artefacto del CSV. En vivo, CF (filtro) frente a C: 3 vs 3 compras en 10 min, sin diferencia significativa | **No significativa (confirmado en el stand). NO usarla para entrenar**: en el histórico domina y el árbol aprendería el artefacto |
| Popular en la categoría de lo último visto | La v0 (solo esto) gana a todo, offline y en vivo (A/B C) | **Imprescindible** |
| Popularidad global | Baseline del starter: 0.54 purchase_hit (v0: 0.86) | Útil (base) |
| Ya visto en esta sesión (re-visita) | Cuando se enseñaba, 1,96 compras/100 huecos vs 0,12 (Leonardo). Como regla: −4 % IPS10 offline, igual en vivo | Útil como variable, no como regla |
| Puntuación de co-visita | Como regla pierde (clics −41 % en vivo, real) | Dudosa: dejarla, pesará poco si no sirve |
| Contexto (device, región) | Tras la mezcla de Artai no cambia nada; antes, efecto no significativo | Probablemente poco útil |
| Vecinos por descripción | El historial no tiene productos nuevos: no medible offline | Sin datos para entrenar; útil solo en vivo (frío) |
| Tendencia (última media hora) | A/B T frente a C (13 min): compras 7 vs 8, clics −13 %, nada significativo | Sin datos suficientes |
| (Modelos M / MT / MTF, regresión logística) | En vivo por debajo de C en clics (MT −52 %, real); compras sin diferencia significativa (4-5 por brazo) | Aún no ganan a la v0 |
| Precio | No medido | No medido |

**Ojo al entrenar:** el historial solo tiene etiquetas de lo que el carrusel antiguo enseñó (sesgado a lo popular).
Ponderar por 1/propensity (con tope, p. ej. 50), como hace la R5, y validar con `python -m src.evaluar` (IPS10 + bootstrap).

## Descubierto (cosas que los demás deben saber)
- **Recompras, definición de mis cifras (para Artai):** de las 1.422 compras de mi muestra de test (100.000 visitas con
  ts >= 2026-11-07), 876 (61,6 %) son de un producto que ese usuario compró ANTES del corte según
  `usuarios.cargar_comprados(hasta='2026-11-07')` (compras de shop.history_events). Lester midió lo mismo sobre las 3.530
  compras del test: 62 %. El 100 % de Artai debe de usar otra ventana o tabla: comparar consultas.
- **Recompra: el histórico engaña.** El 61,6 % de las compras del test son de algo ya comprado antes, pero los
  organizadores confirman que en vivo la recompra NO es significativa ("trust the feedback loop more than the docs,
  and the docs more than a CSV"). Las métricas offline que dependen de recompras (purchase_hit con o sin filtro)
  están infladas por ese artefacto: decidir con el A/B en vivo.
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
- **Leonardo**: para el gradient boosting, ver la tabla de señales de arriba. Sobre todo: NO entrenar con "ya
  comprado" (artefacto del histórico) y ponderar por 1/propensity.
- **Artai**: el commit `30fb29c` y `docs/presentacion.md` dicen que el organizador "confirma que el modelo premia
  recomprar". Es al revés: confirma que NO es significativa. Corrígelo en la presentación (el archivo es tuyo).
