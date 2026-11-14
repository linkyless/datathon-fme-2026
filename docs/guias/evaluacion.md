# Guía de Evaluación (Alex · Claude Code)

**Tu misión:** decir con números, ANTES de ir en vivo, si una versión vende más que el baseline. Nada pasa a
producción sin tu medición. Vales 10 puntos del jurado ("método validado offline") y eres quien evita que
subamos algo peor.

**Tus archivos:** `src/evaluar.py`, `src/panel.py`, `docs/EXPERIMENTOS.md`.
**No toques:** `src/ranking.py` ni las piezas. Tú las mides tal cual están en `main`.

**Antes de empezar:** sigue la Parte 2 de `docs/GUIA.md`, lee `docs/guias/README.md` (el contrato) y recorre
`starter/offline_eval.ipynb` entero con tu agente: *"explícame este cuaderno celda a celda, y qué es IPS y SNIPS,
como a alguien que sabe probabilidad pero no ML"*.

**Con quién te cruzas (y con nadie más):**
- **Leonardo y Artai** te avisan cuando hay algo que medir. Tú mides lo que hay en `main` y devuelves números.
  No editas su código: si algo está mal, se lo dices.
- **Lester** recibe tu veredicto ("apta / no apta") y decide si entra en vivo. Si `src/catalogo.py` falla (tarea
  O1), díselo y mientras tanto escribe el resto de `src/evaluar.py`.

---

## E1 · Convertir el cuaderno en `python -m src.evaluar` — hasta las 14:45
Puedes empezar ya; lo único que necesitas del Operador es que `src/catalogo.py` funcione (tarea O1, ~13:45).
Pega esto a tu agente (usa plan mode, Shift+Tab):
> Lee docs/guias/README.md, src/estado.py, src/ranking.py y starter/offline_eval.ipynb. Crea src/evaluar.py, que
> reproduzca el cuaderno pero usando NUESTRO contrato:
> 1. Corte `CUT` igual que el cuaderno (último día de shop.history_sessions menos 7 días).
> 2. Muestra de test: 100.000 filas de shop.history_sessions con ts >= CUT. Descárgala UNA vez y guárdala en
>    data/test_<CUT>.pkl (data/ no se sube); las siguientes veces léela de ahí, para comparar siempre con las
>    mismas filas.
> 3. Estado offline: `src.estado.construir(hasta=CUT)`, guardado en data/estado_<CUT>.pkl. Opción
>    `--recalcular` para rehacerlo cuando cambie alguna pieza.
> 4. Dos políticas: "baseline" = `ranking.recommend` con un Estado que solo tiene catalogo, disponibles y
>    populares (el resto vacío); "actual" = `ranking.recommend` con el Estado completo. Convierte cada fila en
>    un diccionario con request_id, ts, user_id, context y recent_views (comprueba con DESCRIBE su formato).
> 5. Métricas del cuaderno (hit_rate@10, IPS y SNIPS del slot 1, slot1_matches) y además purchase_hit@10
>    (¿lo comprado está en nuestros 10?). Para todo el test y para el segmento frío (recent_views vacío).
> 6. Imprime una tabla y el `ranking.NOMBRE` medido.
> Ejecútalo y enséñame la tabla.

**Hecho cuando:** sale la tabla y, con las piezas aún vacías, "baseline" y "actual" dan **exactamente lo mismo**
(es la prueba de que el evaluador es correcto). Apunta esa fila en `docs/EXPERIMENTOS.md`. Commit y push.

## E2 · Medir cada versión que te pase Ranking — toda la tarde
Cuando Ranking avise ("mide v1-covisitas"):
```powershell
git pull --rebase
python -m src.evaluar --recalcular
```
Apunta una fila en `docs/EXPERIMENTOS.md` con los números tal cual y escribe en el chat: *"v1-covisitas:
hit_rate X vs Y del baseline, slot1_matches Z → apta / no apta"*. Apta = mejor que el baseline en hit_rate y
purchase_hit, y SNIPS no peor. Si `slot1_matches` es pequeño (< 300), dilo: el SNIPS es ruido.

## E3 · El panel en vivo: nuestro leaderboard para la final — de 16:00 a 19:00
En la final no hay leaderboard público: esto será lo único que veamos. Pega a tu agente:
> Crea src/panel.py (`python -m src.panel`): une team_29.feedback con team_29.submissions por request_id (para
> saber el model_version) y muestra, por model_version y por tramos de 15 minutos de las últimas 3 horas:
> peticiones, % servido por nosotros, clics por 100, compras por 100, ingreso por petición y latencia p95.
> Ignora la fila request_id = '__init__'. Mira las consultas 5 y 6 de starter/queries.sql.

**Hecho cuando:** enseña las filas del baseline y de la versión que esté en vivo. Commit y push.

## E4 · ¿Es casualidad? — de 19:00 a 22:00
Pega a tu agente: *"Añade a src/evaluar.py un intervalo de confianza del 95 % por bootstrap (200 remuestreos)
para la diferencia de hit_rate y purchase_hit entre baseline y actual. Explícame qué significa."* Esto es oro para
el jurado: demuestra que la mejora no es suerte.

---

## Fase 2 (tras el deep research)
_Vacío. Aquí irán métodos de evaluación más finos (más slots con IPS, A/B en vivo...). No empezar hasta que esté escrito._
