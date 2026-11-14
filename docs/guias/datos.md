# Guía de Datos y relato (Artai · OpenCode + GLM 5.3 Flash)

**Tu misión:** conocer los datos mejor que nadie, resolver los compradores y productos NUEVOS (cold start:
10 puntos) y tener la presentación lista (jurado: 30 puntos). La final está llena de productos y compradores
nuevos: tu parte es la que hace que generalicemos en vez de memorizar.

**Tus archivos:** `src/coldstart.py`, `src/embeddings.py`, `docs/datos.md`, `docs/presentacion.md`.
**No toques:** el resto. Nunca subas los embeddings al repo: van en `data/` (no se sube) y en `team_29.scratch`.

**Antes de empezar:** sigue la Parte 2 de `docs/GUIA.md` (sección OpenCode). Añade a tu `.env` la línea
`HELMCODE_API_KEY=...` con la clave del anuncio de HelmCode (la necesita el script de embeddings). Lee
`docs/guias/README.md` (el contrato). Si GLM da error 429, cambia a DeepSeek V4 Flash con `/models`.

**Con quién te cruzas (y con nadie más):**
- **Alex** mide tus piezas de cold start (D3, D4) en el segmento frío. Tú le avisas; no le esperes.
- **Lester** tiene que llamar a `embeber_nuevos()` desde el servicio (D4). Lo pides por tu archivo de estado.
- **Todos** te pasan números para la presentación, pero solo desde `docs/EXPERIMENTOS.md` y `docs/DECISIONES.md`:
  no tienes que perseguir a nadie.
- Con Leonardo no te cruzas: `ranking.py` usa tus piezas solo en cuanto existen.

---

## D1 · Embeddings del catálogo — EMPEZAR YA, hasta las 14:30
Primero esto, porque la clave de HelmCode la comparte todo el datathon y cada hora estará más saturada.
Un embedding convierte un texto en una lista de números; textos parecidos dan listas parecidas. Pega a tu agente:
> Lee docs/guias/README.md y docs/rawtree.md. Crea src/embeddings.py (`python -m src.embeddings`) que:
> 1. Lea product_id, category, subcategory, brand y description de shop.catalog con `src.rawtree.query`.
> 2. Para cada producto forme el texto `"{category} > {subcategory} | {brand} | {description}"`.
> 3. Pida los embeddings a `https://api.helmcode.com/v1/embeddings` (formato OpenAI: `{"model":
>    "qwen3-embedding", "input": [textos]}`), con la clave de la variable de entorno HELMCODE_API_KEY (cárgala del
>    .env igual que src/rawtree.py; nunca la imprimas). Máximo 32 textos por petición y 60 peticiones por minuto:
>    espera 1,1 s entre peticiones y, si responde 429, espera 20 s y reintenta.
> 4. Normalice cada vector (longitud 1) y guarde data/embeddings.npy y data/embeddings_ids.json. Si se corta a
>    medias, que al relanzarlo continúe donde iba.
> 5. Suba los vectores a team_29.scratch con `src.rawtree.insertar("scratch", filas)`, en lotes de 100, con filas
>    `{"tipo": "emb_v1", "product_id": ..., "vec": <vector en float16 codificado en base64>}`.
> Pruébalo primero con 64 productos. Luego lánzalo entero.

**Hecho cuando:** `data/embeddings.npy` tiene tantas filas como productos y
`SELECT count() FROM team_29.scratch WHERE tipo = 'emb_v1'` da lo mismo. Commit (solo el .py) y push.

## D2 · Explorar los datos — mientras corren los embeddings, hasta las 15:00
Pega a tu agente (solo consultas, sin escribir código):
> Usando el MCP rawtree, haz DESCRIBE de shop.catalog, shop.history_sessions, shop.history_events, shop.requests,
> shop.events y shop.catalog_updates, y responde con números en docs/datos.md:
> 1. Columnas y tipos de cada tabla (¿tiene history_sessions is_new_user o page?).
> 2. Valores de context.device, context.region y context.page, y qué % tiene cada uno.
> 3. En shop.requests de la última hora: % con is_new_user = true y % con recent_views vacío.
> 4. Cuántos new_product, price_change y out_of_stock hay en shop.catalog_updates hasta ahora.
> 5. Precio medio y mediano por categoría, y si el % de compras cambia según device o region.
> Todo número tiene que salir de una consulta ejecutada. Pon la consulta debajo de cada respuesta.

**Hecho cuando:** `docs/datos.md` responde a las 5 preguntas. Commit y push, y avisa en el chat: lo usarán todos.

## D3 · Compradores nuevos: populares por tipo de comprador — hasta las 16:30
Pega a tu agente:
> Rellena `cargar_populares_contexto(hasta)` en src/coldstart.py: para cada pareja (device, region), los 50
> productos más comprados en shop.history_events (event_type = 'purchase', ts < hasta si hay hasta), de más a
> menos. Devuelve `{(device, region): [product_id, ...]}`. Comprueba con DESCRIBE los nombres de las columnas.
> Después ejecuta `python -m src.prueba_local`.

**Hecho cuando:** devuelve una lista por pareja y la prueba pasa. Commit, push y pide a Evaluación que mida el
segmento frío.

## D4 · Productos nuevos: vecinos por descripción — hasta las 19:00
Pega a tu agente:
> Rellena `cargar_vecinos(hasta)` en src/coldstart.py: lee de team_29.scratch las filas tipo 'emb_v1', decodifica
> los vectores y, para cada producto, calcula los 20 más parecidos (producto escalar, porque ya están
> normalizados) con numpy. Devuelve `{product_id: [vecino, ...]}`. Añade además una función
> `embeber_nuevos()` que busque en shop.catalog_updates los `new_product` que aún no estén en scratch, calcule
> su embedding igual que src/embeddings.py y lo suba. Ejecuta `python -m src.prueba_local`.

**Hecho cuando:** un producto cualquiera tiene vecinos que tienen sentido (misma subcategoría o parecida). Enséñale
3 ejemplos al equipo. Pide al Operador que llame a `embeber_nuevos()` cada 5 minutos en el servicio.

## D5 · La presentación — empezar el sábado, no el domingo
Crea `docs/presentacion.md` con este guion y ve rellenándolo con lo que salga en EXPERIMENTOS y DECISIONES:
1. El problema en una frase. 2. Nuestra escalera (dibujo). 3. Cómo validamos antes de arriesgar (IPS, sin
fórmulas). 4. Resultados (solo números medidos). 5. Cómo aprende del stream. 6. Cómo usamos RawTree.
7. Qué haríamos con más tiempo. Revisión con el equipo en el punto de control de las 21:00.

---

## Fase 2 (tras el deep research)
_Vacío. Aquí irán ideas avanzadas de cold start y de uso de RawTree (reto agéntico...). No empezar hasta que esté escrito._
