# Presentación · mental_descent() · RawTree Real-Time Recommendation

**Estado: sáb ~15:40 BCN. BORRADOR DE TRABAJO.**
⚠️ **Las cifras de resultados NO están aquí todavía**: se añadirán cuando Evaluación y el stream las den
(cada hueco va marcado **[PENDIENTE: resultado]**). Regla del equipo: solo entran números de
`docs/EXPERIMENTOS.md`, de `team_29.feedback` o de consultas ejecutadas — nada inventado.

La presentación tiene dos hilos, entrelazados:

1. **El reto principal**: recomendador en tiempo real que compite con el baseline.
2. **Tukey (side challenge)**: nuestro **dashboard de decisiones** — la herramienta con la que sabremos
   *qué decisión tomar* (subir un peldaño, revertir, explorar). La enseñamos como demo en directo: es
   simultáneamente nuestra sala de máquinas y el side challenge.

---

## 1. El problema en una frase

Una tienda online (~5.000 productos) tiene que elegir, para cada comprador que llega (~3/s, día y noche), los
**10 productos** que mostrar en su fila de recomendados en **menos de 3 s**; un modelo oculto decide si el
comprador compra, y 70 % de la nota es ganarle al baseline los más vendidos.

## 2. Nuestra estrategia: la escalera y por qué (evidencia, no intuición)

Nos apoyamos en `docs/investigacion.md` (deep research con fuentes) y en lo que enseñan los números del
leaderboard público (`docs/competencia.md`):

| Política | Conversión | Interpretación |
|---|---|---|
| Baseline (más vendidos de siempre) | 0,66 % | el 0 de la nota |
| Más vendidos de la categoría de lo último visto | 0,79 % (+20 %) | la categoría sola explica poco |
| Referencia de los organizadores | 2,09 % (+217 %) | se puede triplicar con ticket más caro |

**Lectura (inferencia nuestra, la diremos como tal):** lo que premia el modelo oculto es la *afinidad fina con
la sesión* (subcategoría, marca, rango de precio), no la categoría en bloque; y el precio frena menos de lo
que parece. Y de aquí sale nuestro principio rector — **"generalizar, no memorizar"**: en la final hay
productos y compradores nuevos; por eso todo lo que entra a decidir se calcula por **atributos**
(subcategoría, marca, precio, descripción/embedding), nunca por ID de producto: lo aprendido por ID se queda
huérfano justo cuando más vale (es el principio detrás de DropoutNet, y lo que aplicamos en cold start).

De ahí la escalera (cada peldaño se mide offline antes de subir; en práctica, versiones simples pueden ir
en vivo con el ok del Operador para medir con compradores reales):

```
N0  Baseline en vivo, sin caerse                          → fiabilidad
N1  Co-visitas por sesión + filtro stock/comprados        → la pieza grande
N2  Tendencias (EWMA) + desempate por precio (mediana)    → aprender del stream
N3  Cold start: comprador nuevo (contexto) · producto nuevo (embeddings) → los 10 pts fríos
N4  Aprender de nuestro feedback (parámetros compartidos, no 5.000 brazos)
N5  Reordenar con un modelo (reutiliza TODO lo anterior)
```

**[PENDIENTE: estado actual de la escalera al presentar (qué está en vivo y qué está medido)]**

## 3. El dashboard de Tukey: de cada pregunta a una decisión

Criterio 1 de Tukey es "sirve para decidir". Cada panel del dashboard nace de UNA pregunta del reto y acaba
en UNA decisión que podemos tomar hoy:

| Pregunta del reto | Indicador (panel) | Gráfico | Decisión que alimenta |
|---|---|---|---|
| ¿Convertimos mejor que el baseline? | compras/100 peticiones por versión y por hora | línea con dos series (nuestra v vs baseline) y banda de diferencia | subir el peldaño o **revertir** (regla: 30-60 min en vivo; si no mejora, fuera) |
| ¿Perdemos peticiones? ¿por qué? | % servido por nosotros; fallbacks por motivo; p50/p95 latency | barra apilada por motivo + líneas p95 | robustez: timeout, relevo, relleno por fallback precalculado |
| ¿Dónde miran y dónde compran? | clics y compras por posición 1-10 del carrusel viejo | barras por posición | curva E(k) de posición → ponderación IPS y orden (arriba = probabilidad de compra, no por ingreso) |
| ¿Qué decide la compra? | conversión según subcategoría/marca/rango de precio de lo último visto | barras de lift por atributo | qué features y pesos usa el ranker (afinidad fina, recencia) |
| ¿Cuánto pesa el mundo frío? | % is_new_user, % sin recent_views; conversión del segmento frío | anillo + comparativa de conversión | prioridad del nivel 3 y exposición de exploración a productos nuevos |
| ¿Qué se está moviendo? | EWMA de vistas/carritos/compras por subcategoría (últimos 2 h vs 24 h) | sparklines por subcategoría | subir tendencias al ranking, explorar en slots 8-10 |
| ¿Cómo evoluciona la tienda? | altas, cambios de precio, roturas de stock (catalog_updates) | línea diaria + días de stock | cada cuánto refrescar catálogo y cuándo re-embeber productos nuevos |

> Nota de reglas (sáb ~16:45): una rotura de stock tarda hasta ~2 s en verse en `catalog_updates`, así que
> ahora un producto agotado hace menos de 5 s ya no invalida la respuesta (ese slot no vende, pero el resto
> de la lista cuenta). Cambia cómo interpretamos "invalid" en el panel y alivia la presión del refresco de
> stock en vivo — pero no cambia el objetivo: leer el stock con la cadencia correcta y sin catálogo congelado.

Todo el panel lee de las MISMAS tablas que usan el evaluador y el servicio (`team_29.feedback`,
`shop.events`, `shop.catalog_updates`, `docs/EXPERIMENTOS.md`) → **criterio 3 de Tukey (coherencia)**: lo
que ves en el panel es exactamente el número con el que decidimos; ninguna métrica tiene dos definiciones.

**Prototipo hecho**: `python -m src.panel_tukey` → `data/panel.html` (autocontenido, sin servidor: se abre
en el navegador; `--loop 60` lo refresca cada minuto para la demo). **[PENDIENTE: captura de pantalla para la
diapositiva + números frescos 30 min antes de presentar]**

## 4. Cómo validamos antes de arrisgo — y el episodio que lo demuestra
## 4. Cómo validamos antes de arriesgar (sin fórmulas) — y el episodio que lo demuestra

**La regla que nos salva: confiar en el feedback medido > en la documentación > en los datos exportados.**
Episodio real del sábado: la documentación oficial decía "quien ya compró un producto no lo quiere otra vez",
y el starter filtraba lo comprado. Nuestro evaluador lo midió y dijo lo contrario: **el 61,6 % de las compras
del tramo de test son recompras** — con el filtro, el acierto de compra caía de 0,86 a 0,32. Dejamos de
aplicar el filtro y subimos la conversión; a las ~17:40 los organizadores lo confirmaron con un mensaje al
equipo ("a veces el cliente te da requisitos que no cuadran con los datos... confía en el feedback loop más
que en los docs"). Metodología vengada en menos de 4 horas.

Las herramientas de validación:
- El historial del carrusel está **sesgado** (solo enseñaba lo popular y "algo de azar"). En esos datos,
  "lo popular" siempre parece bueno: evaluar sin corregir decide mal.
- Corrección en palabras (IPS/SNIPS): cada acierto del historial pesa más cuanto menos probable era que lo
  enseñara el sistema viejo (usamos su `propensity`, que viene en los datos).
- Los 10 slots no valen lo mismo: el usuario mira mucho más arriba. Corregimos también por posición (modelo
  de examen por slot), estimado del propio log.
- **Calibración con verdades conocidas**: el estimador tiene que ordenar "categoría de lo último visto"
  (0,79 %) por encima de "más vendidos" (0,66 %) y con la diferencia correcta; si no, no se fía de él.
- Corte temporal (entrenar con días 1-50, validar con 51-60) y **segmento frío** para simular la final.
- En vivo: regla dura — 30-60 min por versión; si la conversión no mejora, se revierte. Nada entra a la
  ronda final sin haber pasado por offline.

**[PENDIENTE: resultados del evaluador offline por versión — Evaluación]**

**Radiografía del leaderboard (sáb ~18:25, API oficial)**: 24/42 equipos, 0,82 % en la ventana; baseline
real ≈ 0,59-0,60; líder (team_06, skore_money) **2,25 %**; referencia de los organizadores 2,09 %. Lo que
se deduce de las técnicas de los demás (por nombres y saltos de versión):
- **Quitar el filtro de comprados** es la palanca más validada: tres equipos lo han hecho explícito
  ("nobought", "no-rebuy", sufijo "-n") y sus conversiones saltaron a 1,6-2,3 %.
- Los de arriba son **co-visitas de sesión + ajuste fino medido en vivo** ("live-weights", "tendencia");
  los de modelos pesados (BPR+XGBoost, LightGBM, logistic a secas) van por debajo de los simples medidos.
- El líder convierte **3,36 % en el segmento frío** (2× su media global): el "frío" no está vacío, son
  compradores con historial que RECOMPRAN. Nuestra respuesta: `frio_historial` (recompras + co-visitas de
  lo que ya compró el comprador) y `filtrar_comprados = False`.

## 5. Lo que ya medimos del stream (hecho, con número)

**La curva de atención por posición** (histórico del carrusel, 96.825 impresiones por posición en 3 días):
el clic cae de 1,08 % en la posición 1 a 0,16 % en la 10 — y la compra de 0,48 % a 0,04 %. Dos conclusiones
directas: el orden importa 7×, y esta curva es la que Alex usa como corrección por posición (IPS).

| pos | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| % clic | 1,08 | 0,58 | 0,42 | 0,32 | 0,28 | 0,25 | 0,23 | 0,21 | 0,18 | 0,16 |
| % compra | 0,48 | 0,23 | 0,16 | 0,12 | 0,10 | 0,09 | 0,07 | 0,06 | 0,06 | 0,04 |

Resto de números medidos:

| Qué | Número | Fuente |
|---|---|---|
| Compradores nuevos (última hora) | 29,79 % | `shop.requests` |
| Sin nada visto en la sesión | 19,82 % | `shop.requests` |
| Catálogo / disponibles | 5.100 / 5.064 | `src/catalogo.py` |
| Altas / agotados / reposiciones / cambios de precio | 100 / 144 / 108 / 1.241 | `shop.catalog_updates` |
| Latencia del streaming de nuestro v0 (p50/p95) | 773 / 1.091 ms (baseline: 1.238 / 2.232) | `team_29.feedback` |
| Vecinos por embedding en la misma subcategoría | 100 % de la lista por diseño (77 % en la versión global anterior) | `src/coldstart.py` |
| Co-visitas heredadas por los productos nuevos | construidas (D6); pendiente de medición con co-visitas reales | `src/coldstart.py` |

(Completar con los resultados de conversión A/B en la final: sección "Resultados" **[PENDIENTE]**.)

## 6. Uso de RawTree (por qué esta pieza decide el reto)

- **Consultas que respetan la base**: columnas `Dynamic` con casteo explícito, SIEMPRE filtro por `ts`, en
  las tablas en vivo; una sentencia por petición. Sabemos qué rompe (columna nueva sobre tabla vacía,
  comparar sin castear) y lo documentamos (`docs/datos.md`).
- **Nada de consultas pesadas en el bucle** (la consulta sin filtro del starter llegó a tardar 10 s): precalcular
  en SQL y servir desde memoria. `recommend()` en <1 ms por petición.
- **`team_29.scratch` = memoria del equipo**: 5.100 embeddings (4096 dims, normalizados, float16+base64)
  compartidos entre el script de embeddings y el servicio, sin ficheros que sincronizar.
- **`catalog_updates` como fuente de altas**: los productos nuevos no están en `shop.catalog`; los
  embebemos desde ahí (`embeber_nuevos()`) para que un producto de hace 5 min ya tenga vecinos.
- **`team_29.feedback` como sala de observación**: conversiones por hora, fallbacks, latencias → es a la vez
  lección y dashboard. RawTree no es solo el almacen: es el canal por el que el stream nos habla.

## 7. Qué haríamos con más tiempo (honesto, priorizado)

**Y lo que NO hicimos a propósito** (la diapositiva honesta — el jurado lo agradece y es 30 % de la nota):
- **Nada de redes neuronales** (GRU4Rec/SASRec): la comparativa de Ludewig/Jannach (2020) muestra que en
  sesiones los métodos por vecinos compiten con las redes, que además cuestan 8+ horas que no teníamos.
- **Nada de bandit por producto**: en la final hay ~21.600 peticiones y ~430 compras — no hay señal para
  aprender 5.000 brazos; por eso solo aprenderíamos *parámetros compartidos* (Thompson descontado).
- **No empezáramos por el modelo grande**: los equipos que fueron directos a gradient boosting fallaron por
  memorizar el sesgo del log y por latencia (61 % a tiempo) — es la ilustración exacta de "memorizar no
  generaliza". Nuestro ranker (si llega) reutiliza candidatos, curva de posición y features, nada se tira.

Con más tiempo, en orden de valor:
1. **Reordenar con LightGBM** sobre los mismos candidatos y features (features por atributos: subcategoría,
   marca, cubo de precio, similitud del embedding con la sesión, tendencia). Los ganadores de OTTO/RecSys
   hacen exactamente esto en dos etapas — pero solo cuando los peldaños simples están medidos.
2. **Aprendizaje online de pocos parámetros** (Thompson descontado sobre λ de precio y η de tendencia): en
   la final hay ~21.600 peticiones y ~430 compras — no hay señal para aprender 5.000 brazos.
3. **Prueba A/B de hipótesis del modelo oculto** (misma subcategoría vs precio vs re-visita) por hash de
   user_id — la señal más valiosa y más barata.
4. **Redirección al panel**: alertas automáticas del dashboard (caída de conversión, subida de fallbacks)
   para reaccionar en la final sin vigilar manualmente.

---

### Apéndice · plan de la demo (sábado noche / domingo tarde)

- 1 min: problema + escalera (diapositiva 2).
- 2 min: dashboard en directo sobre `feedback` real (diapositiva 3): conversión de nuestra versión vs
  baseline en la última hora y decisión que tomamos con ello.
- 1 min: producto nuevo creado hoy → vecinos por descripción en `scratch` (demo del cold start).
- 1 min: cómo validamos (diapositiva 4) + qué medimos del stream (diapositiva 5).
- Cierre: uso de RawTree (diapositiva 6) + lo que falta por hacer.

**Revisión con el equipo: punto de control de las 21:00.** Dueño: Artai (Datos y relato).
Fuentes de "resultados" cuando llenemos los PENDIENTE: `docs/EXPERIMENTOS.md` (offline, Alex) y
`team_29.feedback` (en vivo, Lester/Operador).
