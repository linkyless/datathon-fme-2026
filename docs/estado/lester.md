# Estado de Lester

- **Rol:** Operador (docs/guias/operador.md)
- **Actualizado:** sáb 17:45

## Haciendo ahora
- O4: vigilar el servicio en vivo. **EN VIVO desde 17:35: `e-samen-v1-C` (1/4 del tráfico) y `e-samen-v1-CF` (3/4).**
  - C = v0: más vendidos de la categoría de lo último visto (relleno: más vendidos de siempre).
  - CF = C quitando, para cada comprador, lo que ya compró (`_filtrar_comprados` en `src/servicio.py`).
  - Ventana propia "EN VIVO e-samen-v1 (C 1/4, CF 3/4)". Log en `logs/servicio.log`.
- **Desde ahora, CONGELADO**: no se reinicia ni se cambia el modelo en vivo sin medición de Evaluación (Alex).
  Lo de esta tarde (5 cambios en vivo sin revisión) no se repite.

## Terminado
- **A/B en vivo de la tarde** (números del leaderboard, ventana 16:39-17:39; todo orientativo, < 3.000 peticiones):
  | Versión | Qué era | Peticiones | Conversión | € / pet. |
  |---|---|---|---|---|
  | **CF** | v0 sin lo ya comprado | 494 | **1,19 %** | **0,726** |
  | C | v0 | 2.873 | 1,02 % | 0,509 |
  | T | v0 + tendencia 0.5 | 464 | 0,93 % | 0,414 |
  | E | v0 + re-visitas 0.3 | 816 | 0,91 % | 0,464 |
  | D | co-visitas, sin filtro | 1.241 | 0,77 % | 0,281 |
  | MT / M | R5 (regresión) ± tendencia, sin filtro | 458 / 464 | 0,58 % / 0,47 % | 0,341 / 0,134 |
  - A/B y comparaciones siempre en la MISMA ventana (Baseline de esa hora: ~0,62 %).
- Servicio más robusto (commits `9545d78`, `32d5d57`):
  - Cada pieza del refresco tiene su propio control de errores y reintento en 60 s (antes: 30 min).
  - Piezas pesadas y `embeber_nuevos` en un hilo aparte: con mala red tardaban 90 s y congelaban el stock.
  - `SystemExit` de embeddings.py (sin clave de HelmCode) mataba el hilo del stock: ya se captura.
- Reparto A/B por `user_id` (crc32) con claves especiales del servicio: `_sin_covisitas`, `_sin_comprados`,
  `_filtrar_comprados`. No tocan `ranking.py`.
- Campo `tendencias` en el Estado (R4 de Leonardo), refrescado cada 90 s.
- HELMCODE_API_KEY en el `.env` del Operador: `embeber_nuevos` funciona (100 nuevos embebidos; vecinos de 5.200).
- O3 (14:08) relevo del baseline sin duplicados. O2 servicio. O1 catálogo.

## Descubierto (cosas que los demás deben saber)
- **Los organizadores dicen que en vivo NO hay recompras.** En el historial sí: de 3.530 compras del carrusel
  posteriores al 2026-11-07, 2.179 (62 %) eran productos que ese usuario ya había comprado antes. Por eso:
  - el offline que premia recompras (purchase_hit con lo ya comprado) mide algo que en vivo no existe;
  - R5 aprendió `ya_comprado` ×1,8 y en vivo ponía lo ya comprado 1.º en 366 de 600 peticiones → M 0,47 %.
- **C también enseña lo ya comprado**: en 186 de 600 peticiones reales (los más vendidos son lo que más gente tiene).
- 497 de 600 peticiones en vivo son de usuarios con compras en el historial: el filtro afecta a casi todos.
- R5 (sin filtro) elige productos de ~61-79 € de media frente a ~46 € de C.
- **Fiabilidad**: "On time" 83,6 % en la última hora. Causas: 8 reinicios por la tarde + cortes de red (15:51-16:04
  sin conexión; timeouts de RawTree a ratos, edad al ver la petición de hasta 4 s). Es lo que más puntos pierde ahora.
- **Causa de la fiabilidad baja = NUESTRA wifi** (medido 17:50): señal 48 % en DatathonFME2026, 10 % de paquetes
  perdidos, ping 11-471 ms (media 138). Los demás equipos tienen 98-99 % a tiempo con el mismo RawTree. Las pérdidas
  salen como `missing` (nunca `late`): a las 17:40 se perdieron 284 sin reinicio; las piezas pesadas tardaron 261 s.
- `team_29.submissions` no tiene `ts`: para filtrar por tiempo, `request_id IN (SELECT ... FROM shop.requests WHERE ts ...)`.
- Cada viaje a RawTree cuesta ~300 ms. Reloj del portátil ~8 s detrás del servidor: edades en SQL.

## Necesito de alguien
- **Alex (Evaluación):** confirmar CF > C con tu panel (`python -m src.panel --horas 1`). Y quitar del test offline
  las compras de productos ya comprados (no existen en vivo).
- **Leonardo (Ranking):** reentrenar R5 sin la señal `ya_comprado` y sin las filas de recompras; mirar el peso del
  precio. Cuando Alex lo mida, se decide si entra en vivo.
- **Lester (persona):** mejorar la red ANTES de la noche: acercarse al router/punto de acceso, hotspot del móvil o
  adaptador de cable. Opción grande a decidir: correr el servicio en una máquina en la nube (no depende de la wifi).
- **Todos:** cualquier cambio que deba ir en vivo, primero medido por Alex y apuntado en EXPERIMENTOS; después el
  Operador decide y reinicia una sola vez.
