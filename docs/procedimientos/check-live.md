# Procedimiento: revisar el servicio en vivo

Usa las consultas 5 y 6 de `starter/queries.sql` (cambia team_NN por el TEAM_ID) sobre los últimos 30 minutos:
1. % servido por nosotros vs fallback, y motivo del fallback (missing / late / invalid).
2. Latencia p50 y p95 (alarma si p95 > 2000 ms).
3. CTR y conversión por hora, nuestro vs fallback.
Resume en 5 líneas con los números y di si hay que actuar. Si hay "invalid", busca qué producto agotado se coló.
