# Estado de Artai

- **Rol:** Datos y relato (docs/guias/datos.md)
- **Actualizado:** sáb ~14:15

## Haciendo ahora
- D2: exploración con DESCRIBE y números en docs/datos.md.

## Terminado
- **D1 embeddings**: `src/embeddings.py` (`python -m src.embeddings`), commit `5159f0b`.
  - 5.000/5.000 vectores (qwen3-embedding, 4096 dims, normalizados) en `data/embeddings.npy` + `data/embeddings_ids.json`.
  - Verificado: `SELECT count() ... WHERE tipo = 'emb_v1'` en `team_29.scratch` = 5.000, product_ids distintos = 5.000 (sin duplicados; las filas que ya estaban no se reenvían).
  - Reanudable: guarda tras cada lote de 32 y no repide lo ya pedido/subido.
  - IMPORTANTE: añadí `HELMCODE_API_KEY` al `.env` local de este repo.

## Descubierto (cosas que los demás deben saber)
- `team_29.scratch` sin datos no tiene columnas (`DESCRIBE` da vacío y cualquier `WHERE` falla con Unknown identifier). Tras la primera inserción funciona normal; verás datos con 1-2 s de retardo.
- En `scratch` las comparaciones sobre columnas funcionan sin casteo (`WHERE tipo = 'emb_v1'`), pero para DISTINCT u operaciones cásteadas: `product_id::String`.
- **OJO**: si trabajamos desde WSL sobre el repo en /mnt/c, TODOS los archivos aparecen como modificados por fin de línea (CRLF/LF). `git diff` muestra reescrituras completas con 0 cambios reales. No hacer `git add .` (solo `git add <archivo>` concreto); conviene arreglarlo con `.gitattributes`/autocrlf.
- En este Linux no había pip: instalado pip + requests + numpy con get-pip `--user`.

## Necesito de alguien
- Lester: cuando exista D4 (`cargar_vecinos` + `embeber_nuevos()` en src/coldstart.py), llamar a `embeber_nuevos()` cada 5 min en el servicio.
- El push de `5159f0b` queda pendiente de que Artai lo lance.
