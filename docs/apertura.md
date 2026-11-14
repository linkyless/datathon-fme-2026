# Diapositivas de la apertura (RawTree/Tinybird, sáb 14/11) — lo que importa

Resumen del HTML oficial de las diapositivas. Solo lo que añade algo a `docs/reto.md`.

## Frases literales que cuentan
- **Baseline (el 0 de la nota):** *"Best sellers of the category of the last product viewed: the starter kit."*
  Y en la historia: *"Today it just shows the best sellers of the category of the last product viewed."*
  → Ojo: el código del starter hace "los más vendidos de SIEMPRE", no por categoría. Si el 0 es por categoría, el
  starter puntúa por DEBAJO de 0 (se recorta a 0). Por eso nuestro `ranking.py` usa como relleno los más vendidos
  de la categoría de lo último visto: así nunca estamos por debajo de ninguna de las dos definiciones.
- **Fallback** (respuesta tarde o inválida): *"replaced by the best sellers"*.
- **Oráculo:** *"The best list possible, picked by the hidden model itself. Out of reach."*
- **Sin suerte:** *"Purchase probability, not the coin flip; bought items lose appeal."*
- **Desempate:** ingreso esperado por petición.
- **Cold start:** *"New shopper, or a session viewing a new product."*
- **La tienda se mueve:** *"Teams that generalise beat teams that memorise."* Compradores nuevos (muchos sin
  historial), productos nuevos (solo descripción), tendencias (*"visible early in the live events"*), cambios de
  precio (en catalog_updates) y roturas de stock (*"inactive products are invalid answers"*).
- **Ejemplo de `model_version` en la diapositiva:** `"v3-mf-bandit"` (mf = matrix factorization; bandit =
  aprendizaje online con exploración). Es una pista de por dónde esperan que vayan los buenos equipos.
- **Latencia:** el baseline responde en ~1 s. Enviar en lotes, p. ej. cada medio segundo.
- **Cierre:** *"Speed wins!"*

## Nota (puntos)
Stream 70: conversión (compras esperadas por 100 peticiones), cold start, fiabilidad. Jurado 30: método, aprender
del stream, uso de RawTree, comunicación. Solo cuenta dom 10:00-12:00 (CET).

## Reto paralelo: "Best agentic use of RawTree"
Un agente de IA que usa RawTree para guardar, analizar y actuar. *"The agent is the protagonist, not a wrapper."*
Decide qué ingerir, qué preguntar y qué hacer con las respuestas (CLI, API, SDK o MCP). Se entrega repo + README +
demo corta. *"A small project with a great story wins."*

## Herramientas que mencionan
- Docs para agentes: `rawtree.com/llms.txt`. Skill oficial: `npx skills add rawtreedb/agent-skills` (ya la tenemos).
- MCP: `https://mcp.rawtree.com/mcp`. CLI: `rtree`. SDK TypeScript `@rawtree/sdk`.
- RawTree es *"fully ClickHouse compatible"*: se puede usar cualquier función de ClickHouse.
