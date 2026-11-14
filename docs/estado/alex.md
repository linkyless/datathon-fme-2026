# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 15:50

## Haciendo ahora
- E3: `src/panel.py`, el panel en vivo con `team_29.feedback` (nuestro leaderboard en la final).

## Terminado
- E2 (primera ronda): `evaluar.py` prueba ya TODAS las combinaciones de interruptores de `ranking.PARAMS`, en paralelo
  (~7 min). Veredicto: `usar_vecinos` y `usar_contexto` **NO APTOS** de momento (números en EXPERIMENTOS). Dejarlos apagados.
- `src/catalogo.py` probado contra RawTree (lectura): 5100 productos, 5063 disponibles; el filtro `hasta` funciona.
- E1: `src/evaluar.py` (`python -m src.evaluar`). Test y Estado se guardan en `data/` (no se sube). Primera fila en EXPERIMENTOS.

## Descubierto (cosas que los demás deben saber)
- `shop.history_sessions` no tiene `is_new_user` y el contexto viene aplanado (`context.device`, `context.region`,
  `context.page`). `clicked` y `purchased` son el product_id (o vacío). En evaluar.py "frío" = sin recent_views.
- Ojo con el hit_rate: el sistema antiguo enseñaba sobre todo populares de la categoría, así que solo se pudo clicar
  lo que enseñó. Por eso baseline_categoria saca 0.76 de hit_rate pero un SNIPS más bajo que baseline_starter.

## Necesito de alguien
- **Leonardo**: 1) co-visitas primero (es lo que más puede subir). 2) En `ranking.py` los vecinos se activan "si el
  producto no tiene co-visitas"; sin co-visitas eso es SIEMPRE y hunde el hit_rate (0.76 → 0.05). Mejor "solo si el
  producto es nuevo" (no está en el historial). Cuando estén, avísame y re-mido.
- **Artai**: `populares_contexto` empeora a los fríos (purchase_hit 0.60 → 0.46). ¿Pocas compras por grupo
  (device × región)? Propuesta: mezclarlo con los populares generales en vez de sustituirlos.
- **Lester**: interruptores apagados en vivo. Si queréis probar vecinos, un A/B pequeño en vivo en la práctica: el
  offline no puede premiar productos que el sistema antiguo casi no enseñaba.
- **Leonardo**: en `src/ranking.py`, `sorted(estado.disponibles)` se calcula 2 veces en cada llamada aunque casi
  nunca se use: es el 95 % del tiempo de `recommend()` (~2 ms/petición). Con eso el evaluador tarda ~12 min.
  Si lo precalculas o lo ordenas solo cuando hace falta, baja a segundos.
