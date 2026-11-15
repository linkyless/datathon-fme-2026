# Opening slides (RawTree/Tinybird, Sat 14/11): what matters

Summary of the official slides' HTML. Only what adds something to `docs/challenge.md`.

## Literal quotes that matter
- **Baseline (the 0 of the score):** *"Best sellers of the category of the last product viewed: the starter kit."*
  And in the story: *"Today it just shows the best sellers of the category of the last product viewed."*
  Note: the starter code does "all-time best sellers", not per category. If the 0 is per category, the starter scores
  BELOW 0 (clipped to 0). That's why our `ranking.py` uses the best sellers of the last viewed category as filler: we
  are never below either definition.
- **Fallback** (late or invalid answer): *"replaced by the best sellers"*.
- **Oracle:** *"The best list possible, picked by the hidden model itself. Out of reach."*
- **No luck involved:** *"Purchase probability, not the coin flip; bought items lose appeal."*
- **Tie-break:** expected revenue per request.
- **Cold start:** *"New shopper, or a session viewing a new product."*
- **The store moves:** *"Teams that generalise beat teams that memorise."* New shoppers (many with no history), new
  products (description only), trends (*"visible early in the live events"*), price changes (in catalog_updates) and
  stock-outs (*"inactive products are invalid answers"*).
- **`model_version` example on the slide:** `"v3-mf-bandit"` (mf = matrix factorization; bandit = online learning with
  exploration). A hint of where they expect good teams to go.
- **Latency:** the baseline answers in ~1 s. Send in batches, e.g. every half second.
- **Closing:** *"Speed wins!"*

## Score (points)
Stream 70: conversion (expected purchases per 100 requests), cold start, reliability. Jury 30: method, learning from
the stream, use of RawTree, communication. Only Sun 10:00-12:00 (CET) counts.

## Side challenge: "Best agentic use of RawTree"
An AI agent that uses RawTree to store, analyse and act. *"The agent is the protagonist, not a wrapper."*
It decides what to ingest, what to ask and what to do with the answers (CLI, API, SDK or MCP). Deliverable: repo +
README + short demo. *"A small project with a great story wins."*

## Tools they mention
- Docs for agents: `rawtree.com/llms.txt`. Official skill: `npx skills add rawtreedb/agent-skills` (we have it).
- MCP: `https://mcp.rawtree.com/mcp`. CLI: `rtree`. TypeScript SDK `@rawtree/sdk`.
- RawTree is *"fully ClickHouse compatible"*: any ClickHouse function works.
