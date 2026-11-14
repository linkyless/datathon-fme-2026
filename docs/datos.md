# Datos: lo que sabemos de las tablas

Consultas ejecutadas el **sáb 14-11-2026 ≈ 14:00 hora Barcelona** (los `ts` de RawTree están en UTC).
Canal usado: la API HTTP de RawTree vía `src.rawtree.query` (el mismo cliente que usa el servicio; el
MCP no está expuesto en la sesión de OpenCode, pero es la misma API). Cada query está debajo de su
respuesta. Recordatorio: todas las columnas son `Dynamic`, por eso los casteos (`::String`, `::Bool`, `::Float64`).

---

## 1. Columnas de cada tabla (DESCRIBE)

| Tabla | Columnas (todas `Dynamic`) |
|---|---|
| `shop.catalog` | brand, category, created_at, description, is_active, price_eur, product_id, subcategory |
| `shop.history_sessions` | clicked, context.device, context.page, context.region, propensity, purchased, recent_views, request_id, revenue_eur, shown, ts, user_id |
| `shop.history_events` | device, event_type, price_eur, product_id, region, session_id, ts, user_id |
| `shop.requests` | context.device, context.page, context.region, is_new_user, recent_views, request_id, ts, user_id |
| `shop.events` | device, event_type, price_eur, product_id, region, session_id, ts, user_id (igual que history_events) |
| `shop.catalog_updates` | brand, category, change, created_at, description, is_active, price_eur, product_id, subcategory, ts (catálogo completo + ts + change) |

**Respuesta directa: `history_sessions` NO tiene `is_new_user`** (solo está en `shop.requests`, tipo `Bool`).
**SÍ tiene `context.page`** (home/category/product), igual que `shop.requests`.

Tamaños y tipos medidos:

- `shop.history_events`: 11.996.560 filas, rango `2026-09-15` → `2026-11-14` (60 días).
- `shop.history_sessions`: 2.000.000 filas.
- Tipos concretos medidos (`dynamicType`): `is_new_user` = `Bool`; `recent_views` = mayormente
  `Array(JSON)` (88 %) y `Array(Nullable(String))` (20 % de las filas de la última hora): en los dos casos
  `length(recent_views)` funciona igual.
- **Descubrimiento importante:** en `history_sessions`, `clicked` y `purchased` NO son true/false:
  guardan el `product_id` que se clicó / compró (cadena vacía `""` cuando no pasó nada). El `propensity`
  acompaña a `shown` (10 valores). Para "¿compró?" usar `purchased::String != ''`.

```sql
DESCRIBE TABLE shop.history_sessions SETTINGS describe_include_subcolumns = 1;   -- y lo mismo para las otras 5
SELECT min(ts::DateTime64(3)), max(ts::DateTime64(3)), count() FROM shop.history_events;
SELECT dynamicType(is_new_user) t, count() n FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR GROUP BY t;
SELECT dynamicType(recent_views) t, count() n FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR GROUP BY t;
SELECT clicked::String c, purchased::String p, count() n
FROM shop.history_sessions WHERE clicked IS NOT NULL OR purchased IS NOT NULL GROUP BY c, p LIMIT 6;
```

## 2. Valores y % de context.device, context.region y context.page

Sobre `shop.history_sessions` (2.000.000 filas, 60 días). Device casi repartido a tres bandas iguales;
Andalucía y Catalunya encabezan regiones; el 56 % de las sesiones antiguas se muestran en página de producto.

- **device**: ios 33,44 % · web 33,43 % · android 33,13 %
- **region**: Andalucía 20,90 · Catalunya 19,07 · Madrid 16,53 · C. Valenciana 12,65 · Galicia 6,99 ·
  Castilla y León 6,09 · País Vasco 5,82 · Canarias 4,97 · Murcia 3,50 · Aragón 3,47 (%)
- **page**: product 55,96 % · category 24,01 % · home 20,03 %

```sql
SELECT context.device::String v, round(100*count()/sum(count()) OVER (),2) pct
FROM shop.history_sessions GROUP BY v ORDER BY pct DESC;   -- lo mismo con context.region y context.page
```

## 3. shop.requests de la última hora

1 hora = 10.802 peticiones → **180 req/min = 3/s** (coincide con el reto).

- **% con `is_new_user = true`: 29,79 %** ← importante para cold start de compradores: uno de cada tres.
- **% con `recent_views` vacío: 19,82 %** (casi 1 de cada 5 entra sin últimas vistas: para ellos co-visitas no dice nada y hacen falta populares por contexto o por embedding).

```sql
SELECT count() total,
  round(100*countIf(is_new_user::Bool = true)/count(),2) pct_nuevos,
  round(100*countIf(recent_views IS NULL OR length(recent_views)=0)/count(),2) pct_sin_vistas
FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR;
SELECT round(count()/60.0,1) req_por_min, max(ts::DateTime64(3)) ultimo
FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR;
```

## 4. shop.catalog_updates hasta ahora (1.593 filas)

| change | n | % |
|---|---|---|
| price_change | 1.241 | 77,9 |
| out_of_stock | 144 | 9,0 |
| back_in_stock | 108 | 6,8 |
| new_product | 100 | 6,3 |

Las 100 filas `new_product` son de 100 productos distintos → cold start de producto cuenta ya con ellos.

```sql
SELECT change::String v, count() n, round(100*count()/sum(count()) OVER (),1) pct
FROM shop.catalog_updates GROUP BY v ORDER BY n DESC;
SELECT count() n, uniqExact(product_id::String) productos
FROM shop.catalog_updates WHERE change = 'new_product';
```

## 5. Precios por categoría y % de compra por device/región (history_events, últimos 7 días)

Mezcla de productos comprados (63.542 compras en 7 días; eventos en 7 días: view 86,28 %, add_to_cart 9,15 %, purchase 4,57 %).
Precio **medio vs mediano** por categoría (el medio va por encima del mediano: hay colas de caros → cuidado al
usarlo como señal; el mediano representa mejor el "producto típico"):

| categoría | precio medio € | mediano € | compras (7d) |
|---|---|---|---|
| Books & Media | 79,30 | 29,99 | 15.325 |
| Home & Kitchen | 56,72 | 30,99 | 3.321 |
| Fashion | 45,91 | 25,99 | 6.469 |
| Toys & Baby | 37,35 | 28,99 | 8.962 |
| Sports & Outdoors | 33,12 | 12,99 | 6.819 |
| Pets | 32,43 | 20,99 | 3.388 |
| Electronics | 31,05 | 21,99 | 6.940 |
| Home & Garden | 30,63 | 23,99 | 2.179 |
| Computers & Gaming | 28,08 | 18,99 | 5.843 |
| Beauty & Health | 17,24 | 11,99 | 4.296 |

**% de compras cambia poco por device o región** (diferencia máx. ~0,7 puntos: web 4,67 % … android 4,48 %;
por región Andalucía 4,92 % … Aragón 4,20 %). En 7 días hay ~460k eventos por device, así que las diferencias
son medidas sobre muestras grandes pero pequeñas en la práctica: el contexto explica a quién le muestras,
no tanto si compra.

```sql
-- tipos de evento y % (7 días)
SELECT event_type::String t, count() n, round(100*count()/sum(count()) OVER (),2) pct
FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY GROUP BY t ORDER BY n DESC;

-- precio medio y mediano por categoría (compras de 7 días, categoría del catálogo actual)
SELECT c.cat, round(avg(e.p),2) precio_medio, round(quantile(0.5)(e.p),2) precio_mediano, count() n
FROM
  (SELECT product_id::String pid, price_eur::Float64 p
   FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY AND event_type = 'purchase') e
INNER JOIN
  (SELECT product_id::String pid, category::String cat FROM shop.catalog) c
ON e.pid = c.pid
GROUP BY c.cat ORDER BY precio_medio DESC;

-- % de compras por device (y lo mismo por region)
SELECT device::String v, count() eventos, countIf(event_type = 'purchase') compras,
       round(100*countIf(event_type='purchase')/count(),2) pct_compra
FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY GROUP BY v ORDER BY pct_compra DESC;
```

## Ideas que salen de aquí (para DECISIONES)

1. ~30 % de compradores nuevos: la pieza "populares por contexto" (D3) pesa bastante, no es un caso raro.
2. En history_sessions, `clicked`/`purchased` son product_ids: los co-visitas de Ranking pueden usar
   directamente `purchased::String != ''`.
3. Desempate por ingreso: precio mediano por categoría (tabla de arriba) para no sobreponderar caros atípicos.
4. Stock se mueve (144 out_of_stock / 108 back_in_stock ya): el filtro de disponibilidad tiene que leer
   `catalog_updates`/`is_active` en vivo, no vale el catálogo congelado.

## 6. Recompras: la señal que cambió el filtro de comprados (sáb ~17:45)

Pregunta: ¿las compras del carrusel son de productos que el usuario YA había comprado antes?
Respuesta medida: **sí, dominan** — con la definición estricta de Evaluación, **el 100 %** de las compras
del test (3.530 de 3.530) son de un producto que el mismo usuario ya había comprado en `shop.events`
antes del corte; con la definición amplia (cualquier compra previa en 60 días, sesiones de los últimos
7 días), **99,0 %** (3.231 de 3.263). El anuncio de los organizadores de las ~17:40 (el modelo oculto
premia recomprar) confirma lo que esto ya decía: **el filtro "no repetir lo comprado" destruye aciertos
y tiene que estar APAGADO** (como parámetro, no como regla).

⚠️ Pendiente de reconciliar con Evaluación: su cifra fue 61,6 % (posible diferencia de definición o de
fuente de `comprados`); las dos consultas están aquí abajo para replicar cualquiera.

```sql
-- Definición estricta (la de Evaluación): compra del test posterior al corte cuyo (user, producto)
-- ya aparece como compra en shop.events ANTES del corte. Resultado: 3.530 compras, 100,0 % con previa.
SELECT count() AS compras_test, countIf(h.u IS NOT NULL) AS con_previa,
       round(100*countIf(h.u IS NOT NULL)/count(), 1) AS pct_recompra
FROM (SELECT user_id::String AS u, purchased::String AS p
      FROM shop.history_sessions
      WHERE ts >= '2026-11-07' AND purchased::String != '') s
LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p
           FROM shop.history_events
           WHERE event_type::String = 'purchase' AND ts < '2026-11-07'
           GROUP BY u, p) h
      ON s.u = h.u AND s.p = h.p;

-- Definición amplia: compras del carrusel de los últimos 7 días cuya misma pareja (user, producto)
-- ya se compró en cualquier momento anterior de los 60 días. Resultado: 3.263 compras, 99,0 % recompras.
SELECT count() AS compras, countIf(h.u IS NOT NULL AND h.primero < s.t) AS recompras
FROM (SELECT user_id::String AS u, purchased::String AS p, ts::DateTime64(3) AS t
      FROM shop.history_sessions
      WHERE ts > now() - INTERVAL 7 DAY AND purchased::String != '') s
LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p, min(ts::DateTime64(3)) AS primero
           FROM shop.history_events WHERE event_type::String = 'purchase' GROUP BY u, p) h
      ON s.u = h.u AND s.p = h.p;
```

Este indicador vive en el panel Tukey (panel "Recompras") para que la decisión del filtro no se revierta
sin datos.
