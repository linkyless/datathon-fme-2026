# Estado de Lester

- **Rol:** Operador (docs/guias/operador.md)
- **Actualizado:** sáb 14:10

## Haciendo ahora
- O4: vigilar el servicio en vivo (e-samen-v0-2a2ee49, 3 hilos de envío desde 14:13:39).

## Terminado
- O3: relevo a las 14:08:23. EN VIVO `e-samen-v0-6804453` (`python -m src.servicio`, en su propia ventana). 0 duplicados. El baseline del starter está PARADO.
- O2: `src/servicio.py` hecho. Prueba `--sin-envio` 60 s: 186 respondidas (~3,1/s), 0 sustituidas, 0 fallos de sondeo; edad de las peticiones al verlas p50 ≈ 300-420 ms, p95 ≈ 0,46-1,2 s (tras el primer arranque).
- O1: `src/catalogo.py` probado contra RawTree. 5.100 productos, 5.064 disponibles, 36 agotados/inactivos. Los 10 populares disponibles coinciden EXACTAMENTE con lo que envía el baseline en vivo. `construir()` tarda ~3,4 s. `v0-categoria` sobre 200 peticiones reales: 0,8 ms/petición, 0 inválidas, 158 de 200 distintas del baseline.
- Clave de RawTree recogida (team_29) y probada.
- Baseline oficial EN VIVO desde las 12:27 en su carpeta aparte. No pararlo.
- Repo subido a GitHub  e invitaciones enviadas.
- Reparto en docs/guias/ y esqueleto de src/ (contrato en src/estado.py).

## Descubierto (cosas que los demás deben saber)
- Cada viaje a RawTree cuesta ~300 ms (incluso `SELECT 1`). La consulta del starter sin filtro de tiempo llegó a tardar 10 s: filtrad SIEMPRE por `ts`.
- El reloj del portátil iba ~8 s por detrás del servidor: las edades se calculan en SQL (`dateDiff('millisecond', ts::DateTime64(3), now64(3))`).
- Baseline en vivo (13:12-13:42): 99,6 % servido por nosotros, latencia p50 1.238 ms y p95 2.232 ms (cerca del límite de 3 s).
- En `IN (SELECT ...)` hay que castear las dos partes: `request_id::String IN (SELECT request_id::String ...)`. Sin cast: "Illegal type Dynamic".
- De las últimas 200 peticiones en vivo: 68 con is_new_user = true (34 %) y 42 sin recent_views (21 %). El cold start pesa mucho.
- `recent_views` va de más antiguo a más reciente (lo último visto es el último elemento). Los `ts` están en UTC.
- `context` llega como objeto: `{"device": "web", "page": "product", "region": "Catalunya"}`.
- En Windows, `python` puede abrir el Python de MSYS2 (sin pip): usar `py -m venv .venv` y luego `python -m pip`.
- En PowerShell las llamadas con JSON fallan por las comillas: usar un archivo `body.json` con `curl.exe --data-binary "@body.json"`.
- Todo el contexto de lo hecho hasta ahora está en `docs/CONTEXTO.md`.

## Necesito de alguien
- Aceptad la invitación de GitHub (llega al correo) y seguid la Parte 2 de docs/GUIA.md.
