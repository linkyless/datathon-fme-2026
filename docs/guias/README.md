# Reparto del trabajo: léelo antes de abrir tu guía

Cuatro personas, cuatro roles, **cero archivos compartidos**. Cada uno tiene su guía con tareas en orden; cada tarea
dice qué pegarle a tu agente, cómo saber que está hecha y para qué hora.

| Rol | Persona | Herramienta | Guía |
|---|---|---|---|
| Operador | Lester | Claude Code | [operador.md](operador.md) |
| Ranking | Leonardo | Claude Code | [ranking.md](ranking.md) |
| Evaluación | Alex | Claude Code | [evaluacion.md](evaluacion.md) |
| Datos y relato | Artai | OpenCode (GLM) | [datos.md](datos.md) |

**Primer mensaje a tu agente, siempre:** `/empezar <tu nombre>` y después
*"Lee docs/guias/<tu guía>.md y hagamos la siguiente tarea que no esté hecha. Antes de tocar nada, explícame en
5 líneas qué vamos a hacer y por qué."* El agente trabaja; tú compruebas el "Hecho cuando" y lo entiendes.

**Todo en Python.** Si al final algo va lento, se reescribe esa pieza en C++/Rust, pero no antes.

## Cómo encaja todo (el contrato)

```
            ┌──────────────── src/estado.py: Estado (todo lo que se sabe, en memoria) ────────────────┐
            │ catalogo, disponibles, populares │ covisitas, comprados │ populares_contexto, vecinos │
            │        OPERADOR (catalogo.py)    │ RANKING (covisitas.py,│  DATOS (coldstart.py)       │
            │                                  │          usuarios.py) │                             │
            └──────────────────────────────────┴──────────┬───────────┴─────────────────────────────┘
                                                          ▼
   petición de shop.requests ──► src/ranking.py: recommend(peticion, estado) ──► 10 product_id   (RANKING)
                                                          │
             en vivo: src/servicio.py lo llama ~3 veces/s (OPERADOR)
             offline: src/evaluar.py lo llama sobre el historial y lo compara con el baseline (EVALUACIÓN)
```

- Cada pieza del `Estado` se rellena con una función `cargar_xxx(hasta)`. **Ya existen todas, vacías**: devuelven
  `{}` y `recommend()` funciona igual (se comporta como el baseline). Tu trabajo es rellenar las tuyas.
- `hasta='YYYY-MM-DD'` = usar solo datos anteriores a esa fecha (para evaluar offline sin mirar el futuro).
  `hasta=None` = todo (en vivo). **Toda función `cargar_` tiene que respetarlo.**
- `recommend()` nunca consulta RawTree, tarda < 5 ms y siempre devuelve 10 IDs de `estado.disponibles`.
- ¿Necesitas un campo nuevo en el `Estado`? Escríbelo en tu `docs/estado/<nombre>.md` → "Necesito de alguien":
  *"Operador: añade `tendencias: list[str]`, la rellena `cargar_tendencias()` en src/tendencias.py"*. Lo añade él.

## Quién es dueño de cada archivo (solo el dueño lo edita)

| Archivos | Dueño |
|---|---|
| `src/rawtree.py`, `src/catalogo.py`, `src/estado.py`, `src/servicio.py`, `src/prueba_local.py` | Operador |
| `src/ranking.py`, `src/covisitas.py`, `src/usuarios.py`, `src/tendencias.py` | Ranking |
| `src/evaluar.py`, `src/panel.py`, `docs/EXPERIMENTOS.md` | Evaluación |
| `src/coldstart.py`, `src/embeddings.py`, `docs/datos.md`, `docs/presentacion.md` | Datos y relato |
| `docs/estado/<nombre>.md` | cada uno el suyo |
| `docs/DECISIONES.md` | todos, **solo añadiendo filas al final** |
| `AGENTS.md`, `docs/guias/`, `src/validar.py` | Lester (avisando al grupo) |
| `starter/` | nadie (es el oficial) |

Si tu agente quiere tocar un archivo que no es tuyo: **para** y pídeselo al dueño.

## El ciclo de cada tarea (igual para los cuatro)

```powershell
git pull --rebase                 # 1. bajar lo último
# 2. trabajar con tu agente en TUS archivos
python -m src.prueba_local        # 3. no he roto nada (sin red, 1 segundo)
git add src/mi_archivo.py docs/estado/minombre.md
git commit -m "covisitas: pesos por tipo de evento"
git pull --rebase                 # 4. por si alguien subió algo mientras tanto
git push                          # 5. subir
```
Como nadie comparte archivos, trabajamos directamente en `main`. **Que algo esté en `main` no significa que esté en
vivo**: solo pasa a producción cuando Evaluación lo ha medido y el Operador reinicia el servicio.

## Puntos de control (10 minutos, los cuatro)
**15:00 · 18:00 · 21:00 · Dom 07:30.** Cada uno dice: qué ha terminado, qué número ha salido, qué le bloquea.
El Operador decide qué versión pasa a vivo.

## Fase 2 (después del deep research)
Las ideas avanzadas (estructuras de datos, modelos, bandits…) llegarán como una sección nueva en cada guía.
**Hasta que estén escritas ahí, no se empiezan.** Primero, la fase 1 funcionando y medida.
