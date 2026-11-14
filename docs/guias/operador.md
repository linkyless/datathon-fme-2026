# Guía del Operador (Lester · Claude Code)

**Tu misión:** que siempre haya UN programa respondiendo, que nunca envíe nada inválido y que lo nuevo entre en
vivo sin huecos ni duplicados. Eres el único que lanza el servicio y el que decide qué versión va a producción.

**Tus archivos:** `src/rawtree.py`, `src/catalogo.py`, `src/estado.py`, `src/servicio.py`, `src/prueba_local.py`.
**Regla de oro:** el baseline (su carpeta aparte, ver `CLAUDE.local.md`) sigue en marcha hasta la tarea O3. No lo toques.

**Con quién te cruzas:**
- **Alex** te dice qué versión es "apta". Sin su medición, nada entra en vivo.
- **Leonardo y Artai** te piden campos nuevos en el `Estado` o llamadas nuevas en el servicio, por su archivo de estado.
- Tu O1 (catálogo probado) desbloquea a Alex: hazla primero.

---

## O1 · Preparar este repo y probar el catálogo — hasta las 13:45
En una PowerShell NUEVA (no la del baseline):
```powershell
cd <carpeta de este repo>
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item <carpeta del baseline>\.env .env      # copia la clave sin mostrarla
python -m src.prueba_local
```
Pega esto a tu agente:
> Lee docs/guias/README.md y src/catalogo.py. Ejecuta (sin enseñarme nunca el .env):
> `python -c "from src.estado import construir; e = construir(); print(len(e.catalogo), len(e.disponibles), e.populares[:10])"`.
> Si falla, arregla SOLO src/catalogo.py (consulta DESCRIBE si una columna no existe). Luego compara los 10
> primeros populares disponibles con lo que está enviando el baseline ahora: la última fila de
> team_29.submissions. Dime si coinciden.

**Hecho cuando:** imprime ~5.000 productos, la mayoría disponibles, y los 10 coinciden con lo que envía el baseline.
Haz commit (`catalogo: probado contra RawTree`) y push: Evaluación lo necesita.

## O2 · El servicio nuevo, en modo "sin envío" — hasta las 15:00
Pega esto a tu agente (usa plan mode, Shift+Tab):
> Crea src/servicio.py a partir de starter/recommender.py (no edites starter/). Requisitos:
> 1. Pide `request_id, ts, user_id, is_new_user, context, recent_views` de shop.requests (DESCRIBE primero).
> 2. Llama a `src.ranking.recommend(peticion, estado)` para cada petición nueva.
> 3. Refresca el Estado por piezas, en un hilo aparte, sin bloquear nunca el bucle: agotados cada 1 s,
>    catálogo cada 60 s, populares cada 10 min, covisitas/comprados/populares_contexto/vecinos al arrancar y
>    cada 30 min. Si un refresco falla, se queda con el valor anterior y lo escribe en el log.
> 4. Antes de enviar, valida cada respuesta con `src.validar.errores_respuesta(items, estado.disponibles)`. Si es
>    inválida o recommend() lanza un error, envía en su lugar los 10 populares disponibles. Nunca se cae.
> 5. Al arrancar, mete en `seen` los request_id ya enviados: los de team_29.submissions cuyo request_id esté en
>    shop.requests de los últimos 10 minutos (ojo: `request_id::String IN (SELECT request_id::String ...)`, sin cast falla). Así reiniciar no duplica.
> 6. `model_version` = `ranking.NOMBRE + "-" + hash corto del commit`.
> 7. Opción `--sin-envio`: hace todo menos el push; imprime cuántas respuestas enviaría y la latencia.
> 8. Cada 10 s imprime una línea: respondidas, inválidas sustituidas y latencia p95 en ms.
> Pruébalo SOLO con `python -m src.servicio --sin-envio` durante 2 minutos. No lo lances sin esa opción.

**Hecho cuando:** `--sin-envio` corre 5 minutos sin errores, 0 inválidas y p95 < 1.000 ms. Commit y push.
Leer de RawTree desde dos sitios no pasa nada; lo único prohibido es que dos programas ENVÍEN.

## O3 · El relevo: del baseline al servicio nuevo — 15:30
Solo si Evaluación ha medido la versión actual de `ranking.py` y no es peor que el baseline.
1. Deja `python -m src.servicio --sin-envio` corriendo 2 minutos más. Sin errores → sigue.
2. Páralo. En la terminal del baseline pulsa **Ctrl+C** e, inmediatamente, en la otra:
   `python -m src.servicio`
3. A los 2 minutos, pide a tu agente:
   > Comprueba en team_29.submissions que no hay request_id duplicados
   > (`SELECT count() - uniqExact(request_id) FROM team_29.submissions`) y ejecuta /check-live.

**Hecho cuando:** 0 duplicados nuevos y casi todo servido por nosotros. Apunta en `docs/DECISIONES.md` la hora y
la versión que ha entrado. Si algo va mal: Ctrl+C y relanza el baseline en su carpeta (`python starter/recommender.py`).

## O4 · Vigilar y meter versiones nuevas — toda la tarde y la noche
- `/check-live` cada 30-60 minutos. Alarma si p95 > 2.000 ms o aparece `invalid`.
- Versión nueva = Evaluación la ha medido + tú das el ok → `git pull --rebase`, `python -m src.prueba_local`,
  Ctrl+C y relanzar (el punto 5 de O2 evita duplicados).
- Portátil: enchufado, suspensión en "Nunca", terminal abierta. Decide los turnos de noche.
- Atiende las peticiones de campos nuevos en el `Estado` (ver README del reparto).

## O5 · Domingo
08:00 `/freeze` y `git tag final`. 10:00-12:00, la final: `/check-live` cada 15 minutos, cambios solo si algo se rompe.

---

## Fase 2 (tras el deep research)
_Vacío. Se rellenará con las conclusiones del deep research. No empezar nada de aquí hasta que esté escrito._
