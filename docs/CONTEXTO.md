# CONTEXTO COMPLETO — todo lo ocurrido hasta el sábado 14/11 a las 12:30

Documento de traspaso. Lo escribió el asistente que ayudó a Lester a preparar el datathon, para que el agente que
lo lea (Claude Code u OpenCode) tenga TODO el contexto y pueda decidir el siguiente paso. Léelo entero junto con
`AGENTS.md`, `docs/reto.md`, `docs/rawtree.md`, `docs/ROADMAP.md` y `docs/GUIA.md`.

## 1. Quiénes somos y cómo hay que tratarnos
- Equipo `mental_descent()` en el Datathon FME 2026 (UPC Barcelona, 14-15 nov 2026). Cuatro personas: Lester,
  Leonardo, Alex y Artai. Reto elegido: **RawTree · Real-Time Recommendation**.
- Lester es estudiante de Ingeniería Informática (UCA), programador competitivo en C++. Fuerte en algoritmos y
  matemáticas, pero **no sabe ML, ni desarrollo web, ni casi SQL**. El resto del equipo también es principiante.
- Cómo explicarle las cosas a Lester: en español, en prosa, sin dar por hecho términos técnicos (explicar cada uno
  la primera vez), apoyándose en lo que ya sabe (algoritmos, C++), con datos justos y sin textos interminables.
  Pasos concretos y comprobables.
- Herramientas: hay **tres suscripciones de Claude** en el equipo (Claude Code). El resto usará **OpenCode** con los
  modelos gratuitos de **HelmCode** (ver sección 4).

## 2. Estado actual (12:30 del sábado)
- **El baseline oficial está EN VIVO** desde las 12:27 en el portátil de Lester. Imprime `answered N` cada fracción
  de segundo. Es el único proceso que responde por el equipo. **No hay que pararlo ni lanzar otro.**
  - Carpeta: `C:\Users\linky\datathon-fme-2026` (clon del starter oficial), con su propio `.venv` y su `.env`.
- Credenciales: `TEAM_ID=team_29`. La `RAWTREE_API_KEY` está en el `.env` (no copiarla nunca a ningún sitio). Se
  probó con una consulta `SELECT 1` y funciona.
- Nuestro repo (este) está descomprimido o por descomprimir en `C:\Users\linky\datathon-repo`. **Pendiente:**
  `git init`, primer commit, subirlo a GitHub privado (`gh repo create datathon-fme-2026 --private --source=. --push`)
  e invitar a Leonardo, Alex y Artai como colaboradores. Usuario de GitHub de Lester: `linkyless`.
- Roles del ROADMAP: **sin asignar todavía**.
- No se ha mirado aún el leaderboard (debería aparecer team_29 a los 10-20 min de arrancar).

## 3. El portátil de Lester (Windows) y sus trampas
- Windows con PowerShell, usuario `linky`.
- **Hay dos Python**: `python` a secas abre el de MSYS2 (`C:\msys64\ucrt64\bin\python.exe`, sin pip). El bueno es
  Python 3.13 oficial, accesible con `py`. Solución aplicada: `py -m venv .venv`, activar con
  `.venv\Scripts\Activate.ps1`, y dentro del entorno usar `python -m pip ...`. Con `(.venv)` delante, `python` es el bueno.
- **PowerShell rompe el JSON**: `curl.exe -d '{\"sql\":...}'` dio "Invalid request body" e `Invoke-RestMethod` dio
  "Internal Server Error" incluso con `SELECT 1`. Lo que funciona: guardar el JSON en un archivo
  (`Out-File -Encoding ascii body.json`) y enviarlo con `curl.exe ... --data-binary "@body.json"`. Mejor aún: hacer
  las consultas desde Python o con el MCP de RawTree.
- Para que el portátil no se duerma: Configuración → Sistema → Energía → suspender "Nunca" con cargador.

## 4. Herramientas de IA
- **HelmCode** (patrocinador) da acceso gratis a todos los participantes: API compatible con OpenAI en
  `https://api.helmcode.com/v1`. Modelos incluidos: `glm5.3-flash` (el recomendado para agentes de código),
  `deepseek-v4-flash`, `qwen3.6`, `gemma4`. Claude/GPT/Gemini en HelmCode son de pago: no los tenemos.
  - Límites por clave: 100 peticiones/min, 10 simultáneas para GLM/DeepSeek, 2M tokens/min. La clave es la misma para
    todo el datathon, así que puede haber errores 429 (esperar o cambiar de modelo).
  - **Embeddings gratis**: `qwen3-embedding` (4096 dimensiones, 32 textos por petición, 60 peticiones/min). Plan:
    calcular PRONTO los embeddings de las ~5.000 descripciones del catálogo (~157 peticiones, ~3 min) antes de que
    otros equipos saturen la clave, guardarlos en `team_29.scratch` y usarlos para productos nuevos (cold start).
- **OpenCode** lee `AGENTS.md` (y NO lee `CLAUDE.md` si existe `AGENTS.md`). Por eso las reglas viven en `AGENTS.md`
  y `CLAUDE.md` solo lo importa. Config en `opencode.json` del repo (sin claves): proveedor `helmcode`, MCP de
  RawTree, permisos. La clave de HelmCode se mete con `/connect` usando el id exacto `helmcode`.
- **Claude Code** con la suscripción de cada uno. MCP de RawTree con la clave del equipo como cabecera:
  `claude mcp add --transport http rawtree https://mcp.rawtree.com/mcp --header "Authorization: Bearer $RAWTREE_API_KEY"`
  (sin cabecera pediría login con una cuenta personal, que no ve nuestras tablas).
- **Skill oficial de RawTree** copiada en `.claude/skills/rawtree/` (Apache 2.0). Úsala antes de escribir SQL raro.

## 5. Lo que sabemos del reto (resumen; detalle en docs/reto.md)
- ~3 peticiones/s en `shop.requests`. Para cada una, 10 product_id distintos, activos y en stock, en < 3 s, una sola
  respuesta, enviando en lotes a `team_29.submissions`. Tarde o inválido → fallback (los más vendidos).
- Solo puntúa **domingo 10:00-12:00**, con datos nuevos (compradores, productos y modas nuevos). Leaderboard oculto
  en la final: solo luz verde/roja. Se puede tocar el código durante la final, pero a ciegas.
- Nota: 70 % stream (conversión 50, cold start 10, fiabilidad 10, normalizado) + 30 jurado (método validado offline
  10, aprender del stream 10, uso de RawTree 5, comunicación 5). Desempate: ingreso esperado por petición.
- Se puntúa la probabilidad de compra, no la compra real. Quien ya compró algo no lo quiere otra vez.
- **Contradicción oficial sobre el baseline (el 0 de la nota)**: README y código del starter = "los más vendidos de
  siempre en stock"; diapositivas de la apertura = "los más vendidos de la categoría del último producto visto".
  Preguntar en el stand. Si es lo segundo, el listón está más alto.
- Pista oficial: "los equipos que generalizan ganan a los que memorizan" (usar atributos, no solo IDs).
- Reto paralelo de RawTree: "Best agentic use of RawTree" (un agente que decide qué ingerir y preguntar). Aparcado
  hasta tener el recomendador estable.

## 6. El starter kit (en `starter/`, sin modificar)
- `recommender.py` (~130 líneas): bucle `pull → predict → push`. Cada 0,3 s pide las 50 peticiones más recientes,
  responde las no vistas y las envía en lote. Calcula los más vendidos UNA vez al arrancar (nunca se refresca) y
  comprueba roturas de stock cada segundo. `recommend()` es lo que hay que sustituir.
- **Fallos conocidos a arreglar en nuestro servicio**:
  1. El conjunto `seen` vive en memoria: si el proceso se reinicia, vuelve a contestar las últimas 50 peticiones
     (duplicados). Solución: al arrancar, leer los `request_id` ya enviados (de `submissions` o `feedback`).
  2. Si dos personas lo lanzan a la vez → duplicados en cada petición. Solo el Operador.
  3. Solo filtra `out_of_stock`, no `is_active` del catálogo ni productos retirados.
  4. Los más vendidos nunca se actualizan: no ve tendencias.
- `queries.sql`: 7 consultas (catálogo actual, popularidad última hora, tendencias 15 min vs 3 h, co-visitas,
  conversión propia, fiabilidad y latencia, clics por posición).
- `offline_eval.ipynb`: separa los últimos 7 días como test; compara `baseline` contra `coview` (co-visitas ya
  implementadas) con hit_rate@10, IPS y SNIPS del clic en la posición 1, y `slot1_matches` (si es bajo, ruido).

## 7. Estrategia acordada (la escalera; detalle en docs/ROADMAP.md)
0. Baseline vivo y estable (HECHO a las 12:27).
1. Co-visitas ("quien vio X vio/compró Y", con peso a lo reciente) + filtro de stock/activos + no repetir lo comprado.
   Inspirado en las soluciones ganadoras de OTTO (Kaggle): generar candidatos con co-visitas y luego reordenar.
2. Tendencias de la última hora (`shop.events`) + desempate por precio (desempate oficial = ingreso).
3. Cold start: usuario nuevo → popular por contexto (device, region, page); producto nuevo → vecinos por embedding
   de la descripción (y por categoría/subcategoría/marca/precio).
4. Aprender del feedback: subir lo que convierte, algo de exploración (tipo bandit).
5. Si sobra tiempo: un modelo que reordene candidatos (nadie del equipo ha entrenado modelos aún: ir con cuidado).
Cada peldaño se mide offline con el notebook (IPS) contra el baseline ANTES de ir en vivo, y se apunta en
`docs/EXPERIMENTOS.md`. `model_version` = nombre de la versión (idealmente con el hash corto del commit).

## 8. Organización del equipo (ya montada en este repo)
- `AGENTS.md`: reglas comunes. `CLAUDE.md`: lo importa + estados.
- `docs/estado/<nombre>.md`: un archivo por persona. Comandos `/empezar`, `/handoff`, `/eval`, `/check-live`,
  `/freeze` en `.claude/commands/` y `.opencode/commands/`, que siguen `docs/procedimientos/`.
- `.githooks/pre-commit`: bloquea `.env`, claves y datos (probado). Activar con `git config core.hooksPath .githooks`.
- `src/validar.py`: valida respuestas (10, distintas, activas). `python -m src.validar` pasa.
- Roles propuestos: Operador (Claude Code), Ranking (Claude Code), Evaluación (Claude Code), Datos y relato (OpenCode).
- Calendario clave: Dom 08:00 congelar, 10:00-12:00 final, tarde presentación.

## 9. Pendientes y preguntas abiertas
- Subir el repo a GitHub e invitar a los tres (sección 2).
- Repartir roles y decidir quién es el Operador y en qué portátil corre el servicio por la noche y el domingo.
- Preguntar en el stand: qué es exactamente el baseline del 0 (sección 5).
- Preguntar por Discord: enchufes y wifi estable para dejar un portátil toda la noche.
- Más adelante (no ahora): un deep research sobre la mejor estrategia para el recomendador.
- Mover el servicio en vivo a este repo (copiando `starter/recommender.py` a `src/` y arreglando los fallos de la
  sección 6) SIN dejar huecos: arrancar el nuevo y parar el viejo casi a la vez, y comprobar que no hay duplicados.

## 10. Lo que se pide al agente que lea esto
Con todo lo anterior, dile a Lester **cuál es el siguiente paso más importante ahora mismo y por qué**, en pocas
líneas y con los comandos exactos para Windows PowerShell. Luego propón los dos o tres siguientes. Respeta las reglas
de `AGENTS.md` (sobre todo: no tocar el baseline que está en vivo y no mostrar nunca claves).
