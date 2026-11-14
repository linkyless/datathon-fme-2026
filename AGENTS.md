# AGENTS.md — mental_descent() · Datathon FME 2026 · Reto RawTree

Este archivo es el contexto común del equipo. Lo leen OpenCode (directamente) y Claude Code (a través de `CLAUDE.md`).
**Si una regla cambia, se cambia AQUÍ y en ningún otro sitio.** `CLAUDE.md` solo lo importa.

## Qué estamos construyendo
Un recomendador EN TIEMPO REAL. Por cada fila nueva de `shop.requests` (~3 por segundo) hay que insertar en
`team_NN.submissions` una lista ORDENADA de 10 `product_id` distintos y activos, en menos de 3 s desde el `ts` de la
petición. Un simulador oculto decide si el comprador hace clic y compra. Ganamos si vendemos más que el baseline
(los 10 más vendidos de siempre en stock). Detalles completos: `docs/reto.md`. API y SQL de RawTree: `docs/rawtree.md`.

## Con quién hablas
El equipo son cuatro estudiantes (Lester, Leonardo, Alex, Artai). **La mayoría somos principiantes** en ML, SQL y Git.
- Explica lo que haces en español sencillo. Cualquier término técnico nuevo, explícalo en una frase la primera vez.
- Antes de editar más de un archivo, di en 3-5 líneas qué vas a hacer y espera un "ok".
- Si algo es ambiguo, pregunta UNA cosa concreta en vez de suponer.
- Al acabar, di: qué cambiaste, cómo lo comprobaste y qué falta.

## Reglas que NUNCA se rompen
1. **Claves**: `RAWTREE_API_KEY`, `TEAM_ID` y la clave de HelmCode viven solo en `.env` o en `/connect`.
   NUNCA las escribas en código, logs, commits, docs ni mensajes. NUNCA leas ni muestres el contenido de `.env`.
2. **Un solo servicio en vivo**: solo el OPERADOR (ver `docs/ROADMAP.md`) ejecuta el servicio que envía a
   `submissions`. Nadie más lo lanza, ni "para probar": dos instancias = respuestas duplicadas. Para probar, usa
   datos históricos o el modo sin envío.
3. **Cada respuesta**: exactamente 10 IDs, distintos, activos y en stock en ese momento, una sola por `request_id`.
   Compruébalo con `src/validar.py` antes de dar algo por terminado.
4. **No inventes cifras**. Todo número que aparezca en docs, slides o mensajes sale de código ejecutado o de
   `team_NN.feedback`. Si no lo has medido, di "no medido".
5. **No inventes columnas ni funciones**. Si no sabes cómo se llama algo en RawTree, consúltalo (`DESCRIBE`,
   el MCP `rawtree`, `docs/rawtree.md` o la skill oficial `.claude/skills/rawtree/`).
6. Todo el código se escribe durante el evento. Librerías open source sí. Nunca subas datos (`*.csv`, `*.parquet`)
   ni modelos al repo.
7. El equipo tiene que poder explicar todo al jurado. Si propones algo no trivial, añade 2-3 frases en
   `docs/DECISIONES.md` explicándolo como a un product manager.

## Cómo trabajamos los cuatro (contexto compartido)
- La única memoria común es este repo. Lo que no está escrito aquí, los demás (y sus agentes) no lo saben.
- Cada persona tiene su archivo `docs/estado/<nombre>.md`. Solo esa persona (o su agente) lo edita.
- **Al empezar una sesión**: sigue `docs/procedimientos/empezar.md` (git pull + leer el estado de todos).
- **Al terminar una tarea**: sigue `docs/procedimientos/handoff.md` (actualizar estado, decisiones, commit).
- Experimentos con números: una fila en `docs/EXPERIMENTOS.md`. Decisiones: `docs/DECISIONES.md`.
- Si te equivocas en algo que se pueda repetir, propone añadir una línea a este archivo para que no vuelva a pasar.

## Mapa del repo
```
AGENTS.md            ← este archivo (reglas comunes)
CLAUDE.md            ← importa AGENTS.md para Claude Code
opencode.json        ← modelos HelmCode + MCP RawTree + permisos para OpenCode
starter/             ← starter kit OFICIAL de los organizadores (no lo edites; cópialo a src/ para cambiarlo)
src/                 ← nuestro código (servicio, features, evaluación, validador)
docs/reto.md         ← el reto completo     docs/rawtree.md   ← API y trucos SQL
docs/ROADMAP.md      ← plan y quién hace qué docs/GUIA.md      ← guía y FAQ para humanos
docs/estado/         ← un archivo por persona docs/procedimientos/ ← pasos de /empezar, /handoff, /eval...
docs/DECISIONES.md   docs/EXPERIMENTOS.md
docs/CONTEXTO.md     ← todo lo ocurrido antes de empezar a programar (léelo una vez)
.githooks/pre-commit ← bloquea commits con claves o datos
.claude/skills/rawtree/ ← skill OFICIAL de RawTree para agentes (no la edites)
```

## Comandos
```bash
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
git config core.hooksPath .githooks  # una vez por persona: activa el bloqueo de claves
python -m src.validar                # autotest del validador
python starter/recommender.py        # baseline oficial EN VIVO (solo el operador)
```

## RawTree en 5 líneas (más en docs/rawtree.md)
- Query: `POST https://api.rawtree.com/v1/query` con `{"sql": "..."}` y cabecera `Authorization: Bearer $RAWTREE_API_KEY`.
- Insertar: `POST https://api.rawtree.com/v1/tables/<tabla>?database=<TEAM_ID>` con una lista JSON.
- Todas las columnas son `Dynamic`: en JOIN, `argMax`, `argMin` y agregados, castea: `product_id::String`, `ts::DateTime64(3)`.
- Las tablas en vivo son grandes y crecen: filtra SIEMPRE por tiempo (`WHERE ts > now() - INTERVAL ...`) y usa `LIMIT`.
- Nunca hagas consultas pesadas dentro del bucle que responde peticiones: precalcula y guarda en memoria o en `team_NN.scratch`.

## Cómo hacer una tarea de código
1. Lee los archivos que vas a tocar antes de cambiarlos.
2. Cambios pequeños: un objetivo por vez. No reescribas archivos enteros si basta con cambiar una función.
3. Comprueba: ejecuta el código, `python -m src.validar` y, si cambia el ranking, la evaluación offline (`docs/procedimientos/eval.md`).
4. Dependencia nueva → añádela a `requirements.txt` en el mismo commit.
5. Python 3.11+, funciones pequeñas con nombres claros, comentarios en español solo donde aporten.

## Git (versión principiante)
- Antes de empezar: `git pull --rebase`. Trabaja en una rama: `git switch -c nombre/tarea` (ej. `alex/coview`).
- Commits pequeños en español: `coview: filtra productos ya comprados`. Añade archivos concretos (`git add src/x.py`), nunca `git add .` a ciegas.
- `git push` lo decide siempre la persona, no el agente. A `main` solo llega lo que funciona.
- La versión que corre en la ronda final se etiqueta: `git tag final`.

## Calendario (hora de Barcelona)
- Sáb 10:00 → Dom 10:00: práctica, leaderboard público cada 10 min. NO puntúa.
- Dom 08:00: congelamos lo grande. 08:00-10:00 solo estabilidad.
- **Dom 10:00 → 12:00: ronda final, la única que puntúa.** Leaderboard oculto: medimos con `team_NN.feedback`.
- Dom tarde: presentación + demo al jurado (30 puntos).
