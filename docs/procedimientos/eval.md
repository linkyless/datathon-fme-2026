# Procedimiento: evaluar una idea offline

1. La idea tiene que ser una función `policy(row) -> lista de 10 product_id`, igual que en `starter/offline_eval.ipynb`.
2. Usa el MISMO corte de tiempo que el notebook: se construye con datos anteriores a los últimos 7 días y se evalúa en esos 7 días.
3. Compara SIEMPRE contra `baseline` en la misma muestra. Reporta hit_rate@10, SNIPS_ctr_slot1 y slot1_matches
   (si slot1_matches es pequeño, el SNIPS es ruidoso: dilo).
4. Añade una fila a `docs/EXPERIMENTOS.md` con los números tal cual salen.
5. Explica en 2 frases si merece la pena llevarla a producción y por qué.
