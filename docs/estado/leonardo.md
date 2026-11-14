# Estado de Leonardo

- **Rol:** Ranking (docs/guias/ranking.md)
- **Actualizado:** sáb 14/11 ~18:30

## Haciendo ahora
- R5 reentrenada SIN recompras (lo que pidieron Lester y Alex). Espero la medición de Alex para decidir R6.

## Terminado
- R5 v2 (sin recompras): quitada la señal `ya_comprado`; al entrenar se descartan las visitas cuya compra era
  algo que el comprador ya tenía (3.715 de 6.545; quedan 2.830 compras) y las filas de productos que ya tenía.
  En recommend(), lo ya comprado ya no es candidato y con `filtrar_comprados` (True) se quita.
  Coeficientes nuevos (×odds por 1 desviación típica): popularidad 4,70 · misma_categoria 2,38 · covisita 1,21 ·
  frio×popularidad 1,19 · revisita 1,16 · log_precio 1,16 · móvil×log_precio 0,89 · distancia_precio 1,07 ·
  misma_marca 1,05 · misma_subcategoria 1,01 · popularidad_categoria 0,46 (va junto con popularidad).
  Precio (lo que pidió Lester): cada desviación típica más de log(precio) multiplica las odds por 1,16 en web y
  por ~1,03 en móvil (1,16 × 0,89): el modelo prefiere algo más caro, sobre todo en web. Por eso elige más caro que C.
  AUC del modelo en visitas posteriores al corte (sin recompras, 542 compras): 0,830.
  Comprobado: 3.000 peticiones con `usar_modelo`: 0 inválidas, 0 listas con algo ya comprado, 0,175 ms/petición.
- R5 v1 (sustituida por la v2) regresión logística: `python -m src.puntuacion` (numpy, sin dependencias nuevas; coeficientes en
  data/puntuacion.json, que no se sube). Entrena con las visitas al carrusel del 2026-10-24 al 2026-11-07 (todas
  las 6.545 con compra + 1 de cada 10 sin compra, 52.696 visitas), piezas calculadas solo con datos anteriores al
  24/10, filas pesadas por 1/propensity (recorte 50) y señal de puesto para quitar el sesgo de posición.
  Coeficientes (por 1 desviación típica; ×odds = cuánto se multiplica la probabilidad relativa de compra):
  popularidad 3,72 · ya_comprado 1,83 · misma_categoria 1,70 · frio×popularidad 1,46 · covisita 1,19 ·
  revisita 1,14 · log_precio 1,12 · misma_subcategoria 1,02 · misma_marca 1,03 · popularidad_categoria 0,54
  (va junto con popularidad: casi la misma información). Puestos: el 2º 0,66, el 5º 0,44, el 10º 0,34 frente al 1º.
  Chequeo mío en el test (100.000 visitas posteriores al corte, 1.467 compras): AUC 0,910 sin puestos.
  Reordenando los 10 que se enseñaron en cada visita con compra, el comprado queda 1º en el 48,5 % con el modelo,
  40,9 % con los populares de la categoría, 33,1 % con el orden del carrusel antiguo (azar 10 %).
  Confirma lo de la recompra: haberlo comprado antes multiplica las odds por 1,83 (el filtro de R2 va al revés).
- R4 tendencias: `cargar_tendencias(hasta)` en src/tendencias.py -> `{product_id: lift}`. Lift = actividad de
  los últimos 30 min (vista 1, carrito 6, compra 3) / ritmo normal de las 3 h anteriores, con prior 10 y
  recortado a [0.5, 3]. En vivo: 5.144 productos, 0,74 s; offline (`hasta='2026-11-07'`): 4.722, 0,30 s.
  En vivo hay más movimiento que en el historial (p90 del lift 1,38 frente a 1,14).
  ¿La tendencia dura? (chequeo mío en vivo) los productos con lift >= 1.3 tienen la media hora SIGUIENTE 1,14×
  su ritmo normal (mediana; media 1,32); los de lift < 0.8, 0,84× (media 0,91). Sí se mantiene.
  En ranking.py: clave `tendencia` (η de lift^η) en PARAMS, APAGADA (0.0). Multiplica los puntos de co-visitas y
  reordena los 30 primeros de los populares de la categoría y de los populares de siempre (llega también a la
  v0 y a los fríos). Sobre 5.000 peticiones del test con tendencias en vivo: 0 inválidas, 0,04 ms/petición;
  con η=0.3 cambia el orden de todas las listas pero nunca el 1º; con η=0.5 cambia el 1º en 383 de 5.000.
  Offline no se puede medir bien (en el historial apenas hay modas): medir con A/B en vivo.
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
- **Evaluación (Alex):** mide R5 v2: `{"usar_modelo": True}` (filtrar_comprados queda en True). Antes hay que
  regenerar `data/puntuacion.json` con `python -m src.puntuacion` (~15 s): el archivo viejo ya no vale (tenía la
  señal `ya_comprado`) y, si no se regenera, `usar_modelo` no hace nada y avisa en consola. Candidatos: los 40
  primeros de populares de su categoría, populares de siempre, co-visitas y lo visto, sin lo ya comprado.
  Según tu veredicto, sigo con R6 (LightGBM) o no.
- **Operador (Lester):** hecho lo que pediste (R5 sin `ya_comprado` ni recompras; precio arriba en "Terminado").
  Si Alex da el ok y entra en vivo: en tu máquina `python -m src.puntuacion` ANTES de reiniciar (el
  data/puntuacion.json viejo se ignora a propósito). `filtrar_comprados` sigue en True por defecto, como CF.
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
