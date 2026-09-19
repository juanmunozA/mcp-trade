# Agente de pronóstico para Polymarket

## Qué es

Un agente que **pronostica y opera mercados de eventos** en Polymarket —
preguntas del tipo *"¿ganará X la elección?"*, *"¿habrá alto el fuego antes
de marzo?"*.

Lee el mercado, busca información en la web, estima su propia probabilidad,
y **apuesta solo cuando su número difiere del precio lo suficiente para
pagar el spread**.

Arranca simulando contra el libro de órdenes real. No toca dinero hasta que
alguien cambie dos variables distintas, a propósito.

---

## El método

**Local-first.** El modelo de lenguaje, la búsqueda y la orquestación corren
en la máquina de quien lo use. Sin cuentas que abrir, sin cuotas que se
agoten a mitad de ciclo.

**Contenerizado.** Un `docker compose up -d` levanta todo.

**Empaquetado como entrega.** El repositorio es privado, así que no se
clona: se recibe una carpeta autocontenida (82 archivos, 144 KB) que genera
un script.

---

## Qué corre local

| servicio | puerto | qué hace |
|---|---|---|
| **agent** | 10000 | la lógica del agente |
| **ollama** | 11434 | los modelos de lenguaje (DeepSeek), en la máquina |
| **searxng** | 8888 | metabuscador propio — Ollama no navega |
| **n8n** | 5678 | orquestación |

---

## Qué está hosteado

**Una sola pieza: la base de datos**, en Neon (postgres gestionado, plan
gratuito).

Se sacó de la máquina porque el contenedor de postgres son ~250 MB de RAM
más una carpeta de datos que crece sin parar. Con Ollama comiéndose casi
toda la memoria, eso puede decidir si el modelo cabe o se va a swap.

A cambio, cada consulta sale a internet y si Neon no responde el ciclo
falla. Para este agente da igual: hace unas pocas escrituras por mercado,
no está en un bucle caliente.

Aparte de eso se **consumen** tres servicios externos (no se hospedan):

| | obligatorio | por qué |
|---|---|---|
| API de Polymarket | sí | es el mercado |
| Buscadores, vía SearXNG | sí | lo local es que nadie cobra ni cuenta peticiones |
| Groq | no | segunda opinión opcional, plan gratuito sin tarjeta |

**Los modelos y el contexto nunca salen de la máquina.**

> Esto reemplazó un montaje entero en la nube — Render, Cloudflare Workers
> AI, Gemini — que se abandonó porque los planes gratuitos se agotaban a
> mitad de ciclo y el disco de Render es efímero. De ahí sobrevivió solo
> Neon.

---

## Qué hace con cada mercado

```
Polymarket · ~800 mercados activos
     ↓ filtro + puntaje (sin IA, sin costo)          GET /candidates
candidatos
     ↓
  ╔═ POLÍTICA · qué mirar ═══════════════╗           n8n, editable
  ║  fuera las categorías mal calibradas ║
  ║  mínimo de días hasta resolución     ║
  ║  ordenar por puntaje, tope por ciclo ║
  ╚══════════════════════════════════════╝
     ↓
SearXNG busca, el modelo destila hechos con fecha    POST /forecast
     ├─ sin hechos ──────────────→ PASS · veto evidencia
     ↓
DeepSeek pronostica → (Groq da segunda opinión si escala)
     ↓
LOS TRES VETOS                                       ← el agente, no editable
     ├─ la pregunta es ambigua ──→ PASS
     ├─ los modelos no coinciden → PASS
     ├─ discrepa +35 puntos ─────→ PASS
     ↓
  ╔═ POLÍTICA · ¿operar? ════════════════╗           n8n, editable
  ║  umbral de divergencia               ║
  ║  ¿exigir segunda opinión?            ║
  ╚══════════════════════════════════════╝
     ├─ no ──────────────────────→ PASS
     ↓
Cotizar · precios reales, se paga el ask             POST /trade
     ↓ Kelly fraccional × 0.20
     ├─ edge insuficiente ───────→ PASS
     ↓
RISK KERNEL · límites duros                          ← el agente, no editable
     ├─ rechaza ─────────────────→ PASS
     ↓
   ORDEN
     ↓ cuando el mercado resuelve
Brier propio vs Brier del mercado ┄→ corrige el sesgo
```

**La mayoría de los mercados terminan en PASS, y eso es correcto.** Un
agente que encuentra oportunidad en todo no está evaluando.

---

## Qué hace n8n: decide el camino

No es un cron con interfaz. El agente expone tres verbos y **n8n decide el
camino entre ellos**:

```
GET  /candidates    qué hay        →  POLÍTICA: qué mercados mirar
POST /forecast      qué opina      →  POLÍTICA: ¿vale la pena operarlo?
POST /trade         actúa
```

Los dos puntos marcados **POLÍTICA** son nodos de código editables desde la
interfaz. Ahí vive lo que antes estaba enterrado en Python:

**Qué mirar** — descartar categorías donde el modelo está mal calibrado,
exigir un mínimo de días hasta la resolución, ordenar por puntaje de
oportunidad, y cuántos analizar por ciclo.

El agente clasifica cada mercado en una de siete: `politica`,
`geopolitica`, `economia`, `empresas`, `deportes`, `cripto`, `otros`. **No
se mira solo política** — las siete entran, y hoy la única excluida es
`cripto`, porque ahí los LLM tienen miscalibración severa y retornos
negativos medidos.

**¿Operar?** — el umbral de divergencia por debajo del cual no compensa
pagar el spread, y si se exige o no una segunda opinión del ensemble.

Cambiar cualquiera de esos criterios ya no requiere tocar el código del
agente.

### Lo que el orquestador NO decide

**Los tres vetos y el risk kernel.** Son seguridad, no política: si
vivieran en nodos editables, un workflow mal tocado los apagaría sin que
nadie lo note. `/trade` los aplica siempre, aunque el orquestador insista.

Y `/trade` exige un `/forecast` previo, de menos de una hora, y no
repetido. Un workflow mal editado no puede operar sin analizar, ni duplicar
una posición.

### Los cinco workflows

| workflow | cuándo | qué hace |
|---|---|---|
| `1-ciclo` | cada 4 h | el camino de arriba, un mercado por ejecución |
| `2-vigilancia` | cada 10 min | consulta la salud del agente |
| `3-aprendizaje` | cada 6 h + diario | registra resultados, dicta el veredicto |
| `4-errores` | cuando algo revienta | avisa con el nodo y el mensaje |
| `5-mando` | webhook | disparar o parar desde el celular |

### Lo demás que aporta

**Un mercado = una ejecución.** Antes era una llamada de diez minutos a
ciegas: si un mercado moría, eso solo existía en los logs del contenedor.
Ahora cada mercado es su propia fila que se ve y se reintenta sola.

**Separa quién frenó qué.** El resumen distingue lo que descartó el agente,
lo que descartó la política y lo que rechazó el kernel. Eso es lo que dice
si los umbrales están bien puestos: si la política frena todo, están muy
estrictos; si el kernel rechaza mucho, el dimensionamiento está mal.

**Memoria entre ejecuciones.** La vigilancia recuerda el chequeo anterior,
así que avisa al **tercer** fallo seguido y una sola vez. Sin memoria solo
hay dos opciones malas: avisar en cada parpadeo de red hasta que dejas de
leer las alertas, o no avisar nunca.

**El veredicto.** Dice *apagar* cuando el agente no le gana al mercado,
aunque esté ganando plata.

### Sigue siendo opcional

El agente tiene sus propios bucles internos y corre igual sin n8n, con la
política por defecto que trae dentro. Si n8n se cae, el agente ni se
entera.

---

## Inventario

**El agente** — configuración tipada, cliente CLOB V2 de Polymarket, API
HTTP, almacenamiento, alertas; y por el lado del pronóstico: selección de
mercados, investigación, ensemble, calibración, cálculo de edge, pipeline,
proveedores de modelo.

**Risk kernel** — límites duros que el modelo *no puede ver ni modificar*:
viven en variables de entorno a propósito, porque si estuvieran donde el
modelo puede leerlos empezaría a razonar sobre cómo llegar hasta ellos.
Kill switch que sobrevive reinicios. 35 pruebas propias.

**El pipeline** — una lista explícita de seis pasos:
`investigar → pronosticar → cotizar → decidir → validar → ejecutar`.
Cada paso registra dónde y por qué se detuvo un mercado.

**Los tres vetos** — ambigüedad declarada, desacuerdo entre modelos,
divergencia extrema.

**Orquestación** — los 5 workflows de n8n.

**Documentación viva** — un vault de Obsidian con 31 notas enlazadas y 3
diagramas del flujo. Abre directo en el grafo.

**El empaquetador** — arma la entrega y **escanea buscando credenciales**;
si encuentra algo con forma de clave de API, aborta sin escribir el paquete.

**Los arrancadores** — comprueban Docker, la RAM *contra el modelo elegido*,
disco y configuración antes de levantar nada.

**203 pruebas** automatizadas.

---

## Qué necesita la máquina que lo corra

**Docker**, y RAM libre — libre, no total, porque el modelo debe caber
entero:

| RAM libre | modelo | velocidad sin GPU |
|---|---|---|
| 16 GB | `deepseek-r1:8b` | ~60 s por pronóstico |
| **32 GB** | `deepseek-r1:14b` | ~2 min · **recomendado** |
| 64 GB | `deepseek-r1:32b` | ~4 min |

Más ~25 GB de disco. Una GPU NVIDIA de 8 GB o más lleva el pronóstico de
minutos a segundos; las integradas de Intel y AMD no sirven.

Configurar son cuatro valores y correr un script.

---

## Estado real

### Probado de punta a punta

Elegir mercados, investigar, pronosticar y decidir: 33 pronósticos, 462
fuentes consultadas, 11 decisiones de orden, los tres vetos disparando.
Esa corrida fue con modelos en la nube.

Y el risk kernel, con sus 35 pruebas.

### No probado

- **El stack en Docker nunca se levantó entero.** Se construyó en una
  máquina con ~2 GB de RAM disponible, que no da para correrlo.
- **Los workflows de n8n nunca se importaron a un n8n real.** Están
  validados en estructura y su lógica se probó con datos reales, pero el
  import puede sacar cosas: versiones de nodo, credenciales.
- **El ciclo de aprendizaje nunca corrió completo**, porque ningún mercado
  pronosticado ha resuelto todavía.

---

## El criterio de éxito

**No es cuánta plata gana.** Con 30 apuestas el resultado económico es
ruido: puede ir ganando sin tener ninguna ventaja real.

El criterio es si **le gana al precio de mercado pronosticando**. Eso se
mide con `skill_vs_market`:

- `> 0` → pronostica mejor que el mercado
- `≤ 0` → **se apaga**, por bien que se vea el resultado económico

Son unas 5-6 semanas de datos hasta poder decirlo. El sistema está
construido para poder concluir que no sirve.

---

## Antes de pasar a dinero real

Hace falta una wallet de **Polygon** con pUSD, y poner dos variables
distintas — no una — para que no ocurra por accidente.

Polymarket vive en **Polygon, chain 137**. Fondos enviados por BNB Smart
Chain u otra red **no llegan y no se recuperan**.
