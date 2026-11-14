# ROADMAP

## Roles (rellenar el nombre en los primeros 5 minutos)
| Rol | Persona | Herramienta | Qué hace |
|---|---|---|---|
| **Operador** | _?_ | Claude Code | Único que ejecuta el servicio en vivo. Robustez, fallback, monitor, portátil enchufado. |
| **Ranking** | _?_ | Claude Code | La función `recommend()`: co-visitas, tendencias, filtros, orden final. |
| **Evaluación** | _?_ | Claude Code | Evaluación offline (IPS) de cada idea antes de ir en vivo. Registro en EXPERIMENTOS. |
| **Datos y relato** | _?_ | OpenCode (GLM) | Exploración SQL, embeddings del catálogo, cold start, y slides/demo desde el sábado. |

Regla: una idea solo va a producción si **Evaluación** la ha medido offline contra el baseline y el **Operador** dice ok.

## La escalera del recomendador (subir un peldaño cada vez)
| Nivel | Qué | Por qué suma |
|---|---|---|
| 0 | Baseline oficial en vivo, sin caerse | Fiabilidad (10 pts) y estar en el leaderboard |
| 1 | Co-visitas "quien vio X vio/compró Y" + filtro de stock + no repetir lo ya comprado | Conversión (50 pts). El notebook oficial ya lo trae hecho |
| 2 | Tendencias de la última hora + desempate por precio | Aprender del stream + desempate por ingreso |
| 3 | Cold start: usuario nuevo → popular por contexto (device/region/page); producto nuevo → vecinos por embedding | Cold start (10 pts) + uso de RawTree |
| 4 | Aprender del feedback: subir lo que convierte, algo de exploración | Aprender del stream (10 pts jurado) |
| 5 | (Si sobra tiempo) Modelo que reordena candidatos | Conversión |

## Calendario
| Cuándo | Qué | Quién |
|---|---|---|
| Sáb 12:00-13:00 | Repo clonado por los 4, OpenCode/Claude conectados, `.env`, hooks. Baseline EN VIVO | Todos / Operador |
| Sáb 13:00-15:00 | Nivel 1 offline (notebook) · servicio robusto (sin duplicados al reiniciar) · exploración de datos · embeddings del catálogo | Ranking · Operador · Evaluación · Datos |
| Sáb 15:00-16:00 | Nivel 1 EN VIVO. Mirar leaderboard | Operador |
| Sáb 16:00-20:00 | Niveles 2 y 3, cada uno medido offline antes de subirlo. Primeras slides | Todos |
| Sáb 20:00-00:00 | Nivel 4. Panel propio de conversión con `feedback` (será nuestro leaderboard en la final) | Ranking · Evaluación |
| Noche | El servicio corre solo como prueba de estabilidad. Turnos de sueño | Operador decide |
| Dom 06:00-08:00 | Últimos ajustes | Todos |
| **Dom 08:00** | `/freeze`: nada grande desde aquí. `git tag final` | Operador |
| Dom 08:00-10:00 | Solo estabilidad. Ensayar la presentación | Todos |
| **Dom 10:00-12:00** | **RONDA FINAL**. Vigilar luz verde y `/check-live` cada 15 min | Operador |
| Dom 12:00-14:00 | Cerrar slides con números reales | Datos y relato |
| Dom 14:00-17:00 | Presentación y demo | Todos |
