# Lo que enseña el leaderboard de práctica

Fuente: https://datathon.rawtree.dev/leaderboard. Añadir una foto nueva cada pocas horas (hora + números tal cual).

## Foto sáb 13:16 (ventana 12:16–13:16, solo práctica)
| Fila | Conversión | Cold start | A tiempo | € / petición | model_version |
|---|---|---|---|---|---|
| **Reference** (organizadores) | **2.0882 %** | 1.7941 % | | **€0.930** | |
| team_24 (1º) | 0.8495 % | 1.3892 % | 94.7 % | €0.323 | v2-HYDRA-explora |
| team_09 | 0.8024 % | 1.2822 % | 37.6 % | €0.276 | v1\|aa=0 |
| team_20 | 0.7885 % | 1.3368 % | 99.7 % | €0.260 | category-v1 |
| team_19 | 0.7886 % | 1.3064 % | 99.7 % | €0.242 | control |
| team_26 | 0.7224 % | 1.2369 % | 61.0 % | €0.214 | v1-gbm-cand |
| team_23 | 0.6857 % | 1.1764 % | 60.9 % | €0.190 | v1-coview-trend |
| **team_29 (nosotros)** | 0.6720 % | 1.1808 % | **84.1 %** | €0.151 | baseline |
| equipos con el starter | ≈ 0.666 % | ≈ 1.167 % | 13–99 % | €0.149 | baseline |
| team_21 | 0.6626 % | 1.1146 % | 81.0 % | €0.189 | ab1-baseline |
| team_05 | 0.5884 % | 0.9717 % | 78.4 % | €0.181 | gbdt-v2-decay |
| **Baseline** (el 0) | 0.6594 % | 1.1499 % | | €0.147 | |

## Conclusiones (se revisan con cada foto)
1. **El 0 parece ser el starter** ("más vendidos de siempre"): los equipos que corren el starter sacan ≈ lo mismo
   que la fila Baseline. Aun así, medimos contra los dos baselines hasta confirmarlo en el stand.
2. **"Más vendidos por categoría" ya gana un +20 %** (category-v1: 0.79 % frente a 0.66 %). Nuestro `ranking.py`
   ya hace eso como relleno: es la primera mejora que debe entrar en vivo.
3. **Hay muchísimo margen**: el primero va por 0.85 % y la referencia de los organizadores por 2.09 % (3× el
   baseline). El oráculo (el 100) está aún más arriba.
4. **La referencia ingresa 6× más por petición con 3× más conversión**: enseña productos más caros (≈ €45 por compra
   frente a ≈ €22 del baseline). Pista: el precio NO parece penalizar mucho, y el ingreso es el desempate.
5. **Ir directos al modelo grande sale mal**: gbdt-v2-decay está POR DEBAJO del baseline y v1-gbm-cand responde
   a tiempo solo el 61 %. Lo simple y fiable va primero.
6. **Otros equipos ya hacen A/B en vivo** (control, ab1-baseline, aa=0 = "prueba A/A"): reparten las peticiones
   entre dos versiones para compararlas con datos reales. Lo haremos (fase 2, "aprender del stream").
7. **Nuestro 84 % a tiempo** es porque el baseline arrancó a las 12:27 y la ventana empieza a las 12:16 (11 minutos
   sin responder). En la próxima hora debería salir ~99 %. Si no, hay un problema.

## Foto sáb 14:11 (ventana 13:11–14:11)
| Fila | Conversión | Cold start | A tiempo | € / petición | model_version |
|---|---|---|---|---|---|
| Reference | 2.0389 % | 1.9080 % | | €0.940 | |
| team_06 (1º) | 1.2741 % | 1.8075 % | 75.4 % | €0.525 | v4a |
| team_09 | 1.1372 % | 1.5413 % | 99.7 % | €0.445 | aneto |
| team_26 | 0.9927 % | 1.6420 % | 76.5 % | €0.450 | v1-gbm-cand |
| team_20 | 0.9472 % | 1.6115 % | 99.9 % | €0.403 | subcategory-v2 |
| team_05 | 0.9373 % | 1.5042 % | 73.2 % | €0.289 | super-markov-bros |
| team_29 (nosotros) | 0.7218 % | 1.2812 % | 91.4 % | €0.130 | e-samen-v0-6804453 (solo 3 min de v0: el resto era el baseline) |
| Baseline | 0.6979 % | 1.2216 % | | €0.109 | |

- El nivel sube rápido: el 1º pasó de 0,85 % a 1,27 % en una hora. La subcategoría (subcategory-v2: 0,95 %) gana a la categoría (0,79 %), como predecía docs/investigacion.md.
- El baseline varía entre horas (0,659 % → 0,698 %): comparad siempre dentro de la MISMA ventana.
- Nuestro 91,4 % a tiempo: el baseline del starter perdió 565 peticiones entre 13:45 y 13:55 y 315 entre 14:00 y 14:08 (consultas colgadas o wifi). Desde el relevo, 0 perdidas fuera de los reinicios.