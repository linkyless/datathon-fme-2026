# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 14:50

## Haciendo ahora
- E2: medir cada versión que pase Ranking (`python -m src.evaluar --recalcular`).

## Terminado
- `src/catalogo.py` probado contra RawTree (lectura): 5100 productos, 5063 disponibles; el filtro `hasta` funciona.
- E1: `src/evaluar.py` (`python -m src.evaluar`). Test y Estado se guardan en `data/` (no se sube). Primera fila en EXPERIMENTOS.

## Descubierto (cosas que los demás deben saber)
- `shop.history_sessions` no tiene `is_new_user` y el contexto viene aplanado (`context.device`, `context.region`,
  `context.page`). `clicked` y `purchased` son el product_id (o vacío). En evaluar.py "frío" = sin recent_views.
- Ojo con el hit_rate: el sistema antiguo enseñaba sobre todo populares de la categoría, así que solo se pudo clicar
  lo que enseñó. Por eso baseline_categoria saca 0.76 de hit_rate pero un SNIPS más bajo que baseline_starter.

## Necesito de alguien
- **Leonardo**: en `src/ranking.py`, `sorted(estado.disponibles)` se calcula 2 veces en cada llamada aunque casi
  nunca se use: es el 95 % del tiempo de `recommend()` (~2 ms/petición). Con eso el evaluador tarda ~12 min.
  Si lo precalculas o lo ordenas solo cuando hace falta, baja a segundos.
