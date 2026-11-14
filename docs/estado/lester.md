# Estado de Lester

- **Rol:** Operador (docs/guias/operador.md)
- **Actualizado:** sáb 22:40

## Haciendo ahora
- **EN VIVO desde 21:43: A/B `e-samen-v1-AP3` (1/2) frente a `e-samen-v1-EXP1`/`-EXP1x` (1/2)** (`python -m src.servicio --ap3`).
  AP3 = aprendiz de Alex reentrenado (`python -m src.aprendiz --nombre ap3` → `data/aprendiz_ap3.json`).
  EXP1 = AP3 con exploración; la mitad que explora sale como -EXP1x. Coste de explorar: EXP1x frente a AP3.
- Antes, AP2 frente a C (20:47-21:43): AP2 gana (Alex: compras reales 2,03 % frente a 0,83 %, IC [+0,56, +1,87]).
- Lectura del A/B a las 22:30 (API, 21:29-22:29): AP3 1,75 · EXP1 1,84 · **EXP1x 1,49**. Explorar cuesta ~0,3 puntos
  en las peticiones que exploran (el modelo estimaba 0,08). Como explora 1 de cada 4 peticiones nuestras, el equipo
  pierde ~0,07. Para la final: bajar `EXPLORAR_FRACCION` o no explorar (decide Alex con el informe de exploración).
- Pendiente: decidir lo de la nube (Railway, rama `produccion`).

## Plan del equipo para esta noche (acordado sáb 18:05) — objetivo: pasar del ~1,2 % a > 2 %
Medido (3.530 compras del carrusel tras el 2026-11-07, comparadas con lo último visto):
- 97,6 % son de la MISMA CATEGORÍA que lo último visto → la categoría ya la acertamos.
- Dentro de la categoría: misma subcategoría 34 % (azar ~11 %), misma marca 22 %, y **precio ×0,55** (mediana)
  frente a lo visto y ×0,53 frente al precio habitual del comprador → se compra lo MÁS BARATO de lo afín.
- R5 aprendió "caro = mejor" (sesgo del carrusel antiguo) y `ya_comprado` ×1,8 (en vivo no existe): por eso falló.
Reparto (nada va en vivo sin la medición de Alex):
| Persona | Tarea | Para |
|---|---|---|
| Artai | Señales en SQL (RawTree) → Estado: perfil por comprador (top categorías/subcategorías/marcas, precio mediano; history_events + shop.events) y por producto la tasa de compra cuando se enseñaba (history_sessions, arrayJoin de shown/propensity, corregida por propensity). `cargar_perfiles(hasta)` en coldstart.py | ~19:30 |
| Leonardo | R6 = LightGBM (árbol del deep research): señales de R5 SIN `ya_comprado` + precio/precio de lo visto + precio/precio habitual + señales de Artai. Entrenar sin filas de recompras | ~20:30 |
| Alex | Test offline sin recompras; medir R6 contra CF (la puerta para ir en vivo) | al llegar R6 |
| Lester | Red estable (hotspot/cable); servicio listo para cargar R6 rápido; probarlo en `--sin-envio` | ~21:00 |

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
- **¿Queda información en el historial que AP3 no use? Poca** (sáb 22:35, fila en EXPERIMENTOS; 22 señales probadas
  sobre las 101.795 respuestas del día, validación por tiempo):
  - Lo único nuevo: **cuántas veces el carrusel antiguo ya le enseñó ese producto a ese comprador**
    (`shop.history_sessions`, `arrayJoin(shown)`). Con 10 o más veces y sin compra, se compra la mitad de lo que AP3
    espera (real / predicho 0,68 frente a 1,3-1,9 con menos). Mejora el error un 0,91 % [+0,38, +1,39].
  - Afinidad por COMPRAS (parte de sus compras del historial en esa categoría): +0,32 % [+0,12, +0,55]. Sale gratis de
    `historial["compras"]`, que el aprendiz ya tiene en memoria.
  - No aportan nada encima de AP3: tasa del carrusel antiguo (`tasa_ips`, lo que más pesaba en el árbol), co-visitas
    (con su historial, la sesión o el carrito de hoy), precio relativo, marca, antigüedad del carrito, clic antiguo.
  - Ojo: todo esto solo vale para compradores con historial. En la final llegan compradores nuevos.
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
- **Alex (Evaluación):** dos señales candidatas para `aprendiz.senales` (medidas offline, fila sáb 22:35), si las
  quieres en AP4: (1) `ensenado_antes` = log(1 + veces que el carrusel antiguo le enseñó ese producto); hay que bajar
  los pares (comprador, producto) con 5 o más veces a `historial.pkl`; (2) afinidad por compras, con lo que ya hay en
  `historial["compras"]`. Juntas con otras 4 menores: +1,66 % [+0,82, +2,51] de error; el acierto del 1.º no se mueve.
  Y la exploración al 50 % cuesta más de lo estimado (arriba).
- **Alex (Evaluación):** confirmar CF > C con tu panel (`python -m src.panel --horas 1`). Y quitar del test offline
  las compras de productos ya comprados (no existen en vivo).
- **Leonardo (Ranking):** reentrenar R5 sin la señal `ya_comprado` y sin las filas de recompras; mirar el peso del
  precio. Cuando Alex lo mida, se decide si entra en vivo.
- **Lester (persona):** mejorar la red ANTES de la noche: acercarse al router/punto de acceso, hotspot del móvil o
  adaptador de cable. Opción grande a decidir: correr el servicio en una máquina en la nube (no depende de la wifi).
- **Todos:** cualquier cambio que deba ir en vivo, primero medido por Alex y apuntado en EXPERIMENTOS; después el
  Operador decide y reinicia una sola vez.
