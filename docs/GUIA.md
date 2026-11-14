# Guía del equipo

Léela de arriba abajo la primera vez. La primera parte explica qué estamos haciendo y por qué. La segunda es la
instalación paso a paso. La tercera responde a las dudas que os van a surgir por el camino. Las reglas para los
agentes de IA están en `AGENTS.md`; esto es para nosotros.

---

# Parte 1 · Entender el proyecto

## Qué nos piden

RawTree, la empresa que patrocina el reto, ha montado una tienda online falsa con unos 5.000 productos repartidos en
diez categorías. En esa tienda, cada vez que alguien entra a la página principal o a la ficha de un producto, ve una
fila de diez productos recomendados: el típico "también te puede interesar". Ahora mismo esa fila enseña siempre lo
mismo, los diez productos más vendidos de la historia. Nuestro trabajo es sustituir eso por algo que venda más.

Lo que lo hace difícil, y a la vez interesante, es que no se entrega un archivo ni un modelo. Lo que entregamos es un
programa que está funcionando en directo. Cada segundo llegan unos tres compradores nuevos, día y noche, y para cada
uno nuestro programa tiene **tres segundos** para decidir qué diez productos verá y en qué orden. Si tarda más, o si
manda algo mal formado (un producto repetido, uno que ya no tiene stock), la tienda enseña los más vendidos de siempre
y ese comprador no nos suma nada.

Los compradores no son personas reales. Son un simulador con un modelo oculto que decide, según quién es el comprador
y qué le enseñamos, si hace clic y si compra. Nadie fuera de RawTree sabe cómo funciona ese modelo por dentro. Lo que
sí tenemos son pistas: dos meses de historial de la tienda y, mientras el programa está en marcha, la información de
qué ha pasado con cada recomendación que hicimos.

## Cómo se gana

La nota tiene dos partes. Setenta puntos los pone una máquina mirando lo que hace nuestro programa entre las **10:00
y las 12:00 del domingo**, que es la única ventana que cuenta. Todo lo anterior, desde el sábado a las 10:00, es
práctica: hay una clasificación pública que se actualiza cada diez minutos y nos sirve para saber si vamos bien, pero
no puntúa. Los otros treinta puntos los pone un jurado el domingo por la tarde, después de una presentación corta
con demo.

De esos setenta puntos automáticos, la mayor parte (cincuenta) mide cuánto vendemos. No cuenta las compras reales,
que dependen de la suerte, sino la probabilidad de compra que el simulador asigna a lo que enseñamos. Esa medida va
de 0 a 100. Un 0 es hacerlo igual de bien que "los más vendidos de siempre". Un 100 es hacerlo tan bien como el
propio modelo oculto, que sabe exactamente qué quiere cada comprador. (Ojo: los organizadores se contradicen sobre
qué es exactamente ese 0. El README dice "los más vendidos de siempre"; las diapositivas de la apertura, "los más
vendidos de la categoría del último producto que miró el comprador". Lo preguntamos en el stand.) Otros diez puntos miden lo mismo, pero solo con
compradores sin historial (usuarios nuevos, o gente que mira productos recién añadidos). A esto se le llama **cold
start**, "arranque en frío", porque no hay nada previo en lo que apoyarse. Los últimos diez miden la **fiabilidad**:
qué porcentaje de compradores recibió una respuesta válida a tiempo.

El jurado premia cuatro cosas:

- Que hayamos comprobado nuestras ideas con datos del pasado antes de ponerlas en directo.
- Que el sistema aprenda de lo que va pasando en vivo.
- Que usemos bien la base de datos de RawTree.
- Que sepamos explicarlo de forma que lo entienda alguien sin formación técnica.

Hay una norma que conviene tener muy presente: **lo que no sepamos explicar puede penalizar**. Podemos usar toda la
IA que queramos, pero cada cosa que presentemos tiene que tener detrás a alguien del equipo que la entienda.

## Qué es un recomendador, sin tecnicismos

Un recomendador intenta responder a una sola pregunta: de todo lo que hay en la tienda, ¿qué es lo más probable que
compre esta persona ahora mismo? Si un comprador lleva diez minutos mirando zapatillas de correr, enseñarle la
televisión más vendida de la tienda es tirar una posición. Enseñarle unos calcetines técnicos, o las zapatillas que
compró la gente que miró las mismas que él, tiene mucho más sentido.

El orden también cuenta, y mucho. La gente mira sobre todo las primeras posiciones de la fila y casi nunca llega a la
décima. Así que no basta con acertar qué diez productos enseñar: lo mejor tiene que ir arriba.

Nuestra estrategia es una escalera. Cada peldaño se comprueba antes de subir al siguiente:

1. **El programa oficial funcionando sin caerse.** Todavía no vende más, pero ya suma los puntos de fiabilidad.
2. **"Quien vio esto, vio también aquello"** (en la jerga, co-visitas): usar lo que el comprador acaba de mirar para
   recomendarle lo que miraron o compraron otros con el mismo interés.
3. **Lo que está de moda ahora**, no hace dos meses. Y nunca enseñar algo agotado ni algo que esa persona ya compró.
4. **Los compradores y productos nuevos**: para un usuario nuevo, lo que suele gustar a gente con su mismo móvil y
   región. Para un producto nuevo, buscar productos con una descripción parecida y tratarlo como a ellos.
5. **Aprender de los resultados**: si algo que recomendamos convierte bien, subirlo; si no, bajarlo, y de vez en
   cuando probar algo nuevo por si acaso.

Es importante saber que la propia organización da una pista con mala intención: **"los equipos que generalizan ganan
a los que memorizan"**. Memorizar es aprender "el producto 91aa se vende mucho". Generalizar es aprender "a quien mira
portátiles le interesan los ratones de gama media". Lo primero se rompe el domingo, porque la ronda final usa
compradores, productos y modas nuevos. Lo segundo sigue funcionando.

## Cómo sabemos si una idea es buena antes de arriesgarla

Esta es la parte más delicada, y vale diez puntos del jurado. Tenemos dos meses de historial, pero lo generó el
sistema antiguo, que enseñaba casi siempre los productos populares. Si medimos una idea nueva con ese historial tal
cual, saldrán ganando las ideas que se parezcan al sistema antiguo, no las buenas. Es como juzgar a un cocinero nuevo
solo con las reseñas de los platos que hacía el anterior.

Para corregirlo, el historial trae una columna con la probabilidad que tenía el sistema antiguo de enseñar cada
producto. Con ella se aplica una técnica llamada **IPS** (*inverse propensity scoring*): un acierto con un producto
que el sistema antiguo casi nunca enseñaba cuenta mucho más que uno con un producto que enseñaba siempre, porque es
una prueba más fiable de que gusta de verdad. No hace falta saber la fórmula: el cuaderno oficial
`starter/offline_eval.ipynb` ya la calcula. Lo que hay que hacer es pasar ahí cada idea y compararla con el sistema
de partida antes de ponerla en directo.

## Cómo trabajamos los cuatro

Vamos a trabajar cada uno con un agente de IA: tres con Claude Code y el resto con OpenCode usando los modelos
gratuitos de HelmCode. Un agente es una IA que, además de hablar, puede leer los archivos del proyecto, escribir
código y ejecutar comandos en tu ordenador. Es muy potente, pero tiene una limitación que lo condiciona todo: **no
recuerda nada de una sesión a otra, y no sabe lo que están haciendo los agentes de los demás**. Si Alex descubre a
las cinco de la tarde que una columna viene en un formato raro y no lo escribe en ningún sitio, el agente de Leonardo
se va a pegar con el mismo problema a las seis.

Por eso el repositorio no guarda solo el código: es también la memoria compartida del equipo.

- **`AGENTS.md`** contiene las reglas que siguen todos los agentes, sean de la herramienta que sean.
- **`docs/estado/`** tiene un archivo por persona donde cada uno cuenta qué está haciendo, qué ha terminado, qué ha
  descubierto y qué necesita de otro.
- **`docs/DECISIONES.md` y `docs/EXPERIMENTOS.md`** guardan qué decidimos y qué números salieron. Nos sirven a
  nosotros y serán la base de la presentación.

Cada agente lee todo eso al empezar. El ciclo es siempre el mismo:

1. Al empezar, escribes `/empezar` con tu nombre: el agente baja lo último y te resume qué hacen los demás.
2. Trabajas en tareas pequeñas.
3. Al terminar algo, escribes `/handoff` con tu nombre: el agente actualiza tu estado, guarda los cambios y te da dos
   líneas para pegar en el chat del equipo.

Hay cuatro papeles:

- **Operador**: el único que tiene el programa en marcha en su portátil. Cuida que no se caiga y decide qué entra en
  producción.
- **Ranking**: mejora la parte que elige los diez productos.
- **Evaluación**: mide cada idea con el historial antes de que llegue al Operador.
- **Datos y relato**: explora los datos, prepara lo de los productos nuevos y va montando la presentación desde el
  sábado, no el domingo por la mañana.

Que solo una persona ejecute el programa en directo no es un capricho. Si dos lo lanzan a la vez con la clave del
equipo, la tienda recibe dos respuestas para cada comprador, y la norma es una por comprador.

## Las herramientas, en una frase cada una

- **Git y GitHub**: el sistema que guarda todas las versiones del proyecto y nos deja trabajar a la vez sin pisarnos.
- **RawTree**: la base de datos donde están todos los datos de la tienda. Se consulta con SQL, el lenguaje estándar
  para preguntar cosas a una base de datos. No tenéis que saber SQL: el agente lo escribe por vosotros, conectado a
  RawTree a través de un "enchufe" llamado MCP que ya está configurado.
- **El starter kit** (`starter/`): lo que nos dieron los organizadores. Un programa de unas 120 líneas que ya lee
  compradores y envía respuestas, un archivo con consultas de ejemplo y el cuaderno de evaluación. Nuestra mejora
  consiste, sobre todo, en cambiar una función de ese programa, `recommend()`, que hoy devuelve siempre los más
  vendidos.
- **La skill de RawTree** (`.claude/skills/rawtree/`): un manual oficial que RawTree ha escrito para que los agentes
  sepan usar su base de datos sin equivocarse. Los agentes lo consultan solos cuando la tarea lo necesita.
- **OpenCode y Claude Code**: los agentes. Los dos leen las mismas reglas y tienen los mismos comandos.

RawTree organiza además un premio aparte, "el mejor uso de RawTree por un agente", abierto a todos los equipos. Lo
dejamos en el cajón hasta que el recomendador esté estable. Si sobra tiempo, puede ser un buen extra.

---

# Parte 2 · Ponerlo en marcha

## Lo básico en tu portátil
Necesitas Git y Python 3.11 o superior. Compruébalo con `git --version` y `python --version` (en Windows también
`py --version`). Si falta alguno: Git en https://git-scm.com y Python en https://python.org. En Windows, al instalar
Python marca la casilla "Add Python to PATH".

Lester te invitará al repositorio. Acepta la invitación que te llega al correo de GitHub y luego:
```bash
git clone https://github.com/<usuario-de-lester>/datathon-fme-2026.git
cd datathon-fme-2026
git config core.hooksPath .githooks
```
La última línea activa un pequeño guardián que impide subir por error claves o datos. Se hace una vez.

Copia `.env.example` a un archivo llamado `.env` y rellena el `TEAM_ID` y la `RAWTREE_API_KEY` que da el stand de
RawTree. Esa clave se pasa por privado, nunca por un canal del Discord, y nunca se sube al repositorio.

Después prepara el entorno de Python. Un entorno virtual es una carpeta donde se instalan las librerías de este
proyecto sin mezclarse con las del resto de tu ordenador:
```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell
pip install -r requirements.txt
python -m src.validar              # debe decir "todos los autotests pasan"
```
Si en Windows PowerShell no te deja activar el entorno, ejecuta una vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` y vuelve a intentarlo.

## Si usas OpenCode
Instálalo con `curl -fsSL https://opencode.ai/v2/install | bash` en Mac o Linux. En Windows, descarga el ejecutable
desde https://opencode.ai.

Antes de abrirlo, carga las variables del `.env` en la terminal. Si no lo haces, el enchufe a RawTree no tendrá
clave y fallará sin explicar bien por qué:

- Mac o Linux:
  ```bash
  set -a; source .env; set +a; opencode
  ```
- Windows PowerShell:
  ```powershell
  Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim()) } }; opencode
  ```

Dentro de OpenCode, conecta HelmCode y comprueba que responde:

1. Escribe `/connect` y elige otro proveedor.
2. Pon como identificador **exactamente** `helmcode`.
3. Pega la clave del anuncio de HelmCode.
4. Con `/models`, elige **GLM 5.3 Flash** y escríbele "hola" para comprobar que responde.

El resto (modelos, conexión con RawTree y permisos) ya viene configurado en `opencode.json`.

Usa GLM 5.3 Flash para casi todo. Si va lento o da el error 429 (que significa "demasiadas peticiones", porque la
clave la comparte todo el datathon), cambia a DeepSeek V4 Flash. Qwen 3.6 y Gemma 4 sirven para preguntas rápidas.

## Si usas Claude Code
Instálalo con `curl -fsSL https://claude.ai/install.sh | bash` en Mac o Linux, o con
`irm https://claude.ai/install.ps1 | iex` en Windows PowerShell. Escribe `claude` e inicia sesión con tu cuenta.

Con las variables del `.env` cargadas como en el apartado anterior, conecta RawTree una vez:
```bash
claude mcp add --transport http rawtree https://mcp.rawtree.com/mcp --header "Authorization: Bearer $RAWTREE_API_KEY"
```
La primera vez que abras `claude` en la carpeta del proyecto te preguntará si confías en ella. Di que sí: así se
activan los permisos del equipo.

## Cómo pedirle cosas al agente
Pídele una tarea pequeña y concreta cada vez, di qué archivo tocar si lo sabes, y pide siempre que compruebe lo que
ha hecho ejecutando algo. Unos ejemplos para adaptar:

- *"Usando el MCP rawtree, dime cuántos productos hay por categoría y el precio medio de cada una. Solo consulta, no
  escribas código."*
- *"Explícame qué hace starter/recommender.py paso a paso, como a alguien que nunca ha usado una API."*
- *"En src/ranking.py añade un filtro para no recomendar lo que el usuario ya compró. Antes de editar, dime el plan
  en tres líneas. Después ejecuta python -m src.validar."*
- *"/eval de la idea coview_v2 contra el baseline."*

Si el agente se lía, dile que pare y que resuma en cinco líneas qué intentaba y qué ha fallado. Si después de
corregirle dos veces sigue mal, abre una sesión nueva con una petición mejor escrita: insistir en la misma
conversación suele empeorarlo.

---

# Parte 3 · Dudas que van a salir

**En Windows, `pip` dice "No module named pip" o falta `requests` aunque lo instalaste.** Tienes varios Python y
`python` abre el que no es (típico si instalaste MSYS2 para C++). Crea el entorno con el lanzador oficial:
`py -m venv .venv`, actívalo y a partir de ahí usa siempre `python -m pip install ...`. Con `(.venv)` delante de la
línea de la terminal, `python` ya es el correcto.

**En Windows PowerShell, una llamada a la API da "Invalid request body" o "Internal Server Error".** PowerShell
estropea las comillas del JSON. No es la clave. Guarda el JSON en un archivo y envíalo con
`curl.exe ... --data-binary "@body.json"`, o deja que lo haga el agente con Python.

**Qué son commit, rama, pull y push.** Un commit es una foto guardada del proyecto con un mensaje que dice qué
cambió. Una rama es una línea de trabajo paralela: tú trabajas en la tuya sin tocar la de los demás, y cuando algo
funciona se une a la principal (`main`). `pull` baja a tu ordenador lo que han subido los demás. `push` sube lo tuyo.

**Me sale un conflicto al hacer pull.** Significa que dos personas han cambiado la misma parte de un archivo y Git no
sabe con cuál quedarse. No borres nada. Dile al agente qué archivo es y pídele que te explique las dos versiones antes
de decidir. Para que pase poco, cada uno tiene su propio archivo de estado.

**El commit dice BLOQUEADO.** Es el guardián: ha visto una clave, el `.env` o un archivo de datos. Sácalo del commit
con `git restore --staged <archivo>` o mueve la clave al `.env`.

**¿Puedo ejecutar `starter/recommender.py` para probar?** No. Solo el Operador. Para probar ideas está el cuaderno de
evaluación, que usa datos del pasado y no envía nada a la tienda.

**El agente quiere leer el `.env`.** No le dejes. Los permisos ya se lo impiden. Si insiste, la tarea está mal
planteada.

**Una consulta da "Unknown expression identifier".** O esa columna no se llama así o la tabla todavía está vacía.
Pide al agente un `DESCRIBE` de la tabla. Y recuerda que en RawTree, al cruzar tablas, hay que indicar el tipo:
`product_id::String`.

**¿Cómo sabemos si vamos bien?** Durante la práctica, en la clasificación pública
(https://datathon.rawtree.dev/leaderboard). Siempre, con `/check-live`, que lee la tabla privada donde RawTree nos
cuenta qué pasó con cada recomendación. El domingo en la final la clasificación se apaga y solo tendremos lo segundo.

**El programa se ha caído.** Avisa al Operador. Mientras esté caído, los compradores ven los más vendidos: no rompe
nada, pero no suma.

**¿Qué pasa por la noche?** Los compradores siguen llegando, pero no cuenta. Nos sirve para comprobar que el programa
aguanta horas solo. El Operador organiza los turnos.

**¿Se puede tocar el código durante la final?** Sí, pero a ciegas. Solo cambios pequeños y comprobados.

**No entiendo lo que ha hecho el agente.** Entonces no se sube. Pídele que te lo explique hasta que lo entiendas. Si
nadie del equipo puede explicarlo, al jurado tampoco.
