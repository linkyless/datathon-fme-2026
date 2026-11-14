# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 16:30

## Haciendo ahora
- E4: intervalo de confianza (bootstrap) en evaluar.py. Pendiente: re-medir interruptores cuando haya co-visitas.

## Terminado
- E3: `python -m src.panel` (`--horas`, `--minutos`): por versión y tramo, % servido, tarde, inválidas, clics,
  compras, €/petición y latencia p95. Solo lee; tarda ~1 s. Es nuestro leaderboard en la final.
- E2 (primera ronda): `evaluar.py` prueba ya TODAS las combinaciones de interruptores de `ranking.PARAMS`, en paralelo
  (~7 min). Veredicto: `usar_vecinos` y `usar_contexto` **NO APTOS** de momento (números en EXPERIMENTOS). Dejarlos apagados.
- `src/catalogo.py` probado contra RawTree (lectura): 5100 productos, 5063 disponibles; el filtro `hasta` funciona.
- E1: `src/evaluar.py` (`python -m src.evaluar`). Test y Estado se guardan en `data/` (no se sube). Primera fila en EXPERIMENTOS.

## Descubierto (cosas que los demás deben saber)
- **En `feedback`, "late" no aparece nunca**: lo que enviamos tarde sale como `fallback_reason = 'missing'`
  aunque esté en `submissions`. El panel lo cuenta como "tarde".
- **Fiabilidad sáb 16:00-16:30: ~10 % de lo que enviamos llega tarde** (202 de 1890 en 16:15, p95 2251 ms) → 89-91 %
  servido, justo en el umbral del 90 % de la luz verde. Para Lester.
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
