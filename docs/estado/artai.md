# Estado de Artai

- **Rol:** Datos y relato (docs/guias/datos.md)
- **Actualizado:** sáb ~19:15

## Haciendo ahora
- **Revisión de puntos ciegos tras el A7 (sáb ~19:45, después del pull)** — para comunicar al equipo:
  1. **`arbol.py` filtra memoria en vivo (Leonardo)**: `arbol._cache` usa claves por `id(estado.catalogo)`, y el servicio sustituye el catálogo cada 15 s → cada refresco añade entradas sin borrar las viejas (verificado: 24 entradas tras 50 refrescos). Unas 20 MB/hora; por la noche pueden ser cientos de MB. Fix de una línea: `_cache.clear()` antes de asignar (vivo.py ya lo hace así).
  2. **`construir_perfiles` NO está programado en el servicio** (solo `cargar_perfiles`, lectura): los perfiles del comprador/producto quedan congelados a mi construcción de las 19:00. En la final: compradores nuevos sin perfil (features del árbol a 0, degradación suave) y `tasa_ips` congelada al sábado. Añadir `intentar("perfiles", 3600, construir_perfiles(None))` al hilo lento (Lester).
  3. **`arbol.modelo_guardado()` se lee UNA sola vez al arrancar**: si `data/arbol.txt`/`arbol.json` faltan al reiniciar, A7 degrada a relleno silenciosamente y NO se recupera sin reiniciar (verificado: degrada con gracia y devuelve 10 válidos). Checklist de Lester antes de reiniciar para la final: los dos archivos existen.
  4. **`src/vivo.py` NO está cableado al servicio** (sin import ni llamada): el "aprendizaje del feedback en vivo" del commit A7 NO está en vivo — A7 usa el árbol del carrusel + favoritas del historial. Si la intención era que vivo aprendiera del feedback, falta el interruptor (Lester/Leonardo).
  5. **`MODELO_ELECCION` se carga al arrancar** (servicio.py:97): igual que el árbol, no se recarga sin reiniciar; y si faltaba al arrancar, un brazo `_eleccion` caería al fallback por excepción (capturada). Menor (V3 no está en vivo).
  6. **`estado.construir` auto-construye perfiles por corte**: con `evaluar` en paralelo (4 procesos) podrían dispararse 4 construcciones del mismo corte (queries pesadas ×4, uploads duplicados con gens distintos). Menor: sugiero construir el corte una vez en `main` antes del pool (Alex).
  - **Lo que comprobé que SÍ va bien**: A7 sin lightgbm/archivos degrada con gracia a relleno válido (10/10); productos nuevos del domingo en el árbol degradan suave (`lc.get(p, tip)`, `rango.get(p, n)`); las piezas con fallo se quedan con lo anterior (`intentar`) y no bloquean el stock; mi `cargar_perfiles` es tolerante a fallos de scratch.
- **Hallazgo del leaderboard (sáb ~19:45): el 2º equipo separa el tráfico por tipo de petición.** La clave está en el campo `cold_requests` de la API (`/api/state?by=model`): las versiones del team_26 con conv == frío EXACTAS (m0c62b 3,37, m41245 2,74, mf4d99 2,23) tienen `cold_requests == requests` — solo reciben peticiones frías; otras (`m56b24`, `mca3af`) tienen `cold_requests = 0` — solo cálidas. O sea: **enrutamiento por tipo de petición** (frío → modelo especialista; cálido → modelo de sesión) en vez de reparto por hash de usuario como hacemos nosotros.
  - Su versión solo-fríos convierte **3,37 %** (por encima de la referencia 2,09): un especialista de fríos que aprende EN VIVO qué productos convierten (feedback loop por producto) — nivel 4 de nuestra escalera funcionando como brazo dedicado.
  - Implicación para nosotros: (1) proponer a Lester repartir brazos por tipo de petición (frío/cálido) y no por hash de usuario; (2) el especialista de fríos puede arrancar de `perfiles_producto[pid]["tasa_ips"]` (ya en el Estado) + tendencias, y aprender del feedback; (3) corrección a mi análisis anterior: la "igualdad exacta" no era "misma lista para todos", era enrutamiento.
- **CORRECCIÓN de mi lectura del anuncio de las 17:40 (gracias Lester/Alex/Leonardo por corregirla).**
  Texto EXACTO del mensaje de los organizadores (Lester lo pidió para contrastar con lo que dijeron en persona):
  > "Welcome to the real world, folks. Sometimes the client hands you requirements that don't quite match the
  > data. Sometimes the data swears customers love buying the same blender three times a week. And sometimes
  > the hidden model just disagrees with both. That's not a bug in your datathon; that's a preview of your
  > career. Pro tip from people who've been there: trust the feedback loop more than the docs, and the docs
  > more than a CSV someone exported 'a while ago'."
  El mensaje dice que el modelo oculto puede NO estar de acuerdo NI con los requisitos NI con los datos — NO
  dice que premie recomprar. Datos duros (API, ~2.800 pet./brazo): total CF (CON filtro) 1,06 vs C (sin) 1,02;
  fríos C 1,73 vs CF 1,41; y EN VIVO no hay recompras. Ya he corregido docs/presentacion.md y el panel Tukey;
  Leonardo ya dejó los defaults como estaban (filtrar_comprados True, frio_historial 0.0) y el mecanismo
  frio_historial queda EN EL CÓDIGO apagado, listo para que Alex lo mida en un brazo A/B.
- **Tarea de Leonardo para R6 hecha (sáb ~19:00)**: `cargar_perfiles(hasta)` + `construir_perfiles(hasta)` en `src/coldstart.py`, subidos a scratch y cargados en el Estado.
  - `perfiles_comprador` (64.470 en vivo): user_id -> top 3 categorías/subcategorías/marcas + precio mediano + nº compras. Fuentes: history_events + shop.events (solo en vivo, con corte se excluye).
  - `perfiles_producto` (4.691): product_id -> veces enseñado, veces comprado, tasa de compra corregida por propensity (SNIPS por posición: sum(compró/p)/sum(1/p)) desde history_sessions con `arrayJoin(arrayZip(shown, propensity))`.
  - `estado.py` (Lester: cambio aditivo): campos nuevos `perfiles_comprador` y `perfiles_producto`; `construir(hasta)` los carga de scratch y auto-construye el corte si falta (respetar `hasta`, sin mirar futuro).
  - **Para Leonardo (R6)**: `estado.perfiles_comprador[user]["precio_mediano"]` = tu "precio habitual del comprador"; de ahí salen `precio/precio de lo último visto` y `precio/precio_mediano`; `perfiles_producto[pid]["tasa_ips"]` es la tasa corregida. Los perfiles de un corte se construyen solos la primera vez (60-90 s) y luego van de cache.
  - **Para Lester**: añadir `coldstart.construir_perfiles(None)` al hilo lento del servicio cada ~30-60 min (si no, R6 usará perfiles congelados al sábado).
  - Trampas de scratch (por si tocáis): (1) events NO traen categoría: join con catálogo; (2) la subcolumna `hasta` se infirió como Date → comparar SIEMPRE `hasta::String`; (3) filas recién insertadas tardan en verse: no leer justo después de subir; (4) v1 de perfiles quedó corrupta (caracteres sueltos) → tipos ahora `perfil_comprador_v2`/`perfil_producto_v2`, el cargador ignora v1.
- **Lectura del leaderboard (sáb ~18:30)**. Fuente: https://datathon.rawtree.dev/api/state?by=model
  - Situación: 24/42 con 0,82-0,83 %. Baseline real ≈ 0,57-0,60 (equipos sin on-time = fallback puro). Líder team_06 2,2-2,4; referencia organizadores 2,09.
  - **CORREGIDO tras el apunte de Alex**: CF es CON filtro; el A/B dice CF 1,06 vs C 1,02 (total) y C 1,73 vs CF 1,41 (fríos): diferencias pequeñas; nada decidido por mi lectura anterior ("sin filtro mejor") — eso era un error de interpretación de los sufijos.
  - Técnicas observadas en los de arriba: co-visitas de sesión como motor; iteración rápida contra la API del leaderboard (team_06: v8→v12 en una hora); pesos aprendidos en vivo y tendencias ayudan; modelos pesados (BPR/XGB/LGBM) a mitad de tabla.
- **Revisión de bugs de todo `src/` (sáb ~17:15) y arreglos aplicados** (los detalles están en DECISIONES y commits): 429 limitados (embeddings), cache de covisitas con firma, fallback completo y validado (servicio), guard de propensity y límite de procesos (evaluar), docstring usuarios, panel Tukey por versión.
- Presentación + panel Tukey listos; pidiendo números a Evaluación y resultados en vivo para rellenar los **[PENDIENTE]** de docs/presentacion.md.

## Terminado
- **D6 co-visitas heredadas** (fase 2): `cargar_covisitas_heredadas(hasta, covisitas)` en `src/coldstart.py`.
  - Producto sin co-visitas → media ponderada por similitud de las de sus 20 vecinos, recortada a 20 pares. Misma forma que `covisitas` (dict pid -> [(otro, peso)]).
  - Vecinos DENTRO de la subcategoría del producto (completan con la categoría si faltan), como pide la D4 actualizada. Matemática probada con datos de juguete + humo real.
  - **Lester (Operador), decidir**: campo nuevo `covisitas_heredadas` en el Estado O mezclarlas en `covisitas` en `construir`. Lo primero es más fácil de medir por separado (mi preferencia).
- **D7 historia para el jurado**: `docs/presentacion.md`: curva de posición medida (tabla 10 slots), "generalizar no memorizar" (DropoutNet), diapositiva honesta "lo que NO hicimos y por qué".
- **Divergencia consciente en D3**: la guía pide peso ×5 a compras de las últimas 2 h; mantengo la mezcla "20 globales delante" porque el contexto-puro empeoraba a los fríos (medido). Propongo probar el "peso reciente" como variante A/B cuando se re-mida. Decide Evaluación.
- **D1 embeddings**: `src/embeddings.py`, 5.000 vectores (qwen3-embedding, 4096 dims, normalizados) en `data/` + `team_29.scratch` (5.000 filas emb_v1, sin duplicados, reanudable).
- **D2 exploración**: `docs/datos.md` responde las 5 preguntas (+ §6 recompras con las dos definiciones y sus consultas).
- **D3 populares por contexto**: `cargar_populares_contexto(hasta)`, 30 pares × 50 productos con mezcla global delante (commit `2ed6420`), respeta `hasta`.
- **D4 vecinos**: `cargar_vecinos(hasta)` (dentro de la subcategoría, completan con categoría) + `embeber_nuevos()` (los 100 new_product ya embebidos).
- **D5 presentación + side challenge Tukey**: `docs/presentacion.md` y `src/panel_tukey.py` → `data/panel.html` (10 paneles; conversión por versión del A/B uniendo feedback⋈submissions).
- IMPORTANTE: he añadido `HELMCODE_API_KEY` al `.env` de este repo.

## Descubierto (cosas que los demás deben saber)
- **EN VIVO NO HAY RECOMPRAS** (organizadores, 17:40): el 62 % de recompras del historial NO existe en el stream. Consecuencias: el test offline debe quitar las filas de recompra (Alex), el filtro de comprados se decide SOLO con el A/B (total CF 1,06 vs C 1,02; fríos C 1,73 vs CF 1,41), y cualquier señal "recompra" en el modelo hay que medirla antes de encenderla (hundió a R5: 0,47 %).
- **Regla nueva del organizador (sáb ~16:45): gracia de 5 s tras un stock-out** — un agotado hace <5 s ya no invalida la respuesta (ese slot no vende, el resto cuenta). Los "invalid" del panel se interpretan con esto (Alex); menos presión sobre la lectura de stock en vivo (Lester).
- **Fiabilidad: la causa principal es la wifi de la sala** (medición de Lester: señal 48 %, ping hasta 471 ms, 10 % paquetes perdidos). Los "missing" son pérdida de red, no lentitud nuestra.
- `team_29.scratch` vacío no tiene columnas (`DESCRIBE` vacío, `WHERE` falla); tras la primera inserción funciona, con retardo de visibilidad de varios segundos tras insertar lotes grandes.
- Comparar sin casteo va bien (`WHERE tipo = 'emb_v1'`), pero para DISTINCT/agregados castea (`product_id::String`).
- **OJO git**: desde WSL sobre /mnt/c, TODOS los archivos aparecen modificados por fin de línea (CRLF/LF). Prohibido `git add .`; solo `git add <archivo>`. Conviene un `.gitattributes` (decisión del equipo).
- En este Linux no había pip: instalado con get-pip `--user` (requests y numpy ya están en requirements).

## Necesito de alguien
- **Operador (Lester)**: `coldstart.embeber_nuevos()` cada ~5 min en el servicio (hecho según tu estado) y `coldstart.construir_perfiles(None)` cada ~30-60 min (R6).
- **Leonardo**: cuando R6 use los perfiles, los campos están en el Estado (`perfiles_comprador`, `perfiles_producto`); el mecanismo `frio_historial` está en el código APAGADO para que lo pruebes como variante si quieres.
- **Alex**: re-medir el segmento frío con el test SIN recompras (tu propio apunte) y, si quieres probar `frio_historial`, es un brazo con params={"frio_historial": 0.3}.
