# Decisiones

Cada decisión no trivial, en 2-3 frases que entienda un product manager. Sirve para el jurado (30 puntos).

| Hora | Quién | Decisión | Alternativas descartadas | Por qué |
|---|---|---|---|---|
| Sáb 12:00 | equipo | Un único AGENTS.md para OpenCode y Claude Code | Un archivo por herramienta | Que los cuatro agentes trabajen con las mismas reglas |
| Sáb 13:00 | Lester | Reparto por dueños de archivo + contrato fijo (`src/estado.py`, `recommend(peticion, estado)`) | Roles que tocan los mismos archivos | Cuatro personas trabajan a la vez sin pisarse; cada pieza se puede medir por separado |
| Sáb 13:00 | equipo | Todo en Python primero; C++/Rust solo si una pieza va lenta | Empezar en C++/Rust | Las librerías de datos y los agentes van mejor en Python; 3 peticiones/s no exige más velocidad |
| Sáb 13:00 | equipo | Primero el modelo simple (co-visitas) y después, encima, el grande (reordenar con un modelo) | Ir directos al modelo grande | El grande necesita los candidatos del simple y el mismo evaluador: nada se tira |
| Sáb 13:45 | equipo | Estrategia según docs/investigacion.md: candidatos de sesión (re-visitas + co-visitas por tipo y recencia + subcategoría/marca), evaluador calibrado con el leaderboard, y solo después regresión logística → LightGBM y Thompson sobre pocas variantes | Ir directos a gradient boosting o a redes neuronales | Lo simple por vecinos compite en sesiones cortas (Ludewig/Jannach 2020, OTTO); los equipos que fueron directos a GBM van por debajo del baseline |
| Sáb 14:08 | Lester | Relevo: el baseline del starter se para y entra src/servicio.py con e-samen-v0, sin esperar a la medición offline | Esperar al evaluador de Alex | Es práctica (no puntúa), la idea ya está probada en vivo por otro equipo (+20 %) y así medimos con compradores reales; volver atrás cuesta 10 s |
