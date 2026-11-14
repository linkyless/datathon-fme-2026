# El reto: RawTree · Real-Time Recommendation

Fuentes oficiales: https://datathon.rawtree.dev/ · starter kit: https://github.com/rawtreedb/datathon-fme-2026

## El problema
Una tienda online con ~5.000 productos en 10 categorías y 90 subcategorías. En la home y en cada ficha de producto
hay una fila de 10 recomendados. Hoy muestra los más vendidos de siempre. Llegan ~3 compradores por segundo,
día y noche. Para cada uno tenemos **3 segundos** para elegir 10 productos ordenados. Un modelo oculto simula si
hace clic y si compra. Nuestro sistema tiene que funcionar solo, sin nadie pulsando botones.

## Las tablas
| Tabla | Qué contiene | Tamaño |
|---|---|---|
| `shop.catalog` | product_id, category, subcategory, brand, price_eur, description, created_at, is_active | ~5k |
| `shop.history_sessions` | 60 días del carrusel antiguo: context, recent_views, `shown` (10), `propensity` (10), clicked, purchased, revenue_eur | ~2M |
| `shop.history_events` | 60 días de actividad fuera del carrusel: ts, event_type (view/add_to_cart/purchase), user_id, session_id, product_id, price_eur, device, region | ~12M |
| `shop.requests` | En vivo: request_id, ts, user_id, is_new_user, context (device, region, page), recent_views [{product_id, ts}] | ~3/s |
| `shop.events` | En vivo, mismo formato que history_events: tendencias en tiempo real | ~50/s |
| `shop.catalog_updates` | En vivo: producto completo + ts + change (new_product, price_change, out_of_stock, back_in_stock) | |
| `team_NN.feedback` | Solo nuestro: request_id, ts, shown, served_by (team/fallback), fallback_reason (missing/late/invalid), latency_ms, clicked, purchased, revenue_eur | |
| `team_NN.scratch` | Tabla libre nuestra (features, embeddings). Se inserta JSON sin esquema | |
| `team_NN.submissions` | Donde escribimos las respuestas | |

Notas:
- El catálogo actual = `shop.catalog` con la última actualización de cada producto encima (consulta 1 de `starter/queries.sql`).
- El carrusel antiguo era "más vendidos + algo de azar": el historial está **sesgado hacia lo popular**.
  `propensity` = probabilidad que tenía el sistema antiguo de mostrar ese producto en esa posición. Sirve para corregir el sesgo (IPS).
- En `feedback`, `clicked`/`purchased` valen `""` si no pasó nada. La primera fila (`request_id = "__init__"`) es de relleno: ignórala.

## Qué enviamos
```json
[{"request_id": "r_01JD8K3...", "items": ["p_91aa", "...10 en total..."], "model_version": "v3-coview"}]
```
- Exactamente 10 IDs distintos y activos. Si uno está agotado, la respuesta ENTERA es inválida.
- Antes de 3 s desde `ts` (el reloj de los organizadores mira cada 0,5 s). Tarde o inválido → se muestra el fallback.
- Una sola respuesta por petición. Enviar en lotes (cada ~0,5 s), no una llamada por petición.
- El orden importa: los compradores miran mucho más las primeras posiciones.
- Referencia: el baseline oficial consulta cada 0,3 s y responde en ~1 s (95 % en < 1,5 s).

## Calendario (hora de Barcelona; los datos están en UTC)
- Sáb 10:00 → Dom 10:00: práctica con leaderboard público cada 10 min. No puntúa.
- **Dom 10:00 → 12:00: ronda final, con datos NUEVOS** (compradores, productos y tendencias nuevos). Leaderboard oculto:
  solo una luz verde/roja (verde si > 90 % de respuestas válidas y a tiempo en los últimos 5 min).
- Se puede cambiar el código durante la final, pero vamos a ciegas: solo vemos nuestro `feedback`.
- Dom 12:00: ranking final automático. Dom tarde: presentación + demo.

## Puntuación
Final = 70 % de la nota del stream + hasta 30 del jurado.

| Parte | Criterio | Puntos |
|---|---|---|
| Stream (70) | Conversión: compras esperadas por 100 peticiones | 50 |
| | Cold start: lo mismo solo en peticiones frías | 10 |
| | Fiabilidad: % de respuestas válidas y a tiempo | 10 |
| Jurado (30) | Método sólido y validado offline antes de ir en vivo | 10 |
| | Aprender del stream: feedback, exploración, tendencias, precios, cold start | 10 |
| | Uso de RawTree: features en SQL, embeddings, cómo lee y escribe el pipeline | 5 |
| | Comunicación: que un product manager lo entienda | 5 |

- `score = 100 × (nosotros − baseline) / (oráculo − baseline)`, recortado a [0, 100].
- Baseline (0): **hay una contradicción oficial**. El README y el código de `starter/recommender.py` dicen "los más vendidos
  de siempre en stock". Las diapositivas de la apertura dicen "los más vendidos de la categoría del último producto visto".
  El código del starter hace lo primero. Pendiente de confirmar en el stand de RawTree. El fallback es el del README.
- Oráculo (100) = lo que elegiría el modelo oculto.
- Hay una "referencia" de los organizadores en el leaderboard. Se puede superar.
- Petición fría: `is_new_user = true`, o `recent_views` incluye un producto añadido durante el evento (`new_product`).
- Se puntúa la PROBABILIDAD de compra, no la compra real. Pero las compras reales cuentan un poco: quien ya compró un producto suele no quererlo otra vez.
- Desempate: ingreso esperado por petición en la final.

## Lo que cambia en la final (diapositiva "The store moves")
Compradores nuevos (muchos sin historial), productos nuevos (solo descripción), tendencias (se ven pronto en
`shop.events`), cambios de precio (en `catalog_updates`) y roturas de stock (un producto inactivo invalida la respuesta).

## Reto paralelo: "Best agentic use of RawTree"
Premio aparte, abierto a todos los equipos: un agente de IA que use RawTree para guardar, analizar y actuar sobre datos,
donde el agente decide qué ingerir, qué preguntar y qué hacer con las respuestas. Se entrega un repo con README y una
demo corta. "Un proyecto pequeño con una gran historia gana". Información en el stand de RawTree. NO es prioridad
hasta que el recomendador esté estable.

## Normas
- Se permiten agentes de IA. Lo que no sepamos explicar puede penalizar.
- Claves y feedback privados. Las recomendaciones salen de nuestro código.
- El sistema corre desatendido en una máquina que no se duerma.
- Los mejores equipos comparten el repo con el jurado: mantenerlo legible.
- Pista oficial: "los equipos que generalizan ganan a los que memorizan".
