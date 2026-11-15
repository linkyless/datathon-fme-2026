# AGENTS.md — mental_descent() · Datathon FME 2026 · Reto RawTree

Contexto común para los agentes (OpenCode lo lee directamente; Claude Code a través de `CLAUDE.md`).
**Si una regla cambia, se cambia AQUÍ y en ningún otro sitio.**

**El evento terminó (dom 15/11/2026). El proyecto está cerrado.** Lo que corre es AP7 (`python -m src.service`).
Cómo se trabajó durante el evento (roles, estados por persona, procedimientos): `docs/archive/`.

## Qué es
Un recomendador EN TIEMPO REAL. Por cada fila nueva de `shop.requests` (~3 por segundo) se inserta en
`team_29.submissions` una lista ORDENADA de 10 `product_id` distintos y activos, en menos de 3 s desde el `ts` de la
petición. Un simulador oculto decide si el comprador hace clic y compra. Detalles: `docs/challenge.md`.
API y SQL de RawTree: `docs/rawtree.md`. Resumen del proyecto y resultados: `README.md`.

## Cómo explicar
- En español sencillo. Cualquier término técnico nuevo, explícalo en una frase la primera vez.
- Antes de editar más de un archivo, di en 3-5 líneas qué vas a hacer y espera un "ok".
- Al acabar, di: qué cambiaste, cómo lo comprobaste y qué falta.

## Reglas que NUNCA se rompen
1. **Claves**: `RAWTREE_API_KEY` y la clave de HelmCode viven solo en `.env` (`TEAM_ID` = `team_29` no es secreto).
   NUNCA las escribas en código, logs, commits, docs ni mensajes. NUNCA leas ni muestres el contenido de `.env`.
2. **Un solo servicio en vivo**: dos instancias de `src/service.py` = respuestas duplicadas. Para probar, usa
   `--no-send` o datos históricos.
3. **Cada respuesta**: exactamente 10 IDs, distintos, activos y en stock en ese momento, una sola por `request_id`.
   Compruébalo con `src/validate.py`.
4. **No inventes cifras**. Todo número sale de código ejecutado o de `team_29.feedback`. Si no está medido, "no medido".
5. **No inventes columnas ni funciones** de RawTree: `DESCRIBE`, el MCP `rawtree`, `docs/rawtree.md` o la skill
   `.claude/skills/rawtree/`.
6. Nunca subas datos (`*.csv`, `*.parquet`, `data/`) ni modelos al repo.
7. Cada cambio de modelo con números va en `docs/EXPERIMENTS.md`; cada decisión no trivial, en `docs/DECISIONS.md`.
   Los dos son el registro del evento, con los problemas que encontramos: se añade al final, no se reescribe.

## Mapa del repo
```
README.md            ← qué es, resultados, cómo ejecutarlo, versiones del modelo
AGENTS.md            ← este archivo        CLAUDE.md ← lo importa para Claude Code
src/                 ← código (nombres en inglés; ver README)
  service.py         ← el servicio en vivo (AP7 por defecto; --arms para repetir los A/B del evento)
  learner.py         ← el aprendiz: modelo entrenado con nuestro feedback (AP2-AP8)
  catalog.py · validate.py · rawtree.py · local_test.py
  ranking.py, state.py, tree.py, scoring.py, choice.py, ...  ← versiones del sábado (reglas, R5, R6, v2), en español
starter/             ← starter kit OFICIAL de los organizadores (no se edita)
docs/challenge.md    ← el reto          docs/rawtree.md    ← API y trucos SQL de RawTree
docs/EXPERIMENTS.md  ← cada medición    docs/DECISIONS.md  ← cada decisión, para un product manager
docs/research.md     ← investigación    docs/competition.md · docs/opening.md · docs/data.md · docs/presentation.md
docs/archive/        ← cómo trabajó el equipo durante el evento (estados, guías por rol, procedimientos, roadmap)
.githooks/pre-commit ← bloquea commits con claves o datos
```

## Comandos
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath .githooks  # activa el bloqueo de claves
python -m src.validate               # autotest del validador
python -m src.local_test             # prueba sin red (antes de cada commit)
python -m src.service --no-send      # el servicio sin enviar nada
```

## RawTree en 5 líneas (más en docs/rawtree.md)
- Query: `POST https://api.rawtree.com/v1/query` con `{"sql": "..."}` y cabecera `Authorization: Bearer $RAWTREE_API_KEY`.
- Insertar: `POST https://api.rawtree.com/v1/tables/<tabla>?database=<TEAM_ID>` con una lista JSON.
- Todas las columnas son `Dynamic`: en JOIN, `argMax`, `argMin` y agregados, castea: `product_id::String`, `ts::DateTime64(3)`.
- Las tablas en vivo son grandes y crecen: filtra SIEMPRE por tiempo (`WHERE ts > now() - INTERVAL ...`) y usa `LIMIT`.
- Nunca hagas consultas pesadas dentro del bucle que responde peticiones: precalcula y guarda en memoria.

## Git
- Commits pequeños en español o inglés. Añade archivos concretos (`git add src/x.py`), nunca `git add .` a ciegas.
- `git push` lo decide siempre la persona, no el agente.
