# Experimentos

Una fila por medición. Solo números que salgan de código ejecutado. `model_version` = lo que enviamos a submissions.

## Qué es cada versión (el nombre público es solo `e-samen-vN`: lo que hace se apunta AQUÍ, no en el nombre)
| Versión | Qué hace | Commit |
|---|---|---|
| baseline | starter oficial: más vendidos de siempre en stock | (starter) |
| e-samen-v0 | más vendidos de la categoría de lo último visto; sin vistas, los más vendidos de siempre | 10ea502 |

## Mediciones

| Hora | Quién | model_version | Qué cambia | hit_rate@10 | SNIPS slot 1 | slot1_matches | Leaderboard | Notas |
|---|---|---|---|---|---|---|---|---|
| | | baseline | starter kit oficial | | | | | |
| Sáb 14:09 | Lester | e-samen-v0-6804453 | EN VIVO desde 14:08:23 (relevo del baseline) | | | | pendiente (actualiza cada 10 min) | Primer minuto: 100 % servido por nosotros (75/75), latencia p50 1.172 ms, p95 1.899 ms (baseline: 1.238 / 2.232). 0 duplicados |
| Sáb 14:14 | Lester | e-samen-v0-2a2ee49 | Mismo ranking; servicio con 3 hilos de envío | | | | | Desde 14:14:10 (23 peticiones): latencia p50 773 ms, p95 1.091 ms, 100 % servido por nosotros. Reinicio: ~4 s de hueco, 5 perdidas, 0 duplicados |
| sáb 14:50 | Alex | baseline_starter | offline, corte 2026-11-07, 100k visitas (3722 clics, 1422 compras) | 0.4022 | 0.0512 | 4041 | | purchase_hit@10 0.5408. Frío (20272): hit 0.3605, purchase 0.6000 |
| sáb 14:50 | Alex | baseline_categoria | ídem, + más vendidos de la categoría de lo último visto | 0.7646 | 0.0237 | 21537 | | purchase_hit@10 0.8629. Frío: igual que starter (sin vistas no hay categoría) |
| sáb 14:50 | Alex | v0-categoria (actual) | ídem, Estado completo (piezas aún vacías) | 0.7646 | 0.0237 | 21537 | | Idéntico a baseline_categoria: prueba de que el evaluador es correcto. 0 respuestas inválidas |
