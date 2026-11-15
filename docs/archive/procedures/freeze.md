# Procedimiento: congelar (Dom 08:00). Solo lo lanza el Operador

1. `git status` limpio y `git pull --rebase`.
2. `python -m src.validar` pasa.
3. `/check-live`: en la última hora, > 98 % servido por nosotros y p95 < 2000 ms.
4. Anota en DECISIONES.md qué versión (model_version y commit) correrá en la final.
5. `git tag final` y, con ok de la persona, `git push --tags`.
6. Recordatorio: portátil enchufado, suspensión desactivada, wifi + hotspot de reserva, terminal sin cerrar.
