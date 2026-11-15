# RawTree: what the agent needs to know

RawTree is an analytical database for JSON (derived from ClickHouse): you insert JSON and query it with SQL, no schema.
Docs for agents: https://rawtree.com/llms.txt

## Official RawTree skill (already included)
`.claude/skills/rawtree/` is the official RawTree skill for agents (Apache 2.0 licence, from
github.com/rawtreedb/agent-skills). Claude Code and OpenCode load it on their own when a task touches RawTree. Before
writing complicated SQL, read `references/query.md` and `references/dynamic-fields.md` in that folder.

## Three ways to use it
1. **MCP** (the most convenient with agents): server `https://mcp.rawtree.com/mcp` with the header
   `Authorization: Bearer <RAWTREE_API_KEY>`. Already set up in `opencode.json`. In Claude Code:
   `claude mcp add --transport http rawtree https://mcp.rawtree.com/mcp --header "Authorization: Bearer $RAWTREE_API_KEY"`
   We use the team key as a header on purpose. Without it, the MCP asks to log in through the browser with a personal
   RawTree account, which won't necessarily see the team's tables.
2. **HTTP** from Python (what the service uses):
   - Query: `POST https://api.rawtree.com/v1/query` with `{"sql": "SELECT ..."}` -> response with `data` (rows as
     dicts), `meta`, `rows`, `statistics`.
   - Insert: `POST https://api.rawtree.com/v1/tables/<table>?database=<TEAM_ID>` with a JSON object or list ->
     `written_rows`.
   - One statement per request. It also accepts `INSERT INTO ... SELECT`, `CREATE TABLE`, `DESCRIBE`, `EXPLAIN`.
3. **CLI**: `curl -fsSL https://rawtree.com/install.sh | bash` and then
   `rtree query --api-key $RAWTREE_API_KEY --database $TEAM_ID "SELECT count() FROM shop.requests"`

## Traps that break the SQL
- Every column is `Dynamic`. In JOINs, `argMax`/`argMin` and some aggregates you have to cast:
  `a.product_id::String = b.product_id::String`, `ts::DateTime64(3)`, `price_eur::Float64`, `is_active::Bool`.
- If you get `Unknown expression identifier` on a live table, either it's empty or the column name is wrong: run
  `DESCRIBE shop.requests`.
- Live tables grow non-stop: filter by time (`WHERE ts > now() - INTERVAL 1 HOUR`) and use `LIMIT`.
- `history_events` has ~12M rows: self-joins (co-visitation) are expensive. Precompute ONCE and store the result
  (in memory, in `team_NN.scratch` or with `INSERT INTO team_NN.<table> SELECT ...`). Never inside the live loop.
- `ts` is in UTC. Barcelona is UTC+1.

## Ready-made queries (in `starter/queries.sql`)
1. Current catalog (catalog + latest updates)  2. Popularity per category in the last hour
3. Trends: views per subcategory 15 min vs 3 h  4. Co-visits of a product
5. Our conversion and revenue per hour  6. Reliability: fallback reason and p50/p95 latency
7. Clicks per position (position bias)

## Embeddings (HelmCode)
- Model `qwen3-embedding` at `https://api.helmcode.com/v1/embeddings` (OpenAI-compatible). 4096 dimensions, max 32
  texts per request, 60 requests/minute. The key is shared by the whole datathon: compute the catalog ONCE, store it
  and reuse it.
