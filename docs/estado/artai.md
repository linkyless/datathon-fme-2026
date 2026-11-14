# Estado de Artai

- **Rol:** Datos y relato (docs/guias/datos.md)
- **Actualizado:** sáb ~14:50

## Haciendo ahora
- Presentación + panel Tukey listos; pidiendo números a Evaluación y resultados en vivo para rellenar los **[PENDIENTE]** de docs/presentacion.md.
- **Arreglo tras la medición de Alex (sáb ~15:30)**: `populares_contexto` ahora lleva los 20 populares generales DELANTE y lo específico del (device, region) detrás (commit `2ed6420`). Así el relleno de fríos no puede rendir peor que los populares de siempre (medido: 0,60) y se conserva la señal de contexto para la cola. Alex: remédelo cuando quieras.
- **Vecinos no hace falta apagarlos**: `ranking.py` ya solo los usa para productos SIN co-visitas (paso 2). El desastre 0,76→0,05 era por co-visitas vacío, no por los vecinos. Con co-visitas de Leonardo, quedan automáticamente limitados a lo nuevo/lo raro. Confirmárselo a Leonardo.

## Terminado
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
