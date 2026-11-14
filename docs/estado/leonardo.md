# Estado de Leonardo

- **Rol:** Ranking (docs/guias/ranking.md)
- **Actualizado:** sáb 14/11 ~16:15

## Haciendo ahora
- Decidir con Lester un A/B en vivo v0 frente a v1 (co-visitas). Siguiente: R4 (tendencias).

## Terminado
- R1: `cargar_covisitas(hasta)` en src/covisitas.py. Con `hasta='2026-11-07'`: co-visitas para 5.000 productos,
  20 vecinos cada uno. La consulta tarda 6,3 s la primera vez; después se lee de `data/covisitas_<hasta>.json`
  (0,07 s). `recommend()` sobre 500 sesiones de history_sessions posteriores al corte: 0 inválidas, 403 usan
  co-visitas, 0,63 ms por petición. `prueba_local` y `validar` pasan.
- R2: `cargar_comprados(hasta)` en src/usuarios.py. Con `hasta='2026-11-07'`: 50.698 compradores y 413.211
  pares (usuario, producto), 4,5 s. Con `hasta=None` (historial + shop.events de 24 h): 60.438 compradores,
  3,6 s. Sobre 2.000 sesiones posteriores al corte: 1.903 son de compradores conocidos, el filtro cambia la
  lista en 559 y ninguna lista repite algo ya comprado. 0 inválidas.
- `NOMBRE` en ranking.py = `e-samen-v1` (v1 = v0 + co-visitas sin normalizar + no repetir lo comprado).
- R3 idea 1 (re-visitas): clave `revisitas` en `ranking.PARAMS`, APAGADA por defecto (0.0). Con 1.0, sobre 5.000
  sesiones posteriores al corte: 0 inválidas, la lista cambia en 3.935 y tarda 0,64 ms por petición.
- R1 corregida: co-visitas SIN dividir por popularidad (`EXPONENTE_POPULARIDAD = 0`). El archivo guardado ahora es
  `data/covisitas_<hasta>_exp0.json`.
- R3 ideas 2-4 como claves de `PARAMS`, todas apagadas: `afin_sub`, `afin_marca`, `normalizar_fila`,
  `precio_coherente`, `lambda_precio`. Probado `decaimiento` 0.5 y 0.9: casi igual que 0.7.
- Arreglos que pidió Alex en ranking.py: los vecinos solo se usan si hay co-visitas cargadas y el producto no está
  en ellas (producto nuevo); `sorted(disponibles)` solo se calcula si se llega a usar. recommend() pasa de ~2 ms
  a 0,025 ms por petición en mi chequeo.
- Chequeo orientativo (mío, NO la evaluación oficial), 3.530 compras del carrusel con ts >= 2026-11-07,
  ¿lo comprado está en nuestros 10? (CORREGIDO a las 16:15: mi primer "v0" tenía los vecinos encendidos por error)
  | Versión | top10 | IPS (1/propensity, recorte 50) |
  |---|---|---|
  | v0 (populares de la categoría, lo que está en vivo) | 31,73 % | 4,101 |
  | v1 co-visitas exponente 0 | 14,84 % | 2,180 |
  | v1 + revisitas 0.3 | 15,92 % | 2,282 |
  | v1 co-visitas exponente 0.5 | 11,47 % | 1,892 |
  | mezcla 2 huecos co-visitas + 8 v0 | 31,08 % | 3,842 |
  OJO: el historial premia parecerse al carrusel antiguo (de media 5 de sus 10 huecos eran top-10 de la categoría
  de lo último visto, y 2.959 de las 3.530 compras son de ese top-10). Offline NO se puede decidir si las
  co-visitas son mejores o peores en vivo. Hace falta un A/B en vivo v0 frente a v1.

## Descubierto (cosas que los demás deben saber)
- Corte para evaluar offline (último día de shop.history_sessions menos 7): `2026-11-07`.
- shop.history_events: 11.996.560 eventos, 2.500.000 sesiones, 5.000 productos. Una sesión tiene como mucho 9
  productos distintos y dura como mucho 954 s (mediana 114 s). Por eso el filtro "pares a menos de 24 h" sobra.
- En shop.history_sessions (carrusel antiguo), de 20.000.000 de huecos mostrados, 224.723 eran productos que el
  comprador ya había visto: 6.788 clics y 4.409 compras (1,96 compras por 100 huecos). Los otros 19.775.277:
  68.127 clics y 23.644 compras (0,12 por 100). SIN corregir por posición.
- `recent_views` puede traer el mismo producto repetido.
- `SELECT *` sobre shop.history_sessions devuelve el contexto aplanado (`context.device`, `context.page`,
  `context.region`); con `SELECT context` llega como objeto. `clicked`/`purchased` llegan como None si no pasó nada.
- **Para Evaluación**: en shop.history_events, 19.996 de 20.000 sesiones con compra (tras el corte) compran algo
  que YA habían visto en la sesión. Medir "¿lo comprado está en nuestros 10?" sobre history_events solo mide si
  repetimos lo visto (re-visitas 78 %, co-visitas 7 %): no sirve para comparar. Hay que medir sobre las compras
  del carrusel (shop.history_sessions.purchased).
- Las co-visitas se guardan en `data/` (no se sube). Para recalcularlas, borrar `data/covisitas_*.json`.
  En vivo (`hasta=None`) el archivo es `data/covisitas_todo.json`.

## Necesito de alguien
- **Evaluación (Alex):** mide `e-samen-v1` (co-visitas + no repetir lo comprado) contra el baseline con
  `hasta='2026-11-07'`. Para ver el efecto de cada pieza por separado, vacía `estado.comprados` o
  `estado.covisitas` antes de llamar a `recommend()`.
- **Evaluación (Alex):** apunta en docs/EXPERIMENTOS.md que `e-samen-v1` = v0 + co-visitas (pesos vista 1,
  carrito 6, compra 3; sin dividir por popularidad; 20 vecinos) + filtro de productos ya comprados. El archivo es tuyo, por eso no lo toco.
- **Evaluación (Alex):** mide también la candidata a v2: `recommend(p, e, {"revisitas": 0.3})` frente a la v1
  (por defecto `revisitas` = 0). Si mejora, la enciendo y subo `NOMBRE` a `e-samen-v2`.
- **Operador (Lester):** `src/prueba_local.py` da por hecho el orden de la v0 (`items[0] == "p_030"` sin piezas y
  `"p_003"` con piezas). Con re-visitas encendidas lo ya visto va primero y esas dos líneas fallan aunque la
  respuesta sea válida. Cuando se enciendan, habrá que pasar `{"revisitas": 0}` en esa prueba o cambiar lo esperado.
- **Operador (Lester):** las compras en vivo solo están tan frescas como el último refresco del Estado.
  `cargar_comprados(None)` tarda 3,6 s: conviene refrescarlo cada pocos minutos, fuera del bucle que responde.
- **Operador (Lester):** al hacer commit sale el aviso "The '.githooks/pre-commit' hook was ignored because
  it's not set as executable" (Linux). El bloqueo de claves no se está ejecutando aquí hasta hacer
  `chmod +x .githooks/pre-commit`.
- **Operador (Lester):** cuando quieras A/B en vivo: `VARIANTES = [("-A", {}), ("-B", {"revisitas": 0.3})]`.
  En vivo, `cargar_covisitas(None)` lee `data/covisitas_todo_exp0.json` si existe (no se refresca solo: se
  calcula con todo el historial la primera vez, ~7 s).
- **Operador (Lester):** para el A/B v0 frente a v1 necesito un interruptor `usar_covisitas` en `PARAMS`, pero
  `src/prueba_local.py` (línea 55) exige que con piezas la co-visita vaya primera con los PARAMS por defecto.
  Si lo pongo apagado por defecto, esa prueba falla. ¿Lo pruebas pasando `{"usar_covisitas": True}` en esa línea,
  o prefieres que quede encendido por defecto y el A/B sea `[("-A", {"usar_covisitas": False}), ("-B", {})]`?
