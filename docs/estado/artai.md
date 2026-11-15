# Estado de Artai

- **Rol:** Datos y relato (docs/guias/datos.md)
- **Actualizado:** dom ~04:15

## Haciendo ahora
- **Deep research (dom ~04:15): ¿algún algoritmo nuevo que valga la pena antes del congelado?**
  Método: literatura de session-based recommendation (Ludewig/Jannach 2020 y sucesores, framework session-rec, ensembles SKNN+GRU4REC) + nuestras filas de EXPERIMENTOS. Veredicto corto: **solo 2 candidatas pasan el filtro de "funciona y tiene impacto"**: (1) AP8 tasa_vivo, ya especificada a Alex (métrica frío: 10 puntos propios en la final, 30 % del tráfico); (2) **BLEND por pesos fijos de nuestras dos cabezas** (árbol R6 + aprendiz AP3): la literatura lo avala (los ensembles de pesos fijos SKNN+GRU4REC ganan a cada modelo por separado, combinando su fuerza en lo popular y su robustez en lo menos popular), los dos modelos ya están cargados en el servicio y el blend entra como variante del servicio sin tocar `ranking.py`. Verificación barata: examen offline de Evaluación (normalizamos cada puntuación con sigmoide, peso w afirmado con el test) y después A/B.
  - Descartadas por evidencia (para DECISIONES del jurado): modelos neuronales de sesión (GRU4REC/SASRec/SR-GNN: 8+ h de entrenamiento, y en el estudio de Ludewig los vecinos compiten), bandits por producto y Thompson (la exploración medida esta noche cuesta 0,15-0,35 en las peticiones que exploran, con 1 clic y 0 compras en 1.462 productos nuevos), KNN sustituyendo al aprendiz (ya lo usamos como señal: co-visitas), peso por dinero como señal primaria (el precio no mueve la compra en vivo: ×1,0 [0,98-1,11]), dividir por popularidad (medido: empeora).
  - Y el punto que no es algoritmo pero vale 10 pts: **fiabilidad**. La caída nocturna (servicio parado ~2 h, on-time al 26,9 % en 3 h) costó más que cualquier movimiento de modelo del día. Watchdog de autoarranque + la decisión pendiente de la nube (Lester).
- **Tarea de Leonardo para R6 hecha (sáb ~19:00)**: `cargar_perfiles(hasta)` + `construir_perfiles(hasta)` en `src/coldstart.py`, subidos a scratch y cargados en el Estado.
  - `perfiles_comprador` (64.470 en vivo): user_id -> top 3 categorías/subcategorías/marcas + precio mediano + nº compras. Fuentes: history_events + shop.events (solo en vivo, con corte se excluye).
  - `perfiles_producto` (4.691): product_id -> veces enseñado, veces comprado, tasa de compra corregida por propensity (SNIPS por posición: sum(compró/p)/sum(1/p)) desde history_sessions con `arrayJoin(arrayZip(shown, propensity))`.
  - `estado.py` (Lester: cambio aditivo): campos nuevos `perfiles_comprador` y `perfiles_producto`; `construir(hasta)` los carga de scratch y, si el corte no está construido, lo construye automáticamente con ese corte (respetar `hasta`, sin mirar futuro).
  - **Para Leonardo (R6)**: `estado.perfiles_comprador[user]["precio_mediano"]` es el "precio habitual del comprador"; de ahí salen `precio/precio de lo último visto` y `precio/precio_mediano`; `perfiles_producto[pid]["tasa_ips"]` es la tasa corregida. Los perfiles de un corte se construyen solos la primera vez (60-90 s) y luego van de cache.
  - Trampas encontradas por el camino (por si tocáis scratch): (1) `shop.events`/`history_events` NO tienen categoría: unirse al catálogo; (2) la subcolumna `hasta` se infirió como Date y `WHERE hasta = 'LIVE'` reventaba → comparar SIEMPRE con `hasta::String`; (3) las filas recién insertadas tardan en verse (varios s con lotes grandes): no leer justo después de subir; (4) v1 de los perfiles quedó corrupta en scratch (top-3 reducido a caracteres) → los tipos ahora son `perfil_comprador_v2`/`perfil_producto_v2` y el cargador ignora v1.
- **Revisión de puntos ciegos tras el A7 (sáb ~19:45)** — para comunicar al equipo:
  1. `arbol.py` filtra memoria en vivo (Leonardo): `arbol._cache` con claves por `id(estado.catalogo)` y el catálogo sustituido cada 15 s → entradas viejas sin borrar (verificado: 24 tras 50 refrescos). Fix: `_cache.clear()` antes de asignar.
  2. `arbol.modelo_guardado()` se lee UNA sola vez al arrancar: si `data/arbol.txt`/`arbol.json` faltan al reiniciar, A7 degrada a relleno sin recuperarse (verificado: degrada con gracia y devuelve 10 válidos). Checklist de Lester antes de reiniciar.
  3. `MODELO_ELECCION` igual: se carga al arrancar, sin recarga (menor, V3 no está en vivo).
  4. `estado.construir` auto-construye perfiles por corte: con `evaluar` paralelo podrían dispararse 4 construcciones (menor; construir el corte una vez en `main`, Alex).
  - Lo que SÍ va bien: A7 sin lightgbm/archivos degrada con gracia (10 válidos); productos nuevos del domingo degradan suave en el árbol; las piezas con fallo se quedan con lo anterior (`intentar`); mi `cargar_perfiles` es tolerante a fallos de scratch.
- **Revisión de bugs de todo `src/` (sáb ~17:15)**: 429 limitados (embeddings), cache de covisitas con firma de parámetros, fallback completo y validado + contador de lotes perdidos (servicio), guard de propensity y límite de procesos (evaluar), docstring usuarios, panel Tukey por versión.
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
- **D3 populares por contexto**: `cargar_populares_contexto(hasta)`, 30 pares × 50 productos con mezcla global delante, respeta `hasta`.
- **D4 vecinos**: `cargar_vecinos(hasta)` (dentro de la subcategoría, completan con categoría) + `embeber_nuevos()` (los 100 new_product ya embebidos).
- **D5 presentación + side challenge Tukey**: `docs/presentacion.md` y `src/panel_tukey.py` → `data/panel.html` (10 paneles; conversión por versión del A/B uniendo feedback con las respuestas por request_id).
- IMPORTANTE: he añadido `HELMCODE_API_KEY` al `.env` de este repo.

## Descubierto (cosas que los demás deben saber)
- **EN VIVO NO HAY RECOMPRAS** (organizadores, 17:40): el 62 % de recompras del historial NO existe en el stream. Consecuencias: el test offline debe quitar las filas de recompra (Alex), el filtro de comprados se decide SOLO con el A/B, y cualquier señal "recompra" en el modelo hay que medirla antes de encenderla (hundió a R5: 0,47 %).
- **Regla nueva del organizador (sáb ~16:45): gracia de 5 s tras un stock-out** — un agotado hace <5 s ya no invalida la respuesta (ese slot no vende, el resto cuenta). Los "invalid" del panel se interpretan con esto (Alex); menos presión sobre la lectura de stock en vivo (Lester).
- **Fiabilidad: la causa principal es la red** (medido por Lester: señal baja, ping errático, ~10 % de paquetes perdidos en los cortes). La caída nocturna (servicio parado) dejó un hueco de ~2 h: cada petición sin respuesta convierte como baseline.
- `team_29.scratch` vacío no tiene columnas (`DESCRIBE` vacío, `WHERE` falla); tras la primera inserción funciona, con retardo de visibilidad de varios segundos tras insertar lotes grandes.
- Comparar sin casteo va bien (`WHERE tipo = 'emb_v1'`), pero para DISTINCT/agregados castea (`product_id::String`).
- **OJO git**: desde WSL sobre /mnt/c, TODOS los archivos aparecen modificados por fin de línea (CRLF/LF). Prohibido `git add .`; solo `git add <archivo>`. Conviene un `.gitattributes` (decisión del equipo).
- En este Linux no había pip: instalado con get-pip `--user` (requests y numpy ya están en requirements).

## Necesito de alguien
- **Alex (Evaluación) — propuesta AP8: señal `tasa_vivo` para el aprendiz, SOLO fríos** (especificación completa, dom 04:15; mi parte ya está: los datos vienen de `team_29.feedback` que tú ya descargas en cada reentreno).
  - **Por qué**: el filtrado del aprendiz calibra la conversión por producto-sesión; la tasa estática del carrusel (`tasa_ips`) no aporta (medido, IC con el 0), pero la tasa calculada con lo que ENSEÑAMOS nosotros hoy mide al modelo de ahora mismo.
  - **Definición**: sobre feedback de `served_by = 'team'` de las ÚLTIMAS 4 h, solo peticiones frías (sin `recent_views` o is_new_user):
    - `enseñados(p)` = nº de huecos en los que enseñamos el producto p.
    - `aciertos(p)` = compras de p + 0,3 × clics de p (el 0,3 es tu propio OBJETIVO_CLIC).
    - `t_global` = tasa de conversión fría global del mismo tramo.
    - **`tasa_vivo(p) = (aciertos(p) + k·t_global) / (enseñados(p) + k)` con k = 30 impresiones de prior**.
    - Se recorta a [0, 10 × t_global] y se expone como señal multiplicada por el indicador `frio` (en cálidos vale 0, para no duplicar lo que ya saben otras señales).
  - **Entreno**: entra como una señal más en `SENALES`; el peso lo aprende el reentreno, NO se fuerza.
  - **Validación (tu protocolo)**: entreno 70 % inicial del día / prueba 30 % final, IC 95 % bootstrap por respuesta (400), AP8 = AP7 + `tasa_vivo` frente a AP7C (= AP7 mismo entreno sin la señal). Métricas: logloss Y compras/100 estimadas en fríos.
  - **A/B en vivo para Lester** (si la validación da el ok): fríos 50/50 `{"_aprendiz": "ap8"}` frente a `{"_aprendiz": "ap7"}`, cálidos con el árbol como en A7. Etiquetas -AP8/-AP7 y lectura por la API.
  - **Si las 08:00 llegan antes**: nada queda a medias — la señal apagada no molesta y el A/B puede correr tras el congelado si Evaluación lo aprueba antes de las 10:00.
- **Operador (Lester)**: `coldstart.embeber_nuevos()` cada ~5 min en el servicio (hecho) y `coldstart.construir_perfiles(None)` cada ~30-60 min (R6) — si no, los perfiles quedan congelados a la última construcción manual.
- **Leonardo**: cuando R6 use los perfiles, los campos están en el Estado (`perfiles_comprador`, `perfiles_producto`); el mecanismo `frio_historial` está en el código APAGADO para que lo pruebes como variante si quieres.
- **Para el checkpoint de las 08:00**: refrescar presentación/panel con los números del día; corregir juntos los [PENDIENTE] de docs/presentacion.md.
