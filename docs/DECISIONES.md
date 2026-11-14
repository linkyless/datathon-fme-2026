# Decisiones

Cada decisión no trivial, en 2-3 frases que entienda un product manager. Sirve para el jurado (30 puntos).

| Hora | Quién | Decisión | Alternativas descartadas | Por qué |
|---|---|---|---|---|
| Sáb 12:00 | equipo | Un único AGENTS.md para OpenCode y Claude Code | Un archivo por herramienta | Que los cuatro agentes trabajen con las mismas reglas |
| Sáb 13:00 | Lester | Reparto por dueños de archivo + contrato fijo (`src/estado.py`, `recommend(peticion, estado)`) | Roles que tocan los mismos archivos | Cuatro personas trabajan a la vez sin pisarse; cada pieza se puede medir por separado |
| Sáb 13:00 | equipo | Todo en Python primero; C++/Rust solo si una pieza va lenta | Empezar en C++/Rust | Las librerías de datos y los agentes van mejor en Python; 3 peticiones/s no exige más velocidad |
| Sáb 13:00 | equipo | Primero el modelo simple (co-visitas) y después, encima, el grande (reordenar con un modelo) | Ir directos al modelo grande | El grande necesita los candidatos del simple y el mismo evaluador: nada se tira |
