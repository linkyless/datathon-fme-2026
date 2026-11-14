# Estado de Artai

- **Rol:** Datos y relato (docs/guias/datos.md)
- **Actualizado:** sáb ~18:00

## Haciendo ahora
- **Panel de RECOMPRAS añadido a Tukey + sección 6 en docs/datos.md (sáb ~18:00)**: con la definición estricta de Evaluación, el **100 %** de las compras del test (3.530/3.530) son de un producto ya comprado antes por el mismo usuario (con la definición amplia, 99,0 %). La decisión queda blindada con número: `filtrar_comprados` APAGADO. ⚠️ Divergencia con la cifra de Alex (61,6 %) — reconciliar definiciones con él (consultas replicables en docs/datos.md §6).
- **Revisión de bugs de todo `src/` (sáb ~17:15) y arreglos aplicados** (pedidos por Artai; commit de hoy):
  1. `embeddings.py` (mío): los 429 ya no reintentan para siempre — tras 5 intentos suelta excepción y el servicio lo reintenta en 60 s. Antes, una saturación de HelmCode congelaba el refresco de TODAS las piezas pesadas.
  2. `covisitas.py` (Leonardo): el cache incluye FIRMA de parámetros (pesos, vecinos, exponente) en el nombre — antes, cambiar un peso dejaba el servicio leyendo co-visitas viejas sin aviso.
  3. `servicio.py` (Lester): el fallback se completa con disponibles hasta 10 y se valida antes de enviar (antes podía salir <10 → inválida); y los lotes que fallan 2 intentos se cuentan como `perdidas` (visibles en el log).
  4. `evaluar.py` (Alex): guard de propensity (0/NULL no matan la medición) y límite de 4 procesos de evaluación por RAM.
  5. `usuarios.py` (Leonardo): docstring actualizado — el filtro de comprados es parámetro medido, no regla fija (61,6 % recompras en el test).
  6. `panel_tukey.py` (mío): conversión POR VERSIÓN (une feedback⋈submissions como el panel de Alex) — los brazos del A/B ya no se mezclan. Dato de las 17:15: e-samen-v1-C 0,97 % vs baseline 0,78 % (6 h).
- Presentación + panel Tukey listos; pidiendo números a Evaluación y resultados en vivo para rellenar los **[PENDIENTE]** de docs/presentacion.md.
- **Arreglo tras la medición de Alex (sáb ~15:30)**: `populares_contexto` ahora lleva los 20 populares generales DELANTE y lo específico del (device, region) detrás (commit `2ed6420`). Así el relleno de fríos no puede rendir peor que los populares de siempre (medido: 0,60) y se conserva la señal de contexto para la cola. Alex: remédelo cuando quieras.
- **Vecinos no hace falta apagarlos**: `ranking.py` ya solo los usa para productos SIN co-visitas (paso 2). El desastre 0,76→0,05 era por co-visitas vacío, no por los vecinos. Con co-visitas de Leonardo, quedan automáticamente limitados a lo nuevo/lo raro. Confirmárselo a Leonardo.

## Terminado
- **D6 co-visitas heredadas** (fase 2, commit de hoy): `cargar_covisitas_heredadas(hasta, covisitas)` en `src/coldstart.py`.
  - Producto sin co-visitas → media ponderada por similitud de las de sus 20 vecinos, recortada a 20 pares. Misma forma que `covisitas` (dict pid -> [(otro, peso)]).
  - Vecinos ahora DENTRO de la subcategoría del producto (completan con la categoría si faltan), como pide la D4 actualizada. Matemática probada con datos de juguete + humo real (5100 productos, 8,6 s vecinos; herencia depende de cuántos vecinos tengan co-visitas).
  - **Lester (Operador), decidir**: campo nuevo `covisitas_heredadas` en el Estado (y que `ranking` lo use como segunda fuente para pids sin co-visitas) O mezclar yo el dict dentro de `covisitas` en `construir`. Lo primero es más fácil de medir por separado (mi preferencia).
  - **Nota de rendimiento**: `construir()` añadirá ~10-13 s (decodificar vectores una vez, cache interno 2 min + atributos 10 min). El refresco del servicio puede seguir cada 60 s.
- **D7 historia para el jurado**: `docs/presentacion.md` (commit de hoy): curva de posición medida (tabla 10 slots), principio "generalizar no memorizar" (DropoutNet explicado), y nueva diapositiva honesta "lo que NO hicimos y por qué" (redes, bandit por producto, GBM directo) con la evidencia de investigación.md.
- **Divergencia consciente en D3**: la guía actualizada pide peso ×5 a las compras de las últimas 2 h y completar parejas con <20 compras. Yo mantengo la mezcla "20 globales delante" porque la medición de Alex mostró que el contexto-puro empeora a los fríos. Propongo: dejar la mezcla como base y probar el "peso reciente" como variante A/B (parámetro) cuando se re-mida. Quien decida: Evaluación.
- **D1 embeddings**: `src/embeddings.py` (`python -m src.embeddings`), commit `5159f0b`.
  - 5.000/5.000 vectores (qwen3-embedding, 4096 dims, normalizados) en `data/embeddings.npy` + `data/embeddings_ids.json`.
  - Verificado: 5.000 filas `emb_v1` en `team_29.scratch`, product_ids distintos = 5.000 (sin duplicados; lo ya subido no se reenvía).
  - Reanudable: guarda tras cada lote de 32 y no repite lo ya pedido/subido.
- **D2 exploración**: `docs/datos.md` responde las 5 preguntas (commit `d405af6`). Claves: ~30 % compradores nuevos; en `history_sessions` `clicked`/`purchased` guardan el product_id; el precio medio tiene colas (usar mediano para desempate).
- **D3 populares por contexto**: `cargar_populares_contexto(hasta)` en `src/coldstart.py` (commit `1cceaab`). 30 pares (3 devices × 10 regiones) × 50 productos, respeta `hasta`.
- **D4 vecinos**: `cargar_vecinos(hasta)` + `embeber_nuevos()` en `src/coldstart.py` (commit `7aafb58`).
  - 5.100 productos con 20 vecinos (98,4 % misma categoría, 77 % misma subcategoría; ~6 s por carga).
  - Los 100 `new_product` de `catalog_updates` ya están embebidos (NO están en shop.catalog: llegan solo por updates).
- **D5 presentación**: `docs/presentacion.md` reescrita alrededor del side challenge Tukey (detalles del panel en la sección 3; huecos **[PENDIENTE]** para resultados que aún no hay).
- **Side challenge Tukey**: `src/panel_tukey.py` → `data/panel.html` (9 paneles, uno por pregunta del reto, con la decisión que alimenta; autocontenido, sin CDN; `--loop 60` para la demo). Fila añadida a DECISIONES.
- IMPORTANTE: he añadido `HELMCODE_API_KEY` al `.env` de este repo.

## Descubierto (cosas que los demás deben saber)
- **¡El organizador lo acaba de confirmar (sáb ~17:40)!** Mensaje literal: "Sometimes the data swears customers love buying the same blender three times a week... trust the feedback loop more than the docs, and the docs more than a CSV". Es decir: el modelo oculto SÍ premia recomprar, la pista oficial engañaba, y la medición de Alex (61,6 % recompras; filtro hunde purchase_hit 0,86 → 0,32) era la verdad. **URGENTE para Lester: `filtrar_comprados` sigue True por defecto en los brazos C y T del A/B en vivo — apagarlo YA** (convierte menos). Alex ya lo propuso; el mensaje del organizador es la confirmación. Después, adaptar `prueba_local.py` (línea 54) para que el default pueda quedar False. Para la presentación: este episodio ES la diapositiva metodológica ("nos fiamos del feedback medido, no de la documentación") — gratis, con episodio real incluido.
- **Regla nueva del organizador (sáb ~16:45): gracia de 5 s tras un stock-out.** Un agotamiento tarda hasta ~2 s en aparecer en `catalog_updates`; ahora un producto agotado hace menos de 5 s antes de la petición ya NO invalida la respuesta (ese slot no vende nada, pero el resto de la lista cuenta). Consecuencias para el equipo: (1) los "invalid" del panel ya no son todos nuestros, hay que leer el motivo con ojo (Alex, Evaluación); (2) menos presión sobre la cadencia de lectura de stock en vivo (Lester); (3) para la presentación, el riesgo de fiabilidad por stock se relaja. Apuntado también en docs/presentacion.md (nota en la sección del panel).
- **A/B en vivo de Lester (16:30) — el brazo A NO es la v0**: lleva el filtro de comprados. Offline ese filtro hunde el purchase_hit de 0,86 a 0,32: el 61,6 % de las compras del test son productos que el usuario YA HABÍA COMPRADO (recomprar es lo normal, al menos en el tramo de test). Propuesta de Evaluación: A = v0 real (sin co-visitas ni filtro), B = co-visitas sin filtro, y un interruptor `filtrar_comprados` apagado por defecto (para Leonardo). Lo que implica para nosotros: la pista oficial "quien ya compró no lo quiere otra vez" NO se cumple en este tramo — el filtro tiene que ser un parámetro medido, no una regla fija.
- **Fiabilidad cayó**: 80 % servido a las 16:30 (392 tarde, p95 2,7 s). Alex lo tiene en su panel (`python -m src.panel --horas 1`; src/panel.py es SUYO, el de Tukey es src/panel_tukey.py — no confundir).
- **Los `new_product` de catalog_updates NO aparecen en shop.catalog** (a día de hoy). El stock/activos de las altas nuevas hay que leerlo de `catalog_updates` (para el Operador: afecta al filtro de `disponibles`).
- **100 filas rotas en team_29.scratch**: tipo emb_v1 con product_id vacío (mi primer bug, ver DECISIONES). No se pueden borrar (la clave no es admin: `ALTER TABLE ... DELETE` → 403). Los lectores ya las filtran. Preguntar en el stand si pueden purgarlas.
- `team_29.scratch` vacío no tiene columnas (`DESCRIBE` vacío, `WHERE` falla); tras la primera inserción funciona, con 1-2 s de retardo.
- Comparar sin casteo va bien (`WHERE tipo = 'emb_v1'`), pero para DISTINCT/agregados castea (`product_id::String`).
- **OJO git**: desde WSL sobre /mnt/c, TODOS los archivos aparecen modificados por fin de línea (CRLF/LF). Prohibido `git add .`; solo `git add <archivo>`. Conviene un `.gitattributes` (decisión del equipo).
- En este Linux no había pip: instalado con get-pip `--user` (requests y numpy ya están en requirements).

## Necesito de alguien
- **Operador (Lester): llama a `coldstart.embeber_nuevos()` cada ~5 min en el servicio** (D4: producto nuevo → vecinos en caliente).
- Evaluación (Alex): mide el segmento frío con `populares_contexto` (D3) y `vecinos` (D4); avisado.
- Push pendiente (decisión de Artai): `1cceaab`, `7aafb58`.
