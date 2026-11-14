# Deep research: prompt listo para pegar

Pegar TODO el bloque de abajo en Claude (claude.ai → Research). Cuando vuelva el informe, guardarlo en
`docs/investigacion.md` y, a partir de él, se rellena la "Fase 2" de cada guía en `docs/guias/`.
Al final hay preguntas que NO son para el deep research sino para el stand de RawTree.

---

```
Necesito una investigación a fondo, con papers, soluciones ganadoras de Kaggle/RecSys y evidencia empírica,
para ganar una competición de recomendación EN TIEMPO REAL. Quiero recomendaciones concretas y priorizadas,
no un resumen general.

## El problema (datos exactos)
- Tienda online simulada: ~5.000 productos, 10 categorías, 90 subcategorías. Cada producto: category,
  subcategory, brand, price_eur, description (texto), created_at, is_active.
- Llegan ~3 peticiones por segundo. Cada petición: user_id, is_new_user, context (device, region, page) y
  recent_views (lista de {product_id, ts} de la sesión). Hay que devolver una lista ORDENADA de 10 productos
  distintos, activos y en stock, en menos de 3 segundos. Si llega tarde o es inválida se muestra un fallback.
- Un simulador con un modelo de usuario OCULTO decide clic y compra. La nota NO es la compra real sino la
  PROBABILIDAD de compra que asigna el simulador a nuestra lista: "compras esperadas por 100 peticiones",
  normalizado como 100 × (nosotros − baseline) / (oráculo − baseline). Baseline = los 10 más vendidos de siempre
  en stock. Oráculo = el propio modelo oculto. Desempate: ingreso esperado por petición.
- Se sabe que: los compradores miran mucho más las primeras posiciones; quien ya compró un producto no suele
  quererlo otra vez; la pista oficial dice "los equipos que generalizan ganan a los que memorizan".
- Solo puntúan 2 horas (~21.600 peticiones) con datos NUEVOS: compradores nuevos (muchos sin historial),
  productos nuevos (solo descripción), tendencias nuevas, cambios de precio y roturas de stock. Antes hay
  ~22 horas de práctica en vivo con datos del mismo tipo. Durante la final no vemos el leaderboard, solo
  nuestro propio feedback (por petición: lo mostrado, clic, compra, ingreso, latencia).
- Datos históricos: 60 días de un carrusel antiguo (~2M impresiones) con lo mostrado (10), la PROPENSITY de
  cada producto en cada posición, clic, compra e ingreso; y ~12M eventos (view / add_to_cart / purchase) con
  usuario, sesión, producto, precio, device y region. El carrusel antiguo era "más vendidos + algo de azar":
  el log está muy sesgado hacia lo popular.
- Base de datos: RawTree (derivada de ClickHouse, SQL sobre JSON). Hay embeddings de texto gratis
  (qwen3-embedding, 4096 dimensiones, con límite de peticiones compartido).
- Nota del jurado (30 % del total): método validado offline antes de ir en vivo, aprender del stream
  (feedback, exploración, tendencias, precios, cold start), buen uso de la base de datos y saber explicarlo.

## El equipo
4 estudiantes, buenos en algoritmos y matemáticas pero SIN experiencia en ML. Programamos con agentes de IA
(Claude Code) en Python. Quedan ~20 horas. Hay que poder explicar todo al jurado.

## Preguntas

### A. Entender la métrica y al simulador
1. ¿Cómo modelan clic y compra sobre una lista de 10 los simuladores de recomendación conocidos (RecSim,
   RecoGym, Virtual-Taobao, KuaiSim, SARDINE u otros)? Position-based model, cascade, logit multinomial…
   ¿Qué implica cada uno para el ORDEN de la lista y para la diversidad?
2. ¿Cómo inferir desde el log (shown + propensity + clic + compra) y desde nuestro feedback en vivo la
   estructura del modelo oculto: curva de sesgo de posición, sensibilidad al precio por device/region,
   afinidad por categoría según lo visto?
3. "Generalizar, no memorizar": ¿qué representaciones transfieren a usuarios y productos nunca vistos
   (transiciones entre categorías/subcategorías/marcas/rangos de precio, atributos) frente a las basadas en
   IDs de producto? ¿Qué dice la literatura de generalización en recomendación?

### B. Generar candidatos (basado en sesión)
4. Estado del arte para recomendación basada en sesiones cortas con catálogo pequeño: matrices de
   co-visitas (soluciones ganadoras de Kaggle OTTO 2023: pesos por tipo de evento, ventanas de tiempo,
   recencia), item-KNN y session-KNN (SKNN, V-SKNN, STAN) frente a modelos neuronales (GRU4Rec, SASRec,
   SR-GNN). Evidencia de que los métodos simples compiten (Ludewig & Jannach y trabajos posteriores).
5. Mejor forma de combinar vista→vista, vista→compra y carrito, y cómo pesar la recencia dentro de la sesión.

### C. Reordenar con un modelo ("el modelo grande")
6. Arquitectura en dos etapas (candidatos + learning-to-rank con LightGBM LambdaRank o similar), como en
   OTTO: qué features importan más, versión mínima viable y cuántas horas lleva a principiantes.
7. Entrenar un ranker con un log sesgado: counterfactual learning-to-rank (Joachims et al. 2017, IPS en el
   entrenamiento), estimación del sesgo de posición (regression EM, Wang et al. 2018).
8. ¿Qué objetivo optimizar: P(clic), P(compra | clic), P(compra) o ingreso esperado, dado que puntúa la
   probabilidad de compra y desempata el ingreso?

### D. Evaluación offline fiable
9. Evaluación off-policy de listas con propensity por posición: IPS, SNIPS, doubly robust, estimador
   pseudoinverse (Swaminathan et al. 2017), estimadores con modelos de clic (Li et al. 2018). El ejemplo
   oficial solo usa IPS del slot 1 y da pocas coincidencias: ¿qué estimador usar con ~100.000 impresiones y
   cómo reducir la varianza?
10. ¿Cómo comprobar que la métrica offline predice la nota online? Protocolo de corte temporal, segmento frío
    e intervalos de confianza.

### E. Cold start
11. Productos nuevos con solo descripción: embeddings de texto para similitud producto-producto, cómo
    "colocar" un producto nuevo en el espacio de co-visitas (heredar las co-visitas de sus vecinos;
    DropoutNet, CB2CF o similares) y alternativas por atributos.
12. Usuarios nuevos con solo contexto (device, region, page) y quizá pocas vistas: popularidad por contexto,
    priors, bandits.

### F. Aprender del stream en vivo
13. Con ~3 peticiones/s, ¿qué aprendizaje online es robusto y explicable: bandits contextuales para listas
    (Thompson sampling, LinUCB, cascading bandits de Kveton et al. 2015), o algo más simple como reponderar la
    popularidad por la conversión observada? ¿Cuánto presupuesto de exploración, y cómo tratar la no
    estacionariedad (ventanas deslizantes, discounted Thompson sampling)?
14. Cómo aprovechar un feedback ruidoso (la compra es una realización de una probabilidad) y repartir el
    mérito entre posiciones.
15. Detectar tendencias en tiempo real (EWMA, lift frente a la media) y reaccionar a cambios de precio y
    roturas de stock.

### G. Ingeniería en tiempo real
16. Patrones para un servicio Python que consulta cada ~0,3 s y responde en < 3 s con features precalculadas
    en una base tipo ClickHouse (agregados incrementales, refrescos en segundo plano), sin duplicados y con
    fallback seguro.
17. Features en SQL de ClickHouse que sumen en "buen uso de la base de datos": ventanas temporales, arrays,
    argMax, tablas precalculadas.

### H. Prioridad y antecedentes
18. Competiciones parecidas y qué hicieron los ganadores: CLEF NewsREEL (recomendación de noticias en vivo
    con latencia corta), RecSys Challenge 2015 (YOOCHOOSE), Diginetica (CIKM Cup 2016), OTTO, Retail Rocket.
19. Errores típicos: fuga de datos, sobreajustar al periodo de práctica, sesgo de popularidad, distribución
    distinta en la final.
20. ¿Empezar por un sistema simple e ir subiendo, o ir directos a un modelo grande? ¿Qué partes del simple
    reutiliza el grande?

## Formato de la respuesta
1. Tabla final con TODAS las técnicas candidatas ordenadas por (ganancia esperada en ESTE problema) /
   (esfuerzo para principiantes), con horas estimadas y riesgo.
2. Para cada técnica recomendada: qué es en 3 frases sencillas, por qué encaja aquí, papers/soluciones de
   referencia con enlace, cómo implementarla en Python (librerías), cómo validarla offline con nuestro log y
   errores típicos.
3. Un plan por horas para 4 personas que trabajen en paralelo sin pisarse.
4. Separa claramente lo que tiene evidencia sólida de lo que es intuición.
```

---

## Preguntas para el stand de RawTree (no para el deep research)
1. El 0 de la nota: ¿los más vendidos de siempre (README) o los más vendidos de la categoría del último
   producto visto (diapositivas)?
2. ¿Cómo se calculan las "compras esperadas" de una lista de 10? ¿Suma de probabilidades por posición? ¿Un
   comprador compra como mucho un producto?
3. ¿El modelo oculto tiene en cuenta la posición? ¿Y el precio?
4. En la final, ¿hay user_id que ya aparecen en el historial o todos son nuevos? ¿Qué % de peticiones serán frías?
5. ¿Qué es la "referencia" de los organizadores que sale en el leaderboard?
