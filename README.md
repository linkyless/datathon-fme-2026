# mental_descent() · Datathon FME 2026 · RawTree challenge

A real-time recommender for a simulated online shop: for every shopper (~3 per second) it picks 10 ordered products
in under 3 seconds. A hidden model decides whether the shopper clicks and buys. Team: Lester, Leonardo, Alex, Artai.

**Status: finished.** The final model is **AP7**, a learner trained only on our own live feedback.
The docs in `docs/` are the logbook of the event, in Spanish; the code is in English.

## Results (measured)
Leaderboard window Sun 15/11 06:26-07:26 (Barcelona), expected purchases per 100 requests:

| | Conversion | Cold-start conversion | On time |
|---|---|---|---|
| **Us (AP7 / AP8 A/B)** | **1.73** (14th of 42) | 2.07 | 99.99 % |
| Official baseline (all-time best sellers) | 0.53 | 0.75 | 100 % |
| Organisers' reference | 1.83 | 1.59 | 100 % |
| Leader | 2.17 | | |

The live A/B tests that shaped the model (full detail, with confidence intervals, in `docs/EXPERIMENTS.md`):

| When | Test | Result |
|---|---|---|
| Sat 20:50 | first learner (AP2) vs rule C (category best sellers) | real purchases 2.03 % vs 0.83 %, difference CI [+0.56, +1.87] |
| Sat 22:00 | AP3 vs AP3 exploring slots 9-10 | exploring costs ~0.3 purchases/100 in the requests that explore: switched off |
| Sat 23:47 | four orderings of the same weights (AP3-AP6) | the model's own order wins: 1.86 vs 1.56 / 1.51 / 1.33 |
| Sun 04:54 | AP7 retrained (APHC) vs hybrid by shopper type (APOH2) | 1.75 vs 1.58 |
| Sun 06:15 | AP7 vs choice model AP8 | 1.77 vs 1.69: a tie (AP8 +14 % clicks, no more purchases) |

## How AP7 works
1. **World** (`src/learner.py`): what is known about every shopper and product at each instant, fed event by event
   from `shop.events` (carts today, carts in the 60-day history, views in the session, category affinity, purchases).
2. **Candidates**: their carts and recent views, best sellers of the category/subcategory they are looking at and of
   the categories they usually view, plus the shop's best sellers. Anything already bought is never shown.
3. **Score**: a logistic regression (one neuron, 12 signals) learned from `team_29.feedback` by replaying the day in
   order, so no signal ever looks into the future. Each weight reads as "× purchase probability" (model `aphc`):
   viewed in the session ×6.7, in today's cart ×6.2, in an old cart ×5.0, same subcategory as the last viewed ×1.44,
   already bought ×0.18 (and removed anyway).
4. **Service** (`src/service.py`): polls `shop.requests`, answers, validates every list and sends in batches from
   three threads; one response per `request_id`, even across restarts; falls back to best sellers if anything fails.

Why a learner on our own feedback: the old carousel in the history only showed popular products, so no model trained
on it could learn that a product in the shopper's cart sells ~10 times more than an unrelated one in slot 1
(2.5 % vs 0.22 %). Our feedback did.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                # fill in TEAM_ID and RAWTREE_API_KEY
git config core.hooksPath .githooks                 # blocks commits with keys or data
python -m src.validate && python -m src.local_test  # offline checks
python -m src.learner --name aphc --without o1,o2,h,x   # train AP7 (~2 min; downloads to data/, never committed)
python -m src.service --no-send                     # run everything except sending
python -m src.service                               # LIVE (only one instance at a time)
```

## Model versions
Every version is kept. The learner versions still run (`python -m src.service --arms <set>`, see `ARM_SETS`);
the git tags point at the exact code that ran live.

| Version | What | Code | Tag |
|---|---|---|---|
| v0 / C | best sellers of the last viewed category | rule in `service.py`, `ranking.py` | `version/v0` |
| v1 | + co-visits ("who viewed X viewed Y") | `ranking.py`, `covisits.py` | `version/v1` |
| CF | v0 without what the shopper already bought | `ranking.py`, `users.py` | `version/cf` |
| R5 | logistic regression on the old carousel | `scoring.py` | `version/r5` |
| v2 | conditional logit on the old carousel (prepared, never live) | `choice.py` | `version/v2` |
| A6 / A7 | LightGBM tree (R6) on the old carousel | `tree.py` | `version/a6`, `version/a7` |
| AP2 / AP3 | learner on our feedback | `learner.py` | `version/ap2`, `version/ap3` |
| AP4-AP6 | other orderings of AP3's scores | (removed from the service) | `version/ap4-ap6` |
| **AP7** | learner + same subcategory signal: **final** | `learner.py` (model `aphc`) | `version/ap7` |
| APO / APH | organic history numbers, new-shopper weights, hybrids | `learner.py` | `version/ap8` |
| AP8 | choice model ("which of the 10 was chosen") | `learner.py --choice` | `version/ap8` |

## What went wrong (and what we learned)
All of it is recorded, with numbers, in `docs/EXPERIMENTS.md` and `docs/DECISIONS.md`:
- **The history lies about repurchases**: 62 % of its purchases were of products the shopper already had; live there
  are none. R5 learned "already bought ×1.8" and put it first: 0.47 % conversion.
- **Offline evaluation rewarded resembling the old carousel**: 100 % of test purchases were in what it showed.
  We moved to live A/B tests split by user (crc32 of `user_id`).
- **Leakage**: a "favourite category" signal was measured with activity after the response and lost live.
- **Reliability was our wifi** (48 % signal, 10 % packet loss): ~80 % on time on Saturday afternoon, 99.99 % later.
- **Short A/Bs are noise**: with ~2,500 requests per arm a difference of 0.2-0.3 is not distinguishable.
- **Per-product logloss misleads for ordering** and a better offline choice measure (AP8: 1.81 → 1.64) did not
  turn into more purchases live.

## Repo map
```
src/service.py      live service              src/learner.py   the learner (AP2-AP8)
src/catalog.py      catalog, stock, sellers   src/validate.py  10 valid IDs check
src/rawtree.py      RawTree client            src/local_test.py offline smoke test
src/evaluate.py     offline evaluation (IPS, bootstrap)   src/panel.py  our own leaderboard from team_29.feedback
src/ranking.py, state.py, covisits.py, users.py, trends.py, coldstart.py, embeddings.py, scoring.py, choice.py,
tree.py, panel_tukey.py   Saturday's versions and data pieces (comments in Spanish)
starter/            official starter kit (unmodified)
docs/               challenge, RawTree notes, EXPERIMENTS, DECISIONS, research, presentation
docs/archive/       how the team worked during the event (status per person, role guides, procedures)
```
Module names during the event were Spanish: `aprendiz`→`learner`, `servicio`→`service`, `catalogo`→`catalog`,
`validar`→`validate`, `prueba_local`→`local_test`, `estado`→`state`, `arbol`→`tree`, `puntuacion`→`scoring`,
`eleccion`→`choice`, `covisitas`→`covisits`, `tendencias`→`trends`, `usuarios`→`users`, `evaluar`→`evaluate`
(and `vivo` was removed: it was never wired in). The logbook in `docs/` keeps the original names.
