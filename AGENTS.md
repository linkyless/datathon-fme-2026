# AGENTS.md: mental_descent(), Datathon FME 2026, RawTree challenge

Shared context for coding agents (OpenCode reads it directly, Claude Code through `CLAUDE.md`).
**If a rule changes, it changes HERE and nowhere else.**

**The event is over (Sun 15/11/2026) and the project is closed.** What runs is AP7 (`python -m src.service`).
How the team worked during the event (roles, per-person status, procedures): `docs/archive/`.

## What it is
A REAL-TIME recommender. For every new row of `shop.requests` (~3 per second) we insert into `team_29.submissions` an
ORDERED list of 10 distinct, active `product_id`s, in under 3 s from the request's `ts`. A hidden simulator decides
whether the shopper clicks and buys. Details: `docs/challenge.md`.
RawTree API and SQL: `docs/rawtree.md`. Project summary and results: `README.md`.

## How to explain things
- Plain language. Any new technical term gets a one-sentence explanation the first time.
- Before editing more than one file, say in 3-5 lines what you're going to do and wait for an "ok".
- When done, say what you changed, how you checked it and what's left.

## Rules that NEVER break
1. **Keys**: `RAWTREE_API_KEY` and the HelmCode key only live in `.env` (`TEAM_ID` = `team_29` is not a secret).
   NEVER write them in code, logs, commits, docs or messages. NEVER read or print the contents of `.env`.
2. **A single live service**: two instances of `src/service.py` = duplicated responses. For testing use `--no-send`
   or historical data.
3. **Every response**: exactly 10 IDs, distinct, active and in stock at that moment, one per `request_id`.
   Check it with `src/validate.py`.
4. **Don't make up numbers**. Every number comes from code that ran or from `team_29.feedback`. If it wasn't
   measured, say "not measured".
5. **Don't make up RawTree columns or functions**: use `DESCRIBE`, the `rawtree` MCP, `docs/rawtree.md` or the skill
   in `.claude/skills/rawtree/`.
6. Never commit data (`*.csv`, `*.parquet`, `data/`) or models.
7. Every model change with numbers goes in `docs/EXPERIMENTS.md`; every non-trivial decision in `docs/DECISIONS.md`.
   Both are the event's log, problems included: append at the end, don't rewrite.

## Repo map
```
README.md            what it is, results, how to run it, model versions
AGENTS.md            this file            CLAUDE.md  imports it for Claude Code
src/                 code
  service.py         the live service (AP7 by default; --arms replays the event's A/B tests)
  learner.py         the learner: model trained on our own feedback (AP2-AP8)
  catalog.py, validate.py, rawtree.py, local_test.py
  ranking.py, state.py, tree.py, scoring.py, choice.py, ...  Saturday's versions (rules, R5, R6, v2)
starter/             OFFICIAL starter kit from the organisers (not edited)
docs/challenge.md    the challenge         docs/rawtree.md    RawTree API and SQL tricks
docs/EXPERIMENTS.md  every measurement     docs/DECISIONS.md  every decision, for a product manager
docs/research.md     research              docs/competition.md, docs/opening.md, docs/data.md, docs/presentation.md
docs/archive/        how the team worked during the event (status, role guides, procedures, roadmap)
.githooks/pre-commit blocks commits with keys or data
```

## Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath .githooks  # turns on the key blocker
python -m src.validate               # validator self-test
python -m src.local_test             # offline smoke test (before every commit)
python -m src.service --no-send      # the service without sending anything
```

## RawTree in 5 lines (more in docs/rawtree.md)
- Query: `POST https://api.rawtree.com/v1/query` with `{"sql": "..."}` and header `Authorization: Bearer $RAWTREE_API_KEY`.
- Insert: `POST https://api.rawtree.com/v1/tables/<table>?database=<TEAM_ID>` with a JSON list.
- Every column is `Dynamic`: in JOINs, `argMax`, `argMin` and aggregates, cast it: `product_id::String`, `ts::DateTime64(3)`.
- Live tables are big and keep growing: ALWAYS filter by time (`WHERE ts > now() - INTERVAL ...`) and use `LIMIT`.
- Never run heavy queries inside the loop that answers requests: precompute and keep it in memory.

## Git
- Small commits. Add specific files (`git add src/x.py`), never a blind `git add .`.
- `git push` is always the person's call, not the agent's.
