# Estado de Alex

- **Rol:** Evaluación (docs/guias/evaluacion.md)
- **Actualizado:** sáb 16:50

## Haciendo ahora
- E4: intervalo de confianza (bootstrap) en evaluar.py. Leer el A/B en vivo con el panel.

## Terminado
- E2 (v1): medida `e-samen-v1` y sus piezas por separado. **NO APTA**: purchase_hit 0.16 vs 0.86 de la v0.
  Culpable principal: el filtro de comprados. `evaluar.py` acepta ya variantes sueltas (`VARIANTES`).
- E3: `python -m src.panel` (`--horas`, `--minutos`): por versión y tramo, % servido, tarde, inválidas, clics,
  compras, €/petición y latencia p95. Solo lee; tarda ~1 s. Es nuestro leaderboard en la final.
- E2 (primera ronda): `evaluar.py` prueba ya TODAS las combinaciones de interruptores de `ranking.PARAMS`, en paralelo
  (~7 min). Veredicto: `usar_vecinos` y `usar_contexto` **NO APTOS** de momento (números en EXPERIMENTOS). Dejarlos apagados.
- `src/catalogo.py` probado contra RawTree (lectura): 5100 productos, 5063 disponibles; el filtro `hasta` funciona.
- E1: `src/evaluar.py` (`python -m src.evaluar`). Test y Estado se guardan en `data/` (no se sube). Primera fila en EXPERIMENTOS.

## Descubierto (cosas que los demás deben saber)
- **La gente recompra**: el 61,6 % de las compras del carrusel en el test (876 de 1422) son productos que ese
  usuario ya había comprado antes. Choca con la pista oficial ("quien compró no lo quiere otra vez"): preguntar en el stand.
- El segmento frío del test solo tiene 35 compras: el purchase_hit frío es ruido; mirar el hit_rate frío.
- **En `feedback`, "late" no aparece nunca**: lo que enviamos tarde sale como `fallback_reason = 'missing'`
  aunque esté en `submissions`. El panel lo cuenta como "tarde".
- **Fiabilidad sáb 16:00-16:30: ~10 % de lo que enviamos llega tarde** (202 de 1890 en 16:15, p95 2251 ms) → 89-91 %
  servido, justo en el umbral del 90 % de la luz verde. Para Lester.
- `shop.history_sessions` no tiene `is_new_user` y el contexto viene aplanado (`context.device`, `context.region`,
  `context.page`). `clicked` y `purchased` son el product_id (o vacío). En evaluar.py "frío" = sin recent_views.
- Ojo con el hit_rate: el sistema antiguo enseñaba sobre todo populares de la categoría, así que solo se pudo clicar
  lo que enseñó. Por eso baseline_categoria saca 0.76 de hit_rate pero un SNIPS más bajo que baseline_starter.

## Necesito de alguien
- **Leonardo**: interruptor `filtrar_comprados` en `PARAMS`, APAGADO por defecto (offline hunde purchase_hit
  0.86 → 0.32). Probar también lo contrario: empujar lo ya comprado.
- **Lester**: el brazo A del A/B (v1 sin co-visitas) NO es la v0: lleva el filtro de comprados. Propuesta:
  A = v0 real (sin co-visitas ni filtro), B = co-visitas sin filtro. Y fiabilidad: 80 % servido a las 16:30
  (392 tarde, p95 2,7 s): `python -m src.panel --horas 1`.
