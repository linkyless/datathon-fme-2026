# Procedimiento: handoff (al terminar una tarea o cada ~1 hora)

Argumento: el nombre de la persona. Si no lo tienes, pregúntalo.

1. Edita SOLO `docs/estado/<nombre>.md`:
   - "Actualizado": hora actual.
   - Mueve lo acabado a "Terminado" (una línea por cosa, con el archivo o commit).
   - En "Descubierto", lo que los demás deben saber (ej. "price_eur viene como texto: castear a Float64").
   - "Haciendo ahora" y "Necesito de alguien": actualizados.
2. Si hubo una decisión no trivial, añade una fila a `docs/DECISIONES.md`.
3. Si hubo una medición, añade una fila a `docs/EXPERIMENTOS.md` (solo números ejecutados).
4. Ejecuta `python -m src.validar`. Si falla, para y avisa.
5. `git add` de los archivos concretos que cambiaron (nunca `.env`, nunca datos). Commit en español: `estado(<nombre>): ...`.
6. Pregunta antes de hacer `git pull --rebase` y `git push`.
7. Escribe un resumen de 2 líneas para pegar en el chat del equipo.
