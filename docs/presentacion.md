# Presentación (borrador para el jurado · demo sábado tarde)

**Revisión con el equipo: punto de control de las 21:00.** Regla: aquí solo entran números medidos
(es decir, `docs/EXPERIMENTOS.md` o consultas ejecutadas); si algo no está medido, se escribe "no medido aún".
Estado de este borrador: sáb ~14:35 BCN (los datos del stream van ~1 h por detrás de lo que ves; ts en UTC).

---

## 1. El problema en una frase

Una tienda online decide, para cada comprador que llega (~3 por segundo, día y noche), los 10 productos que
le enseña… en menos de 3 segundos — el 70 % de la nota depende de acertar esa fila, incluidos los compradores
y productos que acaban de aparecer.

## 2. Nuestra escalera: subir un peldaño cada vez, midiendo antes de subir

```
Nivel 0  Baseline en vivo, sin caerse                  ← FIABILIDAD (10 pts)
Nivel 1  Co-visitas: "quien vio X compró Y" + stock    ← CONVERSIÓN (50 pts)
Nivel 2  Tendencias de la última hora + desempate por precio
Nivel 3  Cold start: comprador nuevo por contexto · producto nuevo por embedding   ← COLD START (10 pts)
Nivel 4  Aprender del feedback (subir lo que convierte)
Nivel 5  Modelo que reordena candidatos
```

Cada peldaño se mide **offline** antes de ir en vivo, y las decisiones están en `docs/DECISIONES.md`.
Hoy en vivo: Nivel 0 → Nivel 1 en pruebas (e-samen-v0). Hoy construido: Nivel 3 completo (sección 6).

## 3. Cómo validamos antes de arriesgar (sin fórmulas)

El historial del carrusel viejo está **sesgado**: solo enseñaba lo popular, así que en los datos "lo popular
vende bien" siempre. Si evaluáramos contra esos datos sin corregir, elegiríamos lo de siempre para siempre.
La corrección (IPS, en palabras): cada acierto del historial vale más cuanto menos probable era que el sistema
viejo lo hubiera enseñado — recompensa lo que sabíamos mostrar poco. Además vigilamos `SNIPS` (la misma idea
para que el azar no engañe) y `slot1_matches` (si nuestro ranking casi clona el viejo, la medición no puede
no puede distinguirnos y no dice nada). Estado: el evaluador offline lo está montando Evaluación; **sin números todavía**.

## 4. Resultados (solo números medidos; fuente: consultas ejecutadas y `docs/EXPERIMENTOS.md`)

**En vivo** (`team_29.feedback`, ts en UTC; baseline 101 min, v0 21 min):

| | peticiones | clics | compras | latencia media |
|---|---|---|---|---|
| baseline del starter | 18.180 | 2,49 % | 0,79 % | 1.207 ms |
| e-samen-v0 (nuestra v0) | 3.901 | 2,49 % | 0,56 % | 1.009 ms |

- Fiabilidad v0: 93,4 % servido por nosotros (257 fallbacks `missing`, 1 `invalid`); p50/p95 773/1.091 ms
  — el baseline p95 era 2.232 ms, cerca del límite de 3 s.
- La v0 aún no supera en conversión al baseline (0,56 % vs 0,79 % en 21 min): no es un mal resultado aisladado,
  es la razón por la que medimos ANTES de subir peldaños — la decisión de ir peldaño a peldaño sale de esto.

**Nuestros datos (D2, consultas en `docs/datos.md`):**
- ~30 % de compradores son nuevos (29,79 % en la última hora); ~20 % entra sin nada visto → 1 de cada 3-5
  peticiones es "fría": el cold start no es un caso raro, es un tercio del stream.
- Catálogo: 5.100 productos, 5.064 disponibles (36 agotados/inactivos); stock se mueve (144 out_of_stock,
  108 back_in_stock y 100 productos nuevos en menos de un día de evento).
- El precio medio tiene colas (Books & Media: 79 € medio vs 30 € mediano) → para desempatar por ingreso,
  mediana, no media.
- Device y región cambian poco la conversión (4,2-4,9 %): sirven para elegir **a quién**, no para mejorarla.

**Nuestra pieza de cold start (medido hoy, D1-D4):**
- 5.000 descripciones embebidas (4096 dims, normalizadas) + los 100 productos nuevos, en `team_29.scratch`.
- Vecinos por producto: en el 98,4 % de los casos el vecino es de la misma categoría (77 % misma subcategoría):
  los vecinos "tienen sentido" y no requieren que el producto tenga ventas.
- 3 ejemplos de producto nuevo → vecinos de su misma subcategoría y marca (ver `docs/estado/artai.md`).

## 5. Cómo aprende del stream

1. Mientras tanto, precalculamos cada pocos minutos en SQL (co-visitas recientes, tendencias 15 min vs 3 h)
   y el servicio solo lee del `Estado` en memoria: el bucle en vivo no consulta RawTree.
2. Leemos nuestro propio `feedback` cada pocos minutos: es nuestro leaderboard privado durante la final
   (el oficial queda oculto): conversión por hora, motivos de fallback, latencia.
3. Aprende de verdad: subir las recomendaciones que convierten (tipo bandit) y re-embeber los productos
   nuevos cada ~5 min (`embeber_nuevos()`) para que un producto de hace 5 minutos ya tenga vecinos.
4. Exploración medible: un pequeño porcentaje de recomendaciones "curiosas" con registro en EXPERIMENTOS.

## 6. Cómo usamos RawTree (y por qué es clave aquí)

- **SQL sobre JSON sin esquema con casteo explícito**: todas las columnas son `Dynamic`; filtramos SIEMPRE
  por tiempo y limitamos: las tablas en vivo crecen sin parar (12M de eventos en 60 días).
- **Todo lo pesado, precalculado**: co-visitas y tendencias se calculan fuera del bucle; `recommend()`
  responde en ~0,8 ms por petición con 0 respuestas inválidas (medido sobre 200 peticiones reales).
- **`team_29.scratch` como nuestra memoria**: 5.100 embeddings (float16 en base64) compartidos entre el
  script de embeddings y el servicio, sin ficheros que sincronizar.
- **`catalog_updates` como fuente de verdad de las altas**: los productos nuevos no están (aún) en
  `shop.catalog`: los cogemos de ahí para embeber sin intervención humana.
- **`team_29.feedback` + logs**: medimos latencia y causas de fallback en vivo; el relevo de servicios se
  hace sin duplicar respuestas (0 duplicados en el relevo de las 14:08).

## 7. Qué haríamos con más tiempo

- **Modelo que reordena** (nivel 5): regresión logística → LightGBM con las features que ya precalculamos
  (co-visitas, precio relativo a la categoría, tendencia, recencia del producto visto).
- **Thompson sampling** sobre pocas variantes del ranking (no una parrilla infinita) para aprender en horas,
  no en días.
- **Cold start de comprador más fino**: popularidad por (device, region, page) con ventana corta, y vecinos
  por embedding ponderados por conversión (no solo similitud).
- **Robustez**: redundancia de servicio y relleno por edad de la petición si la luz roja amenaza.
- Medición del impacto real del embedding en el segmento frío con el evaluador offline de IPS (en cuanto
  Evaluación lo tenga, es la comparación "con vecinos" vs "populares globales").
