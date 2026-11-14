# RawTree: lo que necesita saber el agente

RawTree es una base de datos analítica para JSON (derivada de ClickHouse): se inserta JSON y se consulta con SQL,
sin esquema. Docs para agentes: https://rawtree.com/llms.txt

## Skill oficial de RawTree (ya incluida)
`.claude/skills/rawtree/` es la skill oficial de RawTree para agentes (licencia Apache 2.0, de github.com/rawtreedb/agent-skills).
Claude Code y OpenCode la cargan solos cuando la tarea toca RawTree. Antes de escribir SQL complicado, el agente debe
leer `references/query.md` y `references/dynamic-fields.md` de esa carpeta.

## Tres formas de usarla
1. **MCP** (lo más cómodo con agentes): servidor `https://mcp.rawtree.com/mcp` con cabecera
   `Authorization: Bearer <RAWTREE_API_KEY>`. Ya está configurado en `opencode.json`. En Claude Code:
   `claude mcp add --transport http rawtree https://mcp.rawtree.com/mcp --header "Authorization: Bearer $RAWTREE_API_KEY"`
   Usamos la clave del equipo como cabecera a propósito. Sin cabecera, el MCP pide iniciar sesión en el navegador
   con una cuenta personal de RawTree, que no tiene por qué ver las tablas del equipo.
2. **HTTP** desde Python (lo que usa el servicio):
   - Consultar: `POST https://api.rawtree.com/v1/query` con `{"sql": "SELECT ..."}` → respuesta con `data` (filas como dicts), `meta`, `rows`, `statistics`.
   - Insertar: `POST https://api.rawtree.com/v1/tables/<tabla>?database=<TEAM_ID>` con un objeto o lista JSON → `written_rows`.
   - Una sentencia por petición. También acepta `INSERT INTO ... SELECT`, `CREATE TABLE`, `DESCRIBE`, `EXPLAIN`.
3. **CLI**: `curl -fsSL https://rawtree.com/install.sh | bash` y luego
   `rtree query --api-key $RAWTREE_API_KEY --database $TEAM_ID "SELECT count() FROM shop.requests"`

## Trampas que rompen el SQL
- Todas las columnas son `Dynamic`. En JOIN, `argMax`/`argMin` y algunos agregados hay que castear:
  `a.product_id::String = b.product_id::String`, `ts::DateTime64(3)`, `price_eur::Float64`, `is_active::Bool`.
- Si sale `Unknown expression identifier` en una tabla en vivo, o está vacía o el nombre de columna está mal: haz `DESCRIBE shop.requests`.
- Las tablas en vivo crecen sin parar: filtra por tiempo (`WHERE ts > now() - INTERVAL 1 HOUR`) y pon `LIMIT`.
- `history_events` tiene ~12M filas: las auto-uniones (co-visitas) son caras. Precalcula UNA vez y guarda el resultado
  (en memoria, en `team_NN.scratch` o con `INSERT INTO team_NN.<tabla> SELECT ...`). Nunca dentro del bucle en vivo.
- `ts` está en UTC. Barcelona es UTC+1.

## Consultas ya escritas (en `starter/queries.sql`)
1. Catálogo actual (catálogo + últimas actualizaciones)  2. Popularidad por categoría en la última hora
3. Tendencias: vistas por subcategoría 15 min vs 3 h  4. Co-visitas de un producto
5. Nuestra conversión e ingreso por hora  6. Fiabilidad: motivo del fallback y latencia p50/p95
7. Clics por posición (sesgo de posición)

## Embeddings (HelmCode)
- Modelo `qwen3-embedding` en `https://api.helmcode.com/v1/embeddings` (compatible OpenAI). 4096 dimensiones,
  máx. 32 textos por petición, 60 peticiones/minuto. La clave es compartida por todo el datathon: calcula el catálogo
  UNA vez, guárdalo y reutilízalo.
