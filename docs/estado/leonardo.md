# Estado de Leonardo

- **Rol:** Ranking (docs/guias/ranking.md)
- **Actualizado:** sáb 14/11 (tras R1)

## Haciendo ahora
- R2 de docs/guias/ranking.md: `cargar_comprados()` en src/usuarios.py.

## Terminado
- R1: `cargar_covisitas(hasta)` en src/covisitas.py. Con `hasta='2026-11-07'`: co-visitas para 5.000 productos,
  20 vecinos cada uno. La consulta tarda 6,3 s la primera vez; después se lee de `data/covisitas_<hasta>.json`
  (0,07 s). `recommend()` sobre 500 sesiones de history_sessions posteriores al corte: 0 inválidas, 403 usan
  co-visitas, 0,63 ms por petición. `prueba_local` y `validar` pasan.
- `NOMBRE` en ranking.py = `e-samen-v1` (v1 = v0 + co-visitas).

## Descubierto (cosas que los demás deben saber)
- Corte para evaluar offline (último día de shop.history_sessions menos 7): `2026-11-07`.
- shop.history_events: 11.996.560 eventos, 2.500.000 sesiones, 5.000 productos. Una sesión tiene como mucho 9
  productos distintos y dura como mucho 954 s (mediana 114 s). Por eso el filtro "pares a menos de 24 h" sobra.
- Las co-visitas se guardan en `data/` (no se sube). Para recalcularlas, borrar `data/covisitas_*.json`.
  En vivo (`hasta=None`) el archivo es `data/covisitas_todo.json`.

## Necesito de alguien
- **Evaluación (Alex):** mide `e-samen-v1` (co-visitas) contra el baseline con `hasta='2026-11-07'`.
- **Evaluación (Alex):** apunta en docs/EXPERIMENTOS.md que `e-samen-v1` = v0 + co-visitas (pesos vista 1,
  carrito 6, compra 3; dividido por popularidad^0.5; 20 vecinos). El archivo es tuyo, por eso no lo toco.
