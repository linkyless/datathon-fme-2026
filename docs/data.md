# Data: what we know about the tables

Queries run on **Sat 14-11-2026 at about 14:00 Barcelona time** (RawTree `ts` values are in UTC).
Channel: the RawTree HTTP API through `src.rawtree.query` (the same client the service uses; the MCP wasn't exposed
in that agent session, but it's the same API). Each query sits below its answer. Reminder: every column is `Dynamic`,
hence the casts (`::String`, `::Bool`, `::Float64`).

---

## 1. Columns of each table (DESCRIBE)

| Table | Columns (all `Dynamic`) |
|---|---|
| `shop.catalog` | brand, category, created_at, description, is_active, price_eur, product_id, subcategory |
| `shop.history_sessions` | clicked, context.device, context.page, context.region, propensity, purchased, recent_views, request_id, revenue_eur, shown, ts, user_id |
| `shop.history_events` | device, event_type, price_eur, product_id, region, session_id, ts, user_id |
| `shop.requests` | context.device, context.page, context.region, is_new_user, recent_views, request_id, ts, user_id |
| `shop.events` | device, event_type, price_eur, product_id, region, session_id, ts, user_id (same as history_events) |
| `shop.catalog_updates` | brand, category, change, created_at, description, is_active, price_eur, product_id, subcategory, ts (full catalog + ts + change) |

**Short answer: `history_sessions` does NOT have `is_new_user`** (it's only in `shop.requests`, type `Bool`).
**It DOES have `context.page`** (home/category/product), like `shop.requests`.

Measured sizes and types:

- `shop.history_events`: 11,996,560 rows, range `2026-09-15` -> `2026-11-14` (60 days).
- `shop.history_sessions`: 2,000,000 rows.
- Concrete types (`dynamicType`): `is_new_user` = `Bool`; `recent_views` = mostly `Array(JSON)` (88 %) and
  `Array(Nullable(String))` (20 % of the last hour's rows): `length(recent_views)` works the same in both cases.
- **Important finding:** in `history_sessions`, `clicked` and `purchased` are NOT true/false: they hold the
  `product_id` that was clicked / bought (empty string `""` when nothing happened). `propensity` goes with `shown`
  (10 values). For "did they buy?" use `purchased::String != ''`.

```sql
DESCRIBE TABLE shop.history_sessions SETTINGS describe_include_subcolumns = 1;   -- and the same for the other 5
SELECT min(ts::DateTime64(3)), max(ts::DateTime64(3)), count() FROM shop.history_events;
SELECT dynamicType(is_new_user) t, count() n FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR GROUP BY t;
SELECT dynamicType(recent_views) t, count() n FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR GROUP BY t;
SELECT clicked::String c, purchased::String p, count() n
FROM shop.history_sessions WHERE clicked IS NOT NULL OR purchased IS NOT NULL GROUP BY c, p LIMIT 6;
```

## 2. Values and % of context.device, context.region and context.page

On `shop.history_sessions` (2,000,000 rows, 60 days). Device is split almost evenly three ways; Andalucía and
Catalunya lead the regions; 56 % of old sessions are shown on a product page.

- **device**: ios 33.44 %, web 33.43 %, android 33.13 %
- **region**: Andalucía 20.90, Catalunya 19.07, Madrid 16.53, C. Valenciana 12.65, Galicia 6.99,
  Castilla y León 6.09, País Vasco 5.82, Canarias 4.97, Murcia 3.50, Aragón 3.47 (%)
- **page**: product 55.96 %, category 24.01 %, home 20.03 %

```sql
SELECT context.device::String v, round(100*count()/sum(count()) OVER (),2) pct
FROM shop.history_sessions GROUP BY v ORDER BY pct DESC;   -- same with context.region and context.page
```

## 3. shop.requests in the last hour

1 hour = 10,802 requests -> **180 req/min = 3/s** (matches the challenge).

- **% with `is_new_user = true`: 29.79 %**, important for shopper cold start: one in three.
- **% with empty `recent_views`: 19.82 %** (almost 1 in 5 arrive with no recent views: co-visitation says nothing for
  them, so they need context or embedding-based best sellers).

```sql
SELECT count() total,
  round(100*countIf(is_new_user::Bool = true)/count(),2) pct_new,
  round(100*countIf(recent_views IS NULL OR length(recent_views)=0)/count(),2) pct_no_views
FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR;
SELECT round(count()/60.0,1) req_per_min, max(ts::DateTime64(3)) last_ts
FROM shop.requests WHERE ts > now() - INTERVAL 1 HOUR;
```

## 4. shop.catalog_updates so far (1,593 rows)

| change | n | % |
|---|---|---|
| price_change | 1,241 | 77.9 |
| out_of_stock | 144 | 9.0 |
| back_in_stock | 108 | 6.8 |
| new_product | 100 | 6.3 |

The 100 `new_product` rows are 100 distinct products, so product cold start already has them to work with.

```sql
SELECT change::String v, count() n, round(100*count()/sum(count()) OVER (),1) pct
FROM shop.catalog_updates GROUP BY v ORDER BY n DESC;
SELECT count() n, uniqExact(product_id::String) products
FROM shop.catalog_updates WHERE change = 'new_product';
```

## 5. Prices per category and purchase % by device/region (history_events, last 7 days)

Mix of purchased products (63,542 purchases in 7 days; events in 7 days: view 86.28 %, add_to_cart 9.15 %,
purchase 4.57 %). **Mean vs median** price per category (the mean is above the median: there are long tails of
expensive products, so be careful using it as a signal; the median represents the "typical product" better):

| category | mean price EUR | median EUR | purchases (7d) |
|---|---|---|---|
| Books & Media | 79.30 | 29.99 | 15,325 |
| Home & Kitchen | 56.72 | 30.99 | 3,321 |
| Fashion | 45.91 | 25.99 | 6,469 |
| Toys & Baby | 37.35 | 28.99 | 8,962 |
| Sports & Outdoors | 33.12 | 12.99 | 6,819 |
| Pets | 32.43 | 20.99 | 3,388 |
| Electronics | 31.05 | 21.99 | 6,940 |
| Home & Garden | 30.63 | 23.99 | 2,179 |
| Computers & Gaming | 28.08 | 18.99 | 5,843 |
| Beauty & Health | 17.24 | 11.99 | 4,296 |

**The purchase % barely changes with device or region** (max difference ~0.7 points: web 4.67 % ... android 4.48 %;
by region Andalucía 4.92 % ... Aragón 4.20 %). There are ~460k events per device in 7 days, so the samples are big but
the differences are small in practice: context explains who you show things to, not so much whether they buy.

```sql
-- event types and % (7 days)
SELECT event_type::String t, count() n, round(100*count()/sum(count()) OVER (),2) pct
FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY GROUP BY t ORDER BY n DESC;

-- mean and median price per category (7-day purchases, category from the current catalog)
SELECT c.cat, round(avg(e.p),2) mean_price, round(quantile(0.5)(e.p),2) median_price, count() n
FROM
  (SELECT product_id::String pid, price_eur::Float64 p
   FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY AND event_type = 'purchase') e
INNER JOIN
  (SELECT product_id::String pid, category::String cat FROM shop.catalog) c
ON e.pid = c.pid
GROUP BY c.cat ORDER BY mean_price DESC;

-- purchase % per device (and the same per region)
SELECT device::String v, count() events, countIf(event_type = 'purchase') purchases,
       round(100*countIf(event_type='purchase')/count(),2) pct_purchase
FROM shop.history_events WHERE ts > now() - INTERVAL 7 DAY GROUP BY v ORDER BY pct_purchase DESC;
```

## Ideas that come out of this (for DECISIONS)

1. ~30 % new shoppers: the "context best sellers" piece (D3) matters a lot, it's not an edge case.
2. In history_sessions, `clicked`/`purchased` are product_ids: Ranking's co-visits can use `purchased::String != ''`
   directly.
3. Revenue tie-break: median price per category (table above) so atypical expensive products aren't overweighted.
4. Stock moves (already 144 out_of_stock / 108 back_in_stock): the availability filter has to read
   `catalog_updates`/`is_active` live; a frozen catalog won't do.

## 6. Repurchases: the signal that changed the bought filter (Sat ~17:45)

Question: are carousel purchases of products the user had ALREADY bought before?
Measured answer: **yes, they dominate**. With Evaluation's strict definition, **100 %** of the test purchases (3,530
of 3,530) are of a product the same user had already bought in `shop.events` before the cutoff; with the broad
definition (any earlier purchase in 60 days, sessions from the last 7 days), **99.0 %** (3,231 of 3,263). The
organisers' announcement at ~17:40 (the hidden model rewards repurchasing) confirmed what this already said: **the
"don't repeat what was bought" filter destroys hits and has to be OFF** (as a parameter, not a rule).

Note: still to reconcile with Evaluation, whose figure was 61.6 % (possibly a different definition or source for
`bought`); both queries are below to reproduce either one. (Later that evening the organisers clarified that
repurchases are NOT significant live, and the filter went back on: see docs/EXPERIMENTS.md, Sat 18:10.)

```sql
-- Strict definition (Evaluation's): a test purchase after the cutoff whose (user, product) already appears as a
-- purchase in shop.events BEFORE the cutoff. Result: 3,530 purchases, 100.0 % with an earlier one.
SELECT count() AS test_purchases, countIf(h.u IS NOT NULL) AS with_previous,
       round(100*countIf(h.u IS NOT NULL)/count(), 1) AS pct_repurchase
FROM (SELECT user_id::String AS u, purchased::String AS p
      FROM shop.history_sessions
      WHERE ts >= '2026-11-07' AND purchased::String != '') s
LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p
           FROM shop.history_events
           WHERE event_type::String = 'purchase' AND ts < '2026-11-07'
           GROUP BY u, p) h
      ON s.u = h.u AND s.p = h.p;

-- Broad definition: carousel purchases of the last 7 days whose (user, product) pair had been bought at any earlier
-- point in the 60 days. Result: 3,263 purchases, 99.0 % repurchases.
SELECT count() AS purchases, countIf(h.u IS NOT NULL AND h.first_ts < s.t) AS repurchases
FROM (SELECT user_id::String AS u, purchased::String AS p, ts::DateTime64(3) AS t
      FROM shop.history_sessions
      WHERE ts > now() - INTERVAL 7 DAY AND purchased::String != '') s
LEFT JOIN (SELECT user_id::String AS u, product_id::String AS p, min(ts::DateTime64(3)) AS first_ts
           FROM shop.history_events WHERE event_type::String = 'purchase' GROUP BY u, p) h
      ON s.u = h.u AND s.p = h.p;
```

This indicator lives in the Tukey panel ("Repurchases" panel) so the filter decision doesn't get reverted without data.
