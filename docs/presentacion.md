# Presentación · mental_descent() · RawTree Real-Time Recommendation

**Estado: dom ~04:30. BORRADOR para el checkpoint de las 08:00.**
Regla del equipo: solo entran números de `docs/EXPERIMENTOS.md`, de `team_29.feedback` o de consultas
ejecutadas — nada inventado. Los huecos van marcados **[PENDIENTE]**.

La presentación tiene dos hilos, entrelazados:

1. **El reto principal**: recomendador en tiempo real que compite con el baseline.
2. **Tukey (side challenge)**: nuestro **dashboard de decisiones** — la herramienta con la que sabremos
   *qué decisión tomar* (subir un peldaño, revertir, explorar). Demo en directo: es a la vez nuestra sala
   de máquinas y el side challenge.

---

## 1. El problema en una frase

Una tienda online (~5.000 productos) tiene que elegir, para cada comprador que llega (~3/s, día y noche), los
**10 productos** que mostrar en su fila de recomendados en **menos de 3 s**; un modelo oculto decide si el
comprador compra, y el 70 % de la nota es ganarle al baseline los más vendidos.

## 2. La historia real: cómo pasamos del baseline a un modelo que aprende del stream

No partíamos de una teoría: partimos de **medir cada peldaño** y subir solo el que ganaba. Nuestro día:

| Hora | Versión en vivo | Qué enseñó |
|---|---|---|
| 12:27 | baseline del starter | más vendidos de siempre en stock (0,61 % en su ventana) |
| 14:08 | **e-samen-v0** | más vendidos de la categoría de lo último visto; 0 duplicados en el relevo |
| 16:51-19:07 | v1-A/B/C/D/E/T/M | co-visitas, re-visitas, tendencias, regresión — **cada idea, un brazo medido**; co-visitas como regla: fuera (clics −41 % real); R5 aprendió sesgos del historial: fuera |
| 18:51-19:07 | A6 → **A7** | árbol LightGBM sobre lo visto (R6) + sin la pieza que restaba a los fríos |
| 20:47-23:47 | AP2 → **AP3** | **el aprendiz**: regresión logística entrenada SOLO con nuestro feedback en vivo (no con el carrusel) — AP2 ganó a C por +1,20 pts [IC +0,56, +1,87] |
| 21:43-23:16 | EXP1/EXP1x | exploración de huecos 9-10: medimos su coste (−0,25/−0,35 en las que exploran) y la apagamos |
| 00:05-… | **cuartos AP3/AP4/AP5/AP6** | el mismo modelo con 4 órdenes distintos, a medir esta noche |

La escalera en la que encaja (todas las piezas son reutilizables; nada se tira):

```
N0  Baseline en vivo, sin caerse                          → fiabilidad (10 pts)
N1  Co-visitas de sesión (como SEÑAL, no como regla)      → conversión (50 pts)
N2  Tendencias (lift 30 min) · recencia · stock en vivo   → aprender del stream
N3  Cold start: comprador nuevo (contexto/perfiles) · producto nuevo (embeddings) → 10 pts fríos
N4  El aprendiz: pesos reentrenados SOLO con nuestro feedback (AP2 → AP3)
N5  R6 LightGBM "elección en el carrusel" (lambdarank) + favorecedor para los fríos
```

## 3. El dashboard de Tukey: de cada pregunta a una decisión

Criterio 1 de Tukey es "sirve para decidir". Cada panel nace de UNA pregunta del reto y acaba en UNA
decisión que podemos tomar hoy:

| Pregunta del reto | Indicador (panel) | Decisión que alimenta |
|---|---|---|
| ¿Convertimos mejor? ¿qué versión gana? | conversión por versión y tramo (feedback unido a las respuestas por request_id) | subir el peldaño o **revertir** (regla: 30-60 min en vivo; si no mejora, fuera) |
| ¿Perdemos peticiones? ¿por qué? | % servido, fallbacks por motivo, p50/p95 de latencia | robustez: timeout, relevo, relleno por fallback precalculado |
| ¿Dónde miran y dónde compran? | clics y compras por posición 1-10 (carrusel viejo) | curva de posición → corrección IPS y el orden (arriba = probabilidad de compra, no ingreso) |
| ¿Qué decide la compra? | conversión según carrito/visto/afinidad de categoría del comprador | qué señales entran al aprendiz y con qué peso (ojo al peso: R5 lo aprendió mal sin este panel) |
| ¿Cuánto pesa el mundo frío? | % is_new_user, % sin recent_views; conversión fría vs cálida | prioridad del especialista de fríos y cuánta exploración se permite |
| ¿Qué se está moviendo? | vistas 2 h vs 2 h previas por subcategoría | si la tendencia entra al ranking y con qué η |
| ¿Cómo evoluciona la tienda? | altas, cambios de precio, roturas de stock | cadencia de refresco del catálogo y de re-embebido de productos nuevos |

> Nota de reglas (sáb ~16:45): una rotura de stock tarda hasta ~2 s en verse en `catalog_updates`, así que
> ahora un producto agotado hace menos de 5 s ya no invalida la respuesta (ese slot no vende, pero el resto
> de la lista cuenta). Cambia cómo interpretamos "invalid" en el panel y alivia la presión del refresco de
> stock en vivo — pero no cambia el objetivo: leer el stock con la cadencia correcta.

Todo el panel lee de las MISMAS tablas que usan el evaluador y el servicio (`team_29.feedback`,
`shop.events`, `shop.catalog_updates`) → **criterio 3 de Tukey (coherencia)**: lo que ves es exactamente el
número con el que decidimos; ninguna métrica tiene dos definiciones.

**Prototipo hecho**: `python -m src.panel_tukey` → `data/panel.html` (autocontenido, sin servidor; `--loop 60`
lo refresca cada minuto para la demo). **[PENDIENTE: captura para la diapositiva + números frescos 30 min
antes de presentar]**

## 4. Cómo validamos antes de arriesgar — y los dos episodios que lo demuestran

**La regla que nos salvó dos veces: confiar en el feedback medido > en la documentación > en los datos
históricos.**

**Episodio 1 — la recompra.** La pista oficial decía "quien ya compró un producto no lo quiere otra vez"; el
histórico decía lo contrario (61,6 % de las compras del test eran recompras); el mensaje de los organizadores
de las 17:40 vino a decir que el modelo oculto puede no estar de acuerdo **con ninguno de los dos** ("trust
the feedback loop more than the docs, and the docs more than a CSV"). Lo resolvió el feedback de TODO el día:
de 5.240 veces que enseñamos algo comprado hoy, 1 compra; de 35.746 de algo comprado en el historial, 4 —
**lo ya comprado no se enseña nunca** (×0,12 en la regresión). El filtro de comprados es correcto.

**Episodio 2 — la fuga de futuro.** Nuestra primera "categoría favorita del comprador" parecía multiplicar la
compra ×8-11. Era **fuga** (leakage): se medía con la actividad de hoy POSTERIOR a la respuesta, incluida la
propia compra. Sin fuga (solo historial anterior): ×2,2. La versión con fuga restaba (−21 % en fríos); la
corregida, ya en A7, dejó de restar. El aprendiz, además, reconstruye el estado de cada comprador **pasando
el día en orden evento a evento con 3 s de retraso**: jamás ve el futuro.

Herramientas de validación:
- El historial está **sesgado** (el carrusel viejo solo enseñaba lo popular): corrección por propensity
  (IPS/SNIPS) y por posición (hueco 2 vale ×0,68 del 1.º en bruto calibrado, no ×0,45).
- **Corte temporal** (entrenar con el 70 % inicial del día, validar con el 30 % final) e **intervalos de
  confianza con bootstrap** (200-400 remuestreos): "sube" solo cuando el IC no incluye el cero.
- **Cada idea, un brazo A/B en vivo** comparado en los MISMOS minutos (16 co-visitas, 12 re-visitas, 42 el
  filtro, 15 la exploración…).
- Y una regla nueva del día: **medir qué señal prefieren los compradores orgánicos** (recall@10 sobre compras
  orgánicas: subcategoría de lo último visto 20,5 %; afinidad de categoría 9,2 %; co-compra 14,6 %;
  vecinos por embedding 5,2-9,9 % — descartados como fuente de candidatos).

**[PENDIENTE: resultados del evaluador offline por versión — Evaluación]**

## 5. Lo que ya medimos del stream (hecho, con número)

**La curva de atención por posición** (carrusel histórico, 96.825 impresiones por posición en 3 días): el
clic cae de 1,08 % en la posición 1 a 0,16 % en la 10, y la compra de 0,48 % a 0,04 %. El orden importa ~7×.

| pos | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| % clic | 1,08 | 0,58 | 0,42 | 0,32 | 0,28 | 0,25 | 0,23 | 0,21 | 0,18 | 0,16 |
| % compra | 0,48 | 0,23 | 0,16 | 0,12 | 0,10 | 0,09 | 0,07 | 0,06 | 0,06 | 0,04 |

**Qué premia el simulador** (nuestro feedback del día, 776.800 productos enseñados, 652 compras — hueco 1):
en su carrito de HOY sin comprar **2,5 %** · en carrito del historial sin comprar **1,18 %** · sin relación
previa **0,22 %**. Y la afinidad del comprador con la categoría escalona la compra de 0,01 % a **0,83 %**.
Esa tabla es exactamente la que alimenta las 11 señales del aprendiz.

Resto de números medidos:

| Qué | Número | Fuente |
|---|---|---|
| Compradores nuevos (última hora) | 29,79 % | `shop.requests` |
| "Frío" ≠ "sin vistas": el 83 % de las frías trae vistas | 1,25 | `shop.requests` (medición de Alex) |
| Catálogo / disponibles | 5.100 / 5.064 | `src/catalogo.py` |
| Altas / agotados / reposiciones / cambios de precio | 100 / 144 / 108 / 1.241 | `shop.catalog_updates` |
| Latencia de nuestro servicio (p50/p95) | 773 / 1.091 ms (baseline: 1.238 / 2.232) | `team_29.feedback` |
| El aprendiz: AUC 0,885 (solo puesto: 0,718); el comprado 1.º en el 40,8 % de las respuestas con compra | +14,1 % de error frente a solo posición | `src/aprendiz.py` |
| El aprendiz gana a C en vivo | 2,03 % vs 0,83 % [+0,56, +1,87] | A/B de Lester, feedback |
| Vecinos por embedding en la misma subcategoría | 100 % de la lista por diseño | `src/coldstart.py` |

**[PENDIENTE: tabla final de resultados por versión en la mañana de la final]**

## 6. Uso de RawTree (por qué esta pieza decide el reto)

- **Consultas que respetan la base**: columnas `Dynamic` con casteo explícito, SIEMPRE filtro por `ts`, una
  sentencia por petición. Documentamos lo que rompe (`docs/datos.md`): tabla vacía sin columnas, `hasta`
  inferido como Date, comparar sin castear.
- **Nada de consultas pesadas en el bucle** (la consulta sin filtro del starter llegó a 10 s): precalcular en
  SQL y servir desde memoria. `recommend()` en <1 ms; el aprendiz, 1,4 ms p50.
- **`team_29.scratch` = memoria del equipo**: 5.100 embeddings (4096 dims, normalizados, float16+base64) y
  perfiles de comprador/producto compartidos entre scripts y servicio, sin ficheros que sincronizar.
- **`catalog_updates` como fuente de altas**: los productos nuevos no están en `shop.catalog`; los embebemos
  desde ahí (`embeber_nuevos()`) para que un producto de hace 5 min ya tenga vecinos.
- **`team_29.feedback` como sala de observación**: es la tabla que reentrena el aprendiz cada pocas horas y a
  la vez alimenta el dashboard. RawTree no es solo el almacén: es el canal por el que el stream nos habla —
  y por el que aprendimos más rápido que con cualquier CSV.

## 7. Qué haríamos con más tiempo (honesto, priorizado)

**Lo que NO hicimos a propósito** (la diapositiva honesta — el jurado lo agradece y es 30 % de la nota):
- **Nada de redes neuronales de sesión** (GRU4Rec/SASRec): en la comparativa de Ludewig/Jannach (2020) los
  métodos por vecinos compiten, y cuestan 8+ h que no teníamos; un LightGBM con las señales del aprendiz dio
  el mismo error que la regresión (0,00658 vs 0,00657): no hace falta complicarlo.
- **Nada de bandits por producto ni Thompson**: en la final hay ~21.600 peticiones y ~430 compras — no hay
  señal para 5.000 brazos; y la exploración en huecos 9-10 la MEDIMOS en vivo: cuesta 0,15-0,35 puntos donde
  explora. Por eso solo aprendemos parámetros compartidos y reentrenamos en lote.
- **Nada de "más datos = mejor" sin IC**: probamos 22 señales del historial sobre AP3 y solo 4-6 pasaron el
  intervalo (exposición previa +0,91 %, afinidad por compras +0,32 %, vistas 14 días +0,23 %, carrito ×2
  +0,21 %; juntas +1,66 %). El resto, fuera aunque suene bien.

Con más tiempo, en orden de valor:
1. **Especialista de fríos**: el frío no es "sin vistas" — el 83 % de las frías trae vistas y hay señales
   nuevas para ellos (la de tasa en vivo, AP8, ya está especificada para Evaluación).
2. **El blend de dos cabezas** (árbol R6 + aprendiz, entrenados con datos distintos: carrusel vs feedback):
   sus errores se compensan; validación offline primero, brazo después.
3. **Recargar los modelos sin reiniciar el servicio** (hoy los pesos solo se leen al arrancar): reentrenar
   cada hora en vivo sin tocar el tráfico.
4. **Redirección al panel**: alertas automáticas (caída de conversión, subida de fallbacks) para reaccionar
   en la final sin vigilar manualmente.

---

### Apéndice · plan de la demo (domingo tarde)

- 1 min: problema + la historia de un día (diapositiva 2: cada peldaño, su medición).
- 2 min: dashboard en directo sobre `feedback` real (diapositiva 3): conversión por versión en la última
  hora y la decisión que tomamos con ello (incluir el caso real de revertir la exploración).
- 1 min: producto nuevo creado hoy → vecinos por descripción en `scratch` (demo del cold start).
- 1 min: cómo validamos (diapositiva 4, con los dos episodios: recompra y fuga).
- 1 min: qué medimos del stream (diapositiva 5: curva de posición + qué premia el simulador).
- Cierre: uso de RawTree (diapositiva 6) + lo honesto (diapositiva 7).

**Revisión: checkpoint de las 08:00 (congelado).** Dueño: Artai (Datos y relato).
Fuentes para llenar los [PENDIENTE]: `docs/EXPERIMENTOS.md` (offline y en vivo) y `team_29.feedback`.
