# Cómo ganar el datathon de recomendación en tiempo real: plan priorizado con evidencia

**Respuesta corta: no vayáis directos al «modelo grande».** Construid primero, en unas 6 horas, una política que tenga en cuenta la sesión y sea robusta. Tiene tres partes: candidatos por co‑visitas y por la misma subcategoría, una puntuación explícita P(compra | producto, contexto) que tenga en cuenta el precio, y una lista ordenada de mayor a menor con exclusión de lo ya comprado. Validadla con un estimador off‑policy por posición, calibrado contra los puntos reales del leaderboard. Solo después añadid un ranker LightGBM con features por atributos, nunca por ID, y un aprendizaje online de pocos parámetros. Los números del leaderboard sugieren que el modelo oculto premia mucho la afinidad fina con la sesión (subcategoría, marca, rango de precio) y castiga el precio bastante menos de lo que parece. Esto último es una inferencia, no un dato confirmado.

## TL;DR

- **Prioridad 1 (≈6 h, ganancia mayor):** candidatos de la sesión y co‑visitas ponderadas por tipo de evento y recencia (patrón ganador de OTTO), puntuados con afinidad de subcategoría, marca y precio. Excluid lo comprado y ordenad por P(compra) estimada, porque la atención cae con la posición. En sesiones cortas los métodos simples por vecinos compiten con las redes neuronales (Ludewig, Mauro, Latifi y Jannach, 2019/2020).
- **Prioridad 2 (≈4 h, imprescindible para el jurado y para no fallar a ciegas):** un evaluador offline. Combinad un modelo de posición (PBM) estimado del log con IPS/SNIPS por ítem‑posición, intervalos bootstrap y una prueba de calibración: ¿ordena igual que el leaderboard a «baseline 0,66 %» y «más vendidos de la categoría 0,79 %»? Las fórmulas de IPS sobre la lista entera tienen varianza enorme; Li et al. (KDD 2018) demuestran que los estimadores con modelo de clic son más eficientes si el modelo se cumple.
- **Prioridad 3 (≈6–8 h, después):** un ranker LightGBM entrenado sobre el log de eventos con features que generalizan, más un aprendizaje online de pocos parámetros (tendencias EWMA, peso del precio, stock) con Thompson sampling descontado y una exploración pequeña en los slots bajos. En la final habrá solo unas 21.600 peticiones (≈450 compras si convertís al 2 %), demasiado pocas para aprender producto a producto: aprended parámetros compartidos, no 5.000 brazos.

## Key Findings

### 1. Qué nos dicen los números del leaderboard (análisis propio)

| Política | Conversión | €/petición | € por compra (= €/petición ÷ conversión) |
|---|---|---|---|
| Baseline (más vendidos) | 0,66 % | 0,147 | ≈22 € |
| Más vendidos de la categoría de lo último visto | 0,79 % | — | — |
| v2‑HYDRA‑explora | 0,85 % | 0,32 | ≈38 € |
| Referencia organizadores | 2,09 % | 0,93 | ≈45 € |

Interpretación. Es **inferencia**, no evidencia directa del simulador:

- **El precio no es un freno fuerte para el modelo oculto.** La referencia triplica la conversión mostrando productos de un ticket medio de unos 45 €, el doble que el baseline. Si el simulador castigara mucho el precio, no se podría triplicar la conversión con productos el doble de caros. Lo más probable es que exista una sensibilidad al precio moderada y quizá heterogénea por device o region, o que el precio interactúe con la afinidad: «caro pero de lo que estoy mirando» convierte.
- **La categoría sola explica poco** (+20 %), mientras que la referencia consigue +217 %. La afinidad que premia el modelo oculto es más fina: probablemente subcategoría (90), marca y rango de precio coherentes con `recent_views`. Además, la referencia podría usar los propios productos vistos (re‑visita), la señal más fuerte en OTTO.
- **El bandit con exploración (HYDRA) sube el ingreso más que la conversión.** Encaja con un simulador donde aprender «qué se vende» online ayuda algo, pero sin modelo de sesión no se llega lejos.
- **Gradient boosting directo falló** por dos causas típicas: aprender el sesgo de popularidad del log (que el carrusel antiguo creó) y la latencia (61 % de respuestas a tiempo). Es la ilustración exacta de «generalizar, no memorizar».

**Prueba rápida para confirmar las hipótesis** (1 hora en práctica): servid variantes A/B por hash de `user_id`: (a) misma subcategoría que la última vista, ordenada por popularidad; (b) lo mismo, pero ordenado por precio descendente dentro del top‑30; (c) re‑visita: los productos vistos en la sesión y no comprados primero. Con unas 10.800 peticiones/hora, cada brazo recibe unas 3.600. A una conversión del 1–2 % (36–72 compras por brazo), el error estándar de la diferencia es de unos 0,3 puntos: detectaréis efectos de ×1,5 o más, no matices. Para matices, usad los **clics** como señal intermedia, que son bastante más frecuentes.

### 2. Cómo modelan los simuladores el clic y la compra (pregunta A1)

- **Position‑based model (PBM).** P(clic | usuario u, ítem i, rango r) = A(u,i) × E(r): el atractivo del ítem multiplicado por la probabilidad de examinar la posición. SARDINE (Deffayet et al., 2023) lo usa literalmente como su modelo de clic. En sus experimentos, un modelo PBM aprendido del log «identifica correctamente los sesgos» y casi alcanza al oráculo, mientras que un modelo ingenuo de CTR por documento apenas mejora a la política de log.\[1\] **Implicación:** con PBM, la lista óptima es simplemente ordenar por A(u,i) de mayor a menor, porque el producto de dos secuencias se maximiza emparejando mayor con mayor (desigualdad de reordenamiento). La diversidad no ayuda *por sí misma*.
- **Cascada** (Kveton et al., ICML 2015, «Cascading Bandits»). El usuario recorre la lista de arriba abajo y se detiene en el primer ítem atractivo.\[2\] **Implicación:** el valor de la lista es 1 − ∏(1 − A_i). Poner diez casi‑clones del mismo producto desperdicia slots si el usuario tiene una intención distinta, así que la diversidad ayuda cuando hay incertidumbre sobre la intención.
- **Logit multinomial (MNL) y logit condicional.** Es lo que trae RecSim (Ie et al., 2019): el usuario elige como mucho un ítem de la lista con probabilidad proporcional a exp(utilidad), frente a una opción «no elegir nada». RecSim también incluye un «exponential cascade model» para capturar el sesgo de posición.\[3\]\[4\] **Implicación:** P(compra de la lista) = Σv_i / (v_0 + Σv_j), que crece con cada v_i, así que maximizar la conversión equivale otra vez a escoger los de mayor utilidad. Cuando se optimiza el ingreso con MNL, a veces conviene *quitar* productos baratos (problema de surtido), lo que encaja con el desempate por ingreso.
- **RecoGym** (Rohde et al., 2018) separa la conducta «orgánica» (lo que el usuario ve por su cuenta) de la respuesta a la recomendación («bandit»), con embeddings distintos. Cuando ambas divergen, los métodos puramente orgánicos fallan (BLOB, 2020).\[5\]\[6\] **Implicación para vosotros:** las co‑visitas del log de eventos (orgánicas) no tienen por qué coincidir con lo que compra el simulador desde el carrusel. Hay que validarlas contra el log del carrusel y el feedback en vivo.

**Conclusión práctica:** en los tres modelos lo prioritario es poner primero los ítems de mayor P(compra). Las diferencias aparecen en la cola de la lista (slots 6–10): ahí conviene diversificar (cascada/MNL) y explorar, porque cuesta poco (E(r) bajo).

### 3. Inferir el modelo oculto desde el log (A2)

- **Curva de posición E(r).** Como el carrusel tenía «algo de azar», el mismo producto apareció en posiciones distintas. Comparad su tasa de clic/compra entre posiciones ponderando por 1/propensity («intervention harvesting», Agarwal et al., WSDM 2019, «Estimating Position Bias without Intrusive Interventions»). Alternativa: regression EM (Wang et al., WSDM 2018), que estima a la vez E(r) y la relevancia sin aleatorizar y que en su evaluación sobre búsqueda personal superó a las estimaciones con aleatorización por pares en casi todas las posiciones.\[7\]\[8\] Para 10 posiciones y 2M impresiones, un EM sencillo en numpy (PBM: clic = examen × atractivo) converge en minutos.
- **Sensibilidad al precio por device/region.** Ajustad una regresión logística de compra por impresión con features [posición (one‑hot), log(precio), log(precio) × device, log(precio) × region, misma subcategoría que lo último visto, misma marca, |log precio − log precio medio de la sesión|], ponderada por 1/propensity. Los coeficientes son interpretables y explicables al jurado.
- **Afinidad por categoría según lo visto.** Calculad la tasa de compra condicionada a «el producto mostrado comparte subcategoría/marca/rango de precio con k de las vistas recientes».
- **En vivo:** repetid la regresión cada 30 minutos sobre vuestro propio feedback. Vuestros datos no tienen propensities si la política es determinista; por eso conviene que vuestra política tenga una aleatorización pequeña y *registrada* (ver F).

### 4. Generalizar frente a memorizar (A3)

**Evidencia sólida:** DropoutNet (Volkovs, Yu y Poutanen, NeurIPS 2017)\[9\] muestra que un modelo entrenado «apagando» aleatoriamente las entradas de preferencia (las basadas en ID) aprende a apoyarse en el contenido. Sin bajar la precisión con usuarios y productos conocidos («warm start»), mejora en «cold start» (arranque en frío: usuarios o productos sin historial).\[10\] El principio para vosotros: **todo lo que entre al ranker debe poder calcularse para un producto nuevo**. Valen subcategoría, marca, cubo de precio, similitud de embedding con las vistas y antigüedad. No vale el ID del producto como feature categórica.

**Intuición razonada:** en la final habrá productos y usuarios nuevos y tendencias distintas. Las transiciones a nivel de atributo (subcategoría→subcategoría, marca→marca, cubo de precio→cubo de precio) se estiman con muchos más datos por celda que las de producto→producto (90×90 frente a 5.000×5.000) y se aplican tal cual a productos nuevos. Usad ambas: co‑visitas por ID cuando hay soporte y fallback a transiciones de atributos.

### 5. Candidatos basados en sesión (B4–B5)

- **Evidencia: los métodos simples compiten.** Ludewig, Mauro, Latifi y Jannach (UMUAI, 2020/arXiv:1910.12781) compararon doce algoritmos, seis de ellos neuronales, en ocho datasets, y concluyen que «in most cases… simple heuristic methods based on nearest-neighbors schemes are preferable». En los datasets de e‑commerce RETAIL, DIGI y ZALANDO ganan los métodos por vecinos (SKNN, V‑SKNN, STAN); solo en RSC15 un modelo neuronal (NARM) supera ligeramente a VSTAN (MAP@20 de 0,0357 frente a 0,0350), y SR‑GNN tarda unas 10.000 veces más en entrenar que VSTAN. STAN (Garg et al., SIGIR 2019) añade tres pesos a SKNN: la posición del ítem en la sesión actual, la recencia de las sesiones vecinas y la posición en la sesión vecina. La librería `session-rec` (GitHub rn5l/session-rec) tiene implementaciones en Python de sknn, vsknn, stan y vstan.
- **Evidencia: OTTO (Kaggle, 2022–23).** El cuaderno público de Chris Deotte («Candidate ReRank Model», LB 0,575) usa tres matrices de co‑visita: (1) «Carts‑Orders» ponderada por tipo con pesos clic:carrito:pedido = 1:6:3 y ventana de 24 h; (2) «Buy2Buy» (carrito/pedido → carrito/pedido, ventana de 14 días, peso 1); (3) «Clicks» ponderada por tiempo con peso lineal de 1 a 4 según la fecha y ventana de 24 h.\[11\]\[12\] Trunca cada sesión a sus 30 eventos más recientes.\[11\] Para reordenar, pondera cada evento de la sesión con `np.logspace(0.1, 1, n, base=2) - 1` (más peso a lo reciente) multiplicado por el peso del tipo.\[11\] Los ganadores convirtieron esto en dos etapas: el 1.º (mrkmakr) generó 1.200 candidatos por sesión (re‑visitas, co‑visitas ponderadas por tipo y tiempo y recuperación neuronal) y los reordenó con LightGBM Ranker; según el resumen del blog de lukan217 (febrero de 2023), que cita la discusión n.º 384022 de Kaggle, obtuvo 0,604 con un solo modelo y 0,605 con un ensemble de 9 LightGBM con hiperparámetros distintos. El 3.º, el equipo de Chris Deotte, Giba y Theo, obtuvo 0,60437 en el público y 0,60382 en el privado (repositorio de Benedikt Schifferer, bschifferer/Kaggle-Otto-Comp). Una solución de plata usó ventanas de 12 h (clic→clic) y 24 h (clic→carrito/compra, carrito→carrito) y añadió **todos los ítems ya vistos en la sesión** como candidatos.\[13\]\[14\] Importante: en OTTO la métrica pondera 0,1 clics, 0,3 carritos y 0,6 pedidos, así que las matrices orientadas a compra eran las decisivas, igual que aquí.\[15\]\[16\]
- **Recomendación para combinar señales (B5):** puntuación de candidato c dada la sesión s =
  Σ_{v ∈ vistas de s} w_rec(v) · [α·CV_vista→vista(v,c) + β·CV_vista→compra(v,c) + γ·CV_carrito→compra(v,c)],
  con w_rec(v) = 2^(−(n−pos(v))/h) (decaimiento exponencial; empezad con h ≈ 3 vistas) o el logspace de Deotte. Normalizad cada matriz por fila (dividiendo por la popularidad de c elevada a ~0,5) para no recomendar siempre lo popular. Empezad con α:β:γ = 1:6:3 por analogía con OTTO y ajustadlo con el evaluador offline. **Que vista→compra pese más que vista→vista es evidencia de OTTO; los valores exactos aquí son intuición que hay que validar.**

### 6. Reordenar con un modelo (C6–C8)

- **Arquitectura (evidencia de OTTO y RecSys 2015):** el ganador de RecSys Challenge 2015 (YOOCHOOSE; Romov y Sokolov, Yandex) usó clasificación en dos etapas, uso masivo de features categóricas, gradient boosting y umbrales optimizados directamente para la métrica.\[17\] En OTTO casi todos los oros usaron candidatos + GBDT ranker (LightGBM `lambdarank`, CatBoost `YetiRank`/`QueryCrossEntropy`).\[13\]\[18\]\[19\] Una solución de bronce describe que el ranker se apoya sobre todo en una puntuación de recencia con decaimiento exponencial de los ítems de la sesión.\[20\]
- **Features que más importan, según los writeups de OTTO:** (1) ¿está el candidato en la sesión? y su recencia/tipo; (2) puntuaciones de co‑visita agregadas y su rango; (3) popularidad del candidato en varias ventanas temporales; (4) similitud de embedding con la última vista y con la media de la sesión.\[13\]\[19\]\[21\] Aquí hay que añadir (5) precio relativo al de la sesión, (6) coincidencia de subcategoría/marca/cubo de precio, (7) device/region/page y (8) producto nuevo (sí/no).
- **Versión mínima viable (≈5–6 h):** entrenad sobre el **log de eventos** (12M), no sobre el carrusel. Cortad cada sesión en el instante t: el prefijo da las features y la etiqueta es «¿compra c en el resto de la sesión?». Tomad 50 candidatos por sesión, LightGBM binario (más fácil de calibrar que lambdarank) con 20–30 features y 1–2M filas. Inferencia: 50 candidatos × 30 features es trivial (<10 ms).
- **Log sesgado (C7):** Joachims, Swaminathan y Schnabel (WSDM 2017) formalizan el ERM ponderado por propensity: cada clic observado se pondera por 1/P(examen en su posición). Así se obtiene un estimador insesgado del riesgo aunque los datos estén sesgados, y su Propensity SVM‑Rank superó tanto al ranker de producción como al SVM‑Rank ingenuo en un experimento real.\[22\]\[23\] **Para vosotros:** si entrenáis con el log del carrusel, ponderad las filas positivas por 1/E(r), con E(r) estimado en el punto 3, y recortad pesos (clipping a ≤10). Si entrenáis con el log de eventos orgánico, el sesgo es otro (lo que la tienda enseñaba): mitigadlo normalizando la popularidad.
- **Objetivo (C8):** la nota es Σ_k E(k)·P(compra_k) y el desempate es el ingreso. Ordenad por **p̂(compra | ítem, contexto)** y aplicad el desempate solo cuando las probabilidades estén cerca: score = p̂ · (1 + λ·log(precio)) con λ pequeño (0,05–0,1), validado offline. Predecid P(compra) directamente si hay datos suficientes. Si no, descomponed P(clic) × P(compra | clic): los clics son mucho más frecuentes y dan menos ruido, sobre todo online. No optimicéis el ingreso esperado puro: subiría el ticket a costa de la conversión, que es lo que puntúa.

### 7. Evaluación offline fiable (D9–D10)

- **Por qué el ejemplo oficial da pocas coincidencias:** el IPS de la lista completa o del slot 1 exige que vuestra lista coincida con la mostrada. Con 5.000 productos, eso casi nunca ocurre y la varianza explota. Swaminathan et al. (NeurIPS 2017) proponen el **estimador pseudoinverse (PI)** para listas, insesgado si la recompensa es aditiva por slot, y muestran que su versión ponderada domina al wIPS en todas sus condiciones experimentales.\[24\]\[25\] Vlassis et al. (2021) lo mejoran con variables de control.\[26\] Li et al. (KDD 2018, arXiv:1804.10488) construyen estimadores basados en **modelos de clic** (por ítem, por ítem‑posición, PBM, cascada) que son estadísticamente más eficientes, siempre que el modelo de clic se cumpla.\[27\]
- **Recomendación concreta con ~100.000 impresiones:**
  1. **Estimador principal, PBM‑IPS por ítem‑posición:** V̂(π) = (1/N) Σ_impresiones Σ_k E(k)·θ̂(π_k(x), x). Aquí θ̂(i,x) es el atractivo de compra estimado del ítem i en el contexto x, obtenido del log con pesos 1/propensity y suavizado bayesiano hacia la media de su subcategoría (prior Beta). Es un «direct method» guiado por el modelo de clic: sesgo pequeño si el PBM es razonable y varianza baja.
  2. **Control, IPS/SNIPS por slot con recorte:** para cada slot k, pondera las compras cuando π coincide con el ítem mostrado en k, con peso 1/propensity(k) recortado. SNIPS (normalizado) reduce la varianza a cambio de un sesgo pequeño. Está implementado en Open Bandit Pipeline (`obp`, ZOZO), que incluye IPW, SNIPW, DR, DR con shrinkage, IIPS para listas y Cascade‑DR.\[28\]\[29\]
  3. **Doubly robust** = (1) + corrección IPS de los residuos. Merece la pena si os sobra tiempo.
  4. **Intervalos de confianza** con bootstrap por *sesión* (no por impresión): 200 remuestreos.
- **Comprobar que la métrica offline predice la online (D10):** tenéis puntos de calibración gratuitos. Evaluad offline (a) más vendidos de siempre, (b) más vendidos de la categoría de lo último visto, y cada versión vuestra que hayáis desplegado en práctica. Si el estimador no ordena a (b) por encima de (a) y ~20 % arriba, no es fiable. Usad un **corte temporal** (entrenar días 1–50, evaluar días 51–60) y un **segmento frío** (sesiones de usuarios sin historial y productos creados en los últimos días) para simular la final. Reportad las dos cifras al jurado.

### 8. Cold start (E11–E12)

- **Productos nuevos:** los embeddings de qwen3 son baratos. Con 60 peticiones/min × 32 textos, embeber 5.000 descripciones lleva unos 2,6 minutos. Guardad el vector normalizado (o reducido a 256 dimensiones con PCA). Para un producto nuevo: (1) sus 20 vecinos por coseno *dentro de su subcategoría*; (2) **heredad sus co‑visitas** como media ponderada por similitud de las filas y columnas de co‑visita de esos vecinos; (3) prior de conversión = media de sus vecinos, encogida hacia la de su subcategoría. Así es como DropoutNet o CB2CF «colocan» un ítem frío en el espacio colaborativo, sin red neuronal. Añadid exploración optimista a los nuevos (ver F), porque su incertidumbre es mayor.
- **Usuarios nuevos sin vistas:** popularidad *reciente* (EWMA de compras, no histórica) por (device, region, page), con suavizado jerárquico hacia la global. Cuando haya 1–2 vistas, la puntuación de sesión del punto 5 domina rápido.
- **Regla de seguridad:** precalculad listas fallback por contexto (device×region×page = pocas combinaciones) y refrescadlas cada minuto.

### 9. Aprender del stream (F13–F15)

- **Volumen real de señal:** 3 peticiones/s × 7.200 s = 21.600 peticiones en la final. Al 2 % son unas 430 compras, repartidas entre ~5.000 productos: menos de 0,1 compras por producto. **Un bandit producto a producto no puede aprender en la final.** Lo que sí se aprende: parámetros compartidos (multiplicadores de tendencia por subcategoría, peso del precio, efecto de rotura de stock) y, sobre todo, **clics**. Durante la práctica (≈237.600 peticiones en 22 h) sí podéis afinar.
- **Qué usar:**
  - **Tendencias:** EWMA de vistas/carritos/compras por producto y por subcategoría a partir de *todo* el stream de vuestro feedback y del log, con semivida de 15–30 minutos. Lift = EWMA_corto / (EWMA_largo + prior). Multiplicad la puntuación por lift^η con η≈0,3–0,5 y recortad.
  - **Thompson sampling descontado** (Raj y Kalyani, 2017) sobre pocos «brazos»: variantes de λ (precio), η (tendencia) o mezclas de candidatos. El descuento γ olvida lo antiguo y mantiene viva la exploración ante cambios.\[30\]\[31\] Es explicable en una diapositiva (Beta(α,β) que se encoge con γ≈0,999 por petición).
  - **Cascading bandits** (Kveton et al., 2015) es la base teórica para aprender el orden de listas con clics. Sus algoritmos funcionan sorprendentemente bien incluso cuando el modelo de usuario no es exactamente cascada,\[32\] pero aprender 5.000 atractivos con 430 compras sigue sin ser viable. Usadlo como argumento ante el jurado, no como implementación principal.
- **Exploración:** 1–2 slots de los últimos (8–10) con candidatos «inciertos» (productos nuevos, tendencias emergentes), elegidos por muestreo de Thompson de su posterior. Bajo PBM, el coste es E(9)·Δθ, pequeño. Registrad la probabilidad con que se eligió cada ítem explorado: así podréis hacer IPS con vuestro propio log y explicarlo al jurado.
- **Mérito entre posiciones (F14):** actualizad θ del ítem con «exámenes efectivos». Una impresión en la posición k cuenta como E(k) observaciones y una compra como 1. Es el estimador insesgado bajo PBM. Usad clics como señal densa con un prior de P(compra | clic) por subcategoría.
- **Precio y stock (F15):** filtrad siempre en el momento de servir con la tabla de stock más reciente. Al cambiar un precio, recalculad las features de precio relativo, no el modelo. Si un producto se agota, sus co‑visitas pasan a sus vecinos de embedding de la misma subcategoría.

### 10. Ingeniería en tiempo real (G16–G17)

- **Patrón:** el camino caliente (la respuesta a cada petición) **no consulta la base de datos de forma síncrona**. Un hilo en segundo plano refresca cada 30–60 s, desde RawTree/ClickHouse, diccionarios en memoria: stock/activos, precios, EWMA de tendencias, top‑N de co‑visitas por producto y fallbacks por contexto. Cada petición hace búsquedas en diccionarios, puntúa ≤200 candidatos, deduplica con un `set`, filtra activos y en stock y rellena hasta 10 con el fallback. Con un timeout interno de 1 s, devolved el fallback precalculado, que siempre es válido. Medid p99 de latencia y el porcentaje a tiempo (objetivo: 100 %; el equipo de GBM con 61 % muestra el coste de no hacerlo).
- **SQL que suma con el jurado (agregados incrementales en ClickHouse):** tablas `AggregatingMergeTree` alimentadas por vistas materializadas con `countState`/`sumState` por (producto, ventana de 5 min). Las vistas materializadas de ClickHouse son disparadores de INSERT que mantienen agregados en tiempo real sin trabajos programados.\[33\] `argMax(price, ts)` da el precio vigente y `argMax(in_stock, ts)` el stock vigente.\[34\]\[35\] Las co‑visitas se construyen con `groupArray` + `arrayJoin` por sesión, en una consulta. Para el top‑N por fila, `ORDER BY ... LIMIT N BY item`. Para ventanas, `windowFunnel` o filtros `ts > now() - INTERVAL 30 MINUTE`. Comprobad qué funciones soporta RawTree, porque es una derivada y no ClickHouse literal.

### 11. Antecedentes (H18)

- **CLEF NewsREEL (2014–2017):** recomendación de noticias en vivo con tiempo límite. El informe de 2014 (Kille et al., «Overview of CLEF NEWSREEL 2014») constata que «most systems carried a fall-back solution in terms of most-popular and/or most-recent strategies» y que «the baseline is hard to beat», aunque lo atribuye en parte a las restricciones técnicas (13 participantes activos y un límite de respuesta de 100 ms). Las respuestas tardías o inválidas contaban como peticiones sin clic, igual que aquí.\[36\] En 2015, los mejores CTR por periodo fueron del 0,79 % (irit‑imt, 44 clics en 5.597 peticiones) y del 0,86 % (abc, 56 clics en 6.483 peticiones), según el Overview de NewsREEL 2015.
- **RecSys 2015 (YOOCHOOSE):** ganó gradient boosting en dos etapas con features categóricas masivas (Romov y Sokolov). Según Ben‑Shimon et al. («RecSys Challenge 2015 and the YOOCHOOSE Dataset», RecSys '15), compitieron 850 equipos de 49 países con 5.437 soluciones, y el ganador obtuvo 63.102 puntos, «approximately 50% of the maximum score».
- **OTTO 2023:** co‑visitas, candidatos y GBDT ranker. El 1.º usó 1.200 candidatos y una recuperación neuronal, pero la columna vertebral era la misma que la de los equipos de plata.\[37\]
- **Diginetica (CIKM Cup 2016) y Retail Rocket:** son benchmarks habituales en los estudios de Ludewig/Jannach donde SKNN/STAN compiten con GRU4Rec/SR‑GNN. No he verificado los detalles de los ganadores de CIKM Cup para este informe.

### 12. Errores típicos (H19)

1. **Fuga temporal:** construir co‑visitas o popularidad con datos posteriores al punto de corte de validación.
2. **Memorizar el log:** IDs de producto como feature y popularidad histórica como feature dominante. Lo que se aprende así es la política antigua, no al usuario.
3. **Sobreajustar a la práctica:** en la final cambian los productos, los precios y las tendencias. Todo parámetro ajustado en vivo debe tener un prior y un descuento temporal.
4. **Recomendar lo ya comprado:** filtradlo siempre (la pista lo dice).
5. **Latencia y validez:** duplicados, productos inactivos o sin stock, menos de 10 ítems. Validad cada respuesta antes de enviarla.
6. **Evaluar con IPS de lista completa** y concluir «no podemos medir»: usad estimadores por posición.

## Tabla priorizada de técnicas (ganancia esperada ÷ esfuerzo)

| # | Técnica | Ganancia esperada aquí | Horas | Riesgo | Base |
|---|---|---|---|---|---|
| 1 | Servicio robusto: caché en memoria, fallback por contexto, validación, timeout | Evita perder hasta el 100 % de una petición | 2–3 | Bajo | NewsREEL; el equipo con 61 % a tiempo |
| 2 | Excluir comprados + filtros de stock/activo | Pequeña pero segura | 0,5 | Muy bajo | Pista oficial |
| 3 | Candidatos de sesión: re‑visitas + misma subcategoría/marca + co‑visitas ponderadas por tipo y recencia | **Muy alta** (pasar de +20 % a ×2) | 4–5 | Bajo | OTTO, Ludewig/Jannach, STAN |
| 4 | Curva de posición E(k) del log (harvesting/EM) | Alta (habilita 5, 6 y 9) | 2 | Bajo | Wang 2018; Agarwal 2019 |
| 5 | Evaluador offline PBM‑IPS + SNIPS + bootstrap + calibración con el leaderboard | Alta (decide todo lo demás; 30 % del jurado) | 3–4 | Medio | Li 2018; Swaminathan 2017; obp |
| 6 | Puntuación explícita: afinidad × precio (regresión logística ponderada) | Alta | 2–3 | Medio | Joachims 2017 (IPS); inferencia propia |
| 7 | Cold start de productos con embeddings y herencia de co‑visitas | Media‑alta en la final | 2–3 | Bajo | DropoutNet (principio) |
| 8 | Popularidad reciente por contexto (EWMA) para usuarios nuevos | Media | 1–2 | Bajo | NewsREEL |
| 9 | Tendencias EWMA + lift | Media | 1–2 | Bajo‑medio | Intuición + NewsREEL |
| 10 | Ranker LightGBM con features por atributos | Media‑alta si 3–6 funcionan | 5–6 | **Medio‑alto** (los equipos de GBM fallaron) | OTTO, RecSys 2015 |
| 11 | Thompson descontado sobre pocos hiperparámetros + exploración en slots 8–10 | Media; alta para el jurado | 2–3 | Medio | Raj y Kalyani 2017; Kveton 2015 |
| 12 | Desempate por ingreso (λ·log precio) | Pequeña (solo desempate) | 0,5 | Bajo | Métrica |
| 13 | Doubly robust / PI completo | Pequeña sobre 5 | 2–3 | Medio | Swaminathan 2017; obp |
| 14 | Modelos neuronales (GRU4Rec, SASRec, SR‑GNN) | Baja con este presupuesto | 8+ | Alto | Ludewig et al. 2020 |
| 15 | Bandit por producto (LinUCB/cascading completo) | Baja en la final (≈430 compras) | 4+ | Alto | Cálculo propio |

## Fichas de las técnicas recomendadas

**T3 – Candidatos de sesión con co‑visitas.** *Qué es:* contad cuántas veces dos productos aparecen juntos en la misma sesión, en un intervalo corto y dando más peso si el segundo evento fue una compra. Para recomendar, sumad las filas de los productos vistos, con más peso a los recientes. Es «la gente que miró esto compró aquello». *Por qué encaja:* catálogo pequeño, sesiones cortas y 12M eventos: tabla densa y fiable. *Referencias:* cuaderno de Deotte en Kaggle OTTO (pesos 1:6:3, ventanas de 24 h y 14 días); Ludewig et al. arXiv:1910.12781; STAN (Garg et al., SIGIR 2019). *Python:* `polars`/`pandas` o SQL en ClickHouse; `session-rec` si queréis probar V‑SKNN/STAN. *Validación:* Recall@10 de compras sobre el corte temporal y el estimador del punto 7. *Errores:* no normalizar por popularidad; ventanas demasiado largas; contar pares duplicados de la misma sesión.

**T4 – Curva de posición.** *Qué es:* la probabilidad de que el usuario mire el slot k. Se estima comparando el mismo producto en distintos slots, corrigiendo con la propensity. *Por qué encaja:* tenéis propensities y azar en el log, el caso ideal. *Referencias:* Wang et al. WSDM 2018 (regression EM); Agarwal et al. WSDM 2019. *Python:* numpy (EM de 30 líneas) o `statsmodels` GLM. *Validación:* la curva debe ser monótona y estable entre mitades del log. *Errores:* confundir la caída por posición con la caída por calidad, que es justo lo que corrige la propensity.

**T5 – Evaluador offline.** *Qué es:* una función que, dada una política, estima sus compras por cada 100 peticiones con los datos antiguos. *Por qué encaja:* es la única forma de decidir sin gastar la práctica, y el jurado lo pide. *Referencias:* Li et al. KDD 2018 (arXiv:1804.10488); Swaminathan et al. NeurIPS 2017; Open Bandit Pipeline (arXiv:2008.07146). *Python:* numpy + `obp`. *Validación:* calibrad con el baseline (0,66 %) y la categoría (0,79 %). *Errores:* propensities sin recorte; bootstrap por impresión en lugar de por sesión.

**T6/T10 – Puntuación y ranker.** *Qué es:* un modelo que da a cada candidato la probabilidad de compra a partir de features interpretables. *Por qué encaja:* combina afinidad, precio y contexto, la estructura más probable del simulador. *Referencias:* Joachims et al. WSDM 2017; Romov y Sokolov, RecSys Challenge 2015; writeups de OTTO. *Python:* `scikit-learn` LogisticRegression primero y `lightgbm` después. *Validación:* AUC en el corte temporal + el estimador T5 + el segmento frío. *Errores:* features por ID; entrenar con popularidad futura; olvidar la latencia (cargad el modelo una vez y predecid en lote).

**T7 – Cold start con embeddings.** Ver punto 8. *Errores:* superar el límite compartido de 60 rpm (encolad y cachead); comparar embeddings sin normalizar.

**T11 – Thompson descontado.** *Qué es:* cada variante tiene una distribución Beta de su tasa de éxito; en cada petición se muestrea y se usa la variante ganadora; lo antiguo se olvida poco a poco. *Por qué encaja:* aprende online con pocos brazos y se adapta a cambios. *Referencias:* Raj y Kalyani, arXiv:1707.09727; Kveton et al. ICML 2015. *Validación:* simulad offline con el log que la regla converge. *Errores:* demasiados brazos para la señal disponible; recompensar con compras cuando los clics darían 10× más señal.

## Plan por horas (4 personas, ~20 h)

| Horas | P1 – Infra/servicio | P2 – Candidatos | P3 – Evaluación offline | P4 – Online/jurado |
|---|---|---|---|---|
| 0–3 | Servicio con caché, fallback por contexto, validador y métricas de latencia; desplegar baseline+exclusión de comprados | Co‑visitas en SQL (vista→vista, vista→compra, carrito→compra) con ventanas y pesos | Cargar el log del carrusel; estimar E(k) | Tablas incrementales en ClickHouse (EWMA, stock, precio vigente); plantilla de la presentación |
| 3–6 | Integrar la política de P2 (v1) y desplegarla | Política v1: re‑visitas + co‑visitas + subcategoría; normalización | Evaluador PBM‑IPS + SNIPS + bootstrap; calibración con baseline/categoría | Prueba A/B de hipótesis del modelo oculto (subcategoría, precio, re‑visita) |
| 6–10 | Embeddings de descripciones (cola con límite de rpm); vecinos y herencia de co‑visitas | Regresión logística ponderada (afinidad × precio × contexto) | Evaluar v1, v2 y variantes; corte temporal y segmento frío | Tendencias EWMA y lift; Thompson descontado sobre λ y η |
| 10–16 | Endurecer el servicio: p99, pruebas de carga a 10 req/s, simulacros de caída de la base de datos | LightGBM con features por atributos; comparar con la regresión logística | Decidir con datos si el ranker mejora (IC sin solapamiento) | Exploración en slots 8–10 con propensities registradas; panel de feedback |
| 16–19 | Congelar el código; modo seguro por defecto | Ajuste fino de pesos con el evaluador | Informe de validación para el jurado | Ensayar la explicación; checklist de la final |
| 19–20 | Despliegue final y monitorización | Reserva | Reserva | Reserva |

**Regla de decisión:** cada cambio pasa por el evaluador offline y luego por 30–60 minutos en vivo. Si el intervalo de confianza online no mejora, se revierte. El ranker grande **reutiliza** todo lo anterior: los candidatos (T3), la curva de posición (T4), las features de la regresión (T6), los embeddings (T7) y el evaluador (T5). Por eso empezar simple no es tiempo perdido: es la etapa 1 del modelo grande.

## Caveats

- **Evidencia sólida:** que los métodos por vecinos compiten en sesiones (Ludewig/Jannach); el patrón co‑visitas + GBDT de OTTO y RecSys 2015; IPS para aprender con sesgo de posición (Joachims 2017); regression EM (Wang 2018); los estimadores con modelo de clic y el PI (Li 2018, Swaminathan 2017); los modelos de usuario de RecSim, SARDINE y RecoGym; NewsREEL (lo popular y reciente es difícil de batir).
- **Intuición o inferencia propia:** la lectura de los números del leaderboard (precio poco penalizado, afinidad fina); los pesos concretos (α:β:γ, h, λ, η, semividas); el cálculo de que en la final hay pocas compras para aprender por producto; el plan por horas.
- **Datos de segunda mano:** los detalles del cuaderno de Deotte y la puntuación del 1.º de OTTO proceden de copias en blogs de terceros; el top‑N exacto por matriz no está verificado. No he verificado a los ganadores de Diginetica/CIKM Cup ni de Retail Rocket.
- **RawTree no es ClickHouse literal:** confirmad la sintaxis (`AggregatingMergeTree`, `argMax`, `LIMIT BY`) antes de diseñar en torno a ella.
- **El simulador puede no seguir ninguno de los modelos de la literatura.** Por eso la calibración del evaluador con los puntos reales del leaderboard y las pruebas A/B tempranas son lo más valioso de todo el plan.

## Fuentes

1. [SARDINE: Simulator for Automated Recommendation in Dynamic and Interactive Environments](https://doi.org/10.1145/3656481)
2. [Cascading Bandits: Learning to Rank in the Cascade Model](https://proceedings.mlr.press/v37/kveton15.html)
3. [RecSim: A Configurable Simulation Platform for Recommender Systems](https://arxiv.org/html/1909.04847v2)
4. [SlateQ: Reinforcement Learning in slate recommender problems](https://medium.com/analytics-vidhya/slateq-a-scalable-algorithm-for-slate-recommendation-problems-735a1c24458c)
5. [BLOB: A Probabilistic Model for Recommendation that Combines Organic and Bandit Signals \[Quick Review\]](https://liner.com/review/blob-probabilistic-model-for-recommendation-that-combines-organic-and-bandit)
6. [From Clicks to Conversions: Recommendation for long-term reward](https://arxiv.org/pdf/2009.00497)
7. [Estimating Position Bias without Intrusive Interventions](https://arxiv.org/html/1812.05161)
8. [Position Bias Estimation for Unbiased Learning to Rank in Personal Search · Issue #2 · usaito/CFML-papers · GitHub](https://github.com/usaito/CFML-papers/issues/2)
9. [Multi-modal Adaptive Mixture of Experts for Cold-start Recommendation](https://arxiv.org/pdf/2508.08042)
10. [DropoutNet: Addressing Cold Start in Recommender Systems - rotten-fruits](https://scrapbox.io/rotten-fruits/DropoutNet:_Addressing_Cold_Start_in_Recommender_Systems)
11. [实习-Kaggle-OTTO比赛回顾](https://gladdduck.github.io/2024/03/14/%E5%AE%9E%E4%B9%A0-Kaggle-OTTO%E6%AF%94%E8%B5%9B%E5%9B%9E%E9%A1%BE/index.html)
12. [📔OTTO: easy-to-use co-visitation matrix function](https://www.kaggle.com/code/mizuny/otto-easy-to-use-co-visitation-matrix-function)
13. [OTTO](https://www.kaggle.com/c/otto-recommender-system/discussion/383130)
14. [GitHub - nicolaivicol/otto-recommender: Solution to kaggle competition OTTO](https://github.com/nicolaivicol/otto-recommender)
15. [GitHub - wenlinxiao/otto-recsys: 🛍 A real-world e-commerce dataset for session-based recommender systems research.](https://github.com/wenlinxiao/otto-recsys)
16. [OTTO](https://www.kaggle.com/competitions/otto-recommender-system)
17. [RecSys Challenge 2015: ensemble learning with categorical features](https://publications.hse.ru/en/chapters/175896819)
18. [Building a Recommender System using Machine Learning](https://www.leoniemonigatti.com/blog/recommender-system-ml.html)
19. [ex-27th place solution](https://www.kaggle.com/competitions/otto-recommender-system/writeups/dreamteam-ex-27th-place-solution)
20. [Bronze Medal Solution on Kaggle OTTO — Multi-Objective Recommender System](https://ajisamudra.medium.com/bronze-medal-solution-on-kaggle-otto-multi-objective-recommender-system-d0720aed4712)
21. [6th place solution (single model LB 0.603)](https://www.kaggle.com/c/otto-recommender-system/discussion/384120)
22. [Unbiased Learning-to-Rank with Biased Feedback](https://www.alphaxiv.org/abs/1608.04468)
23. [Google Scholar](https://scholar.google.com/scholar_lookup?title=Unbiased+learning-to-rank+with+biased+feedback&amp=&author=T.+Joachims&amp=&author=A.+Swaminathan&amp=&author=T.+Schnabel&amp=&publication_year=2017&amp=&pages=781-789&amp=&doi=10.1145%2F3018661.3018699)
24. [Off-policy evaluation for slate recommendation Adith Swaminathan](http://papers.neurips.cc/paper/6954-off-policy-evaluation-for-slate-recommendation.pdf)
25. [Reviews: Off-policy evaluation for slate recommendation](https://proceedings.neurips.cc/paper/2017/file/5352696a9ca3397beb79f116f3a33991-Reviews.html)
26. [Control Variates for Slate Off-Policy Evaluation](https://arxiv.org/pdf/2106.07914)
27. [Offline Evaluation of Ranking Policies with Click Models](https://arxiv.org/abs/1804.10488)
28. [GitHub - st-tech/zr-obp: Open Bandit Pipeline: a python library for bandit algorithms and off-policy evaluation · GitHub](https://github.com/st-tech/zr-obp)
29. [Open Bandit Dataset and Pipeline: Towards Realistic](https://arxiv.org/pdf/2008.07146)
30. [Change Detection Based Thompson Sampling Algorithm For Non-Stationary Bandits](https://repository.iiitd.edu.in/xmlui/bitstream/handle/123456789/868/MT18164_Kunwar%20Zaid.pdf?sequence=1&isAllowed=y)
31. [Taming Non-stationary Bandits:A Bayesian Approach](https://arxiv.org/html/1707.09727)
32. [Cascading Bandits: Learning to Rank in the Cascade Model](https://mlanthology.org/icml/2015/kveton2015icml-cascading/)
33. [ClickHouse Materialized Views — INSERT Triggers, AggregatingMergeTree & Refreshable MVs](https://systeminternals.dev/clickhouse/materialized-views/)
34. [ClickHouse Newsletter June 2022: Materialized, but still real-time](https://clickhouse.com/blog/clickhouse-newsletter-june-2022-materialized-but-still-real-time)
35. [How to Use -ArgMin and -ArgMax Combinators in ClickHouse](https://oneuptime.com/blog/post/2026-03-31-clickhouse-combinator-argmin-argmax/view)
36. [NEWSREEL 2017 « CLEF NEWSREEL](https://www.newsreelchallenge.org/previous-campaigns/newsreel-2017/)
37. [Kaggle Otto推荐系统比赛TOP方案总结](https://lukan217.github.io/2023/02/12/Kaggle%20Otto%E6%8E%A8%E8%8D%90%E7%B3%BB%E7%BB%9F%E6%AF%94%E8%B5%9BTOP%E6%96%B9%E6%A1%88%E6%80%BB%E7%BB%93/)
