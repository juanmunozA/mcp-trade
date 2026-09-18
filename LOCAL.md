# Correr todo local

Agente de pronóstico para mercados de eventos de Polymarket. Sin nubes,
sin cuotas, sin credenciales de pago.

---

## Qué necesita la máquina

El modelo se elige según la **RAM libre**, no la total:

| Modelo | Disco | RAM libre | Velocidad sin GPU | ¿Sirve? |
|---|---|---|---|---|
| `deepseek-r1:1.5b` | 1.1 GB | 4 GB | rápido | solo para probar que arranca |
| `deepseek-r1:8b` | 4.9 GB | **16 GB** | ~60 s/pronóstico | mínimo razonable |
| `deepseek-r1:14b` | 9 GB | **32 GB** | ~2 min/pronóstico | **recomendado** |
| `deepseek-r1:32b` | 20 GB | 64 GB | ~4 min/pronóstico | el mejor que cabe |

Más Docker, ~10 GB de disco para los contenedores, y conexión (el agente
consulta Polymarket y los buscadores).

**Con GPU NVIDIA de 8 GB o más**, descomenta el bloque `deploy` en
`docker-compose.yml`: el pronóstico pasa de minutos a segundos y puedes
subir un escalón de modelo.

---

## Arranque

```bash
git clone https://github.com/juanmunozA/mcp-trade
cd mcp-trade

cp .env.local.example .env
#   → edita OLLAMA_MODEL según tu RAM
#   → pon algo aleatorio en AGENT_API_TOKEN y SEARXNG_SECRET

docker compose up -d
```

El primer arranque **tarda**: Ollama descarga el modelo (5-20 GB). Míralo con:

```bash
docker compose logs -f ollama
```

Cuando termine, aplica el esquema de la base:

```bash
docker compose exec agent python -m scripts.init_db
```

Y listo. El agente ya está pronosticando:

```bash
docker compose logs -f agent
```

---

## Qué quedó levantado

| Servicio | Dónde | Para qué |
|---|---|---|
| **agent** | http://localhost:10000/health | El agente. Estado, presupuesto, kill switch |
| **n8n** | http://localhost:5678 | Orquestación: cuándo corre cada cosa |
| **ollama** | http://localhost:11434 | Los modelos |
| **searxng** | http://localhost:8888 | Búsqueda web |
| **postgres** | localhost:5432 | Pronósticos, resoluciones, calibración |

### n8n

Entra a http://localhost:5678, crea la cuenta local (no sale a internet) e
importa los **cinco** workflows de `n8n/` desde *Import from File*:

| | qué hace | por qué en n8n y no en el agente |
|---|---|---|
| `1-ciclo.json` | **un mercado = una ejecución** | ves cuál murió dónde y reintentas solo ese |
| `2-vigilancia.json` | salud cada 10 min | avisa al **3er** fallo seguido, no en cada parpadeo |
| `3-aprendizaje.json` | resolver + veredicto diario | te dice cuándo **apagarlo** |
| `4-errores.json` | atrapa cualquier workflow que reviente | sin esto un workflow roto se calla |
| `5-mando.json` | webhook: disparar o parar desde el celular | no hay que entrar a la máquina |

Después de importar, dos cosas a mano:

1. **Credencial.** Crea una *Header Auth* con `Name: Authorization` y
   `Value: Bearer <tu AGENT_API_TOKEN>`, y asígnala a los nodos HTTP que
   llaman al agente. Es la misma para todos.
2. **Error workflow.** En cada workflow → *Settings* → *Error workflow* →
   `4 · Errores`. Sin ese paso el workflow 4 nunca se dispara.

Los workflows ya apuntan a `http://agent:10000` — dentro de la red de
Docker los servicios se ven por nombre.

**No los edites a mano en la UI sin volcar los cambios**: el JSON se
genera desde [n8n/generar.py](n8n/generar.py), y `python n8n/generar.py`
los reescribe.

#### El fan-out, que es lo que cambia

Antes n8n disparaba `POST /cycle` y esperaba 10 minutos a una caja negra.
Ahora reparte:

```
GET /candidates        →  el explorador. Sin IA, sin costo.
      ↓ un item por mercado
POST /market {slug}    →  UN mercado por el pipeline, atómico
      ↓ resultado por mercado: parado_en, motivo, decisión
```

Cada mercado es su propia ejecución: se ve dónde murió, se reintenta solo
ese, y se ramifica según el resultado.

**Hasta ahí llega el corte, y es a propósito.** `validar` y `ejecutar`
comparten el estado en memoria del risk kernel y corren bajo un lock —
entre `check()` y `on_order_sent()` hay `await`s, y dos mercados que los
crucen a la vez pasan el mismo límite antes de que ninguno lo consuma. Un
salto HTTP ahí devuelve esa carrera y encima la hace sobrevivir a
reinicios. Por eso `/market` es indivisible.

`/market` solo acepta slugs que el explorador ya eligió. Un workflow mal
editado no puede meter un mercado arbitrario en la ruta de ejecución.

### Groq (opcional, pero vale la pena)

Corriendo con un solo modelo local se pierde **el veto por desacuerdo** —
el que comparaba dos modelos y frenaba la apuesta cuando no se ponían de
acuerdo. Es el veto que evitó la peor apuesta que el sistema habría hecho.

Groq lo devuelve gratis y sin tarjeta: https://console.groq.com/keys

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

Sirve porque es **otra familia** (Llama contra DeepSeek): sus errores no
están correlacionados con los del modelo local, y eso es justo lo que
hace informativo el desacuerdo. Dos modelos parecidos se equivocan juntos
y el veto no dispara nunca.

El límite real del free tier no es el que anuncian. Dicen 1.000
peticiones/día, pero **lo que ata son los tokens diarios**:

| Modelo | TPD | Pronósticos/día reales |
|---|---|---|
| `llama-3.3-70b-versatile` | 100K | ~18 |
| `openai/gpt-oss-120b` | 200K | ~36 |

Alcanza porque Groq solo entra en el **segundo escalón**: los ~4 mercados
por ciclo que el modelo local marca como interesantes, no los 8. Con 6
ciclos al día son ~24 escalados.

Dos detalles: los límites son por organización (no por key, no sirve
abrir varias), y DeepSeek-R1 fue deprecado en Groq en septiembre de 2025 —
por eso el segundo modelo es Llama y no otro DeepSeek.

Si dejas `GROQ_API_KEY` vacío el agente corre igual, solo con un modelo y
sin ese veto.

### Obsidian

Abre `./vault` como vault. Ahí el agente escribe cada pronóstico con su
razonamiento completo, y tú escribes en `contexto/` las lecciones que
quieres que use. `Ctrl+G` para la vista de grafo.

---

## Comandos que vas a usar

```bash
# disparar un ciclo a mano
curl -X POST http://localhost:10000/cycle \
     -H "Authorization: Bearer $AGENT_API_TOKEN"

# o paso a paso, como lo hace n8n
curl "http://localhost:10000/candidates?n=3" \
     -H "Authorization: Bearer $AGENT_API_TOKEN"
curl -X POST http://localhost:10000/market \
     -H "Authorization: Bearer $AGENT_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"slug":"el-slug-que-devolvio-candidates"}'

# ¿hay edge o hay que apagarlo?
curl http://localhost:10000/report \
     -H "Authorization: Bearer $AGENT_API_TOKEN"

# botón de pánico
curl -X POST http://localhost:10000/kill \
     -H "Authorization: Bearer $AGENT_API_TOKEN"

# cambiar de modelo
docker compose exec ollama ollama pull deepseek-r1:14b
#   → cambia OLLAMA_MODEL en .env
docker compose restart agent
```

---

## Ajustar el ritmo

En `.env`:

```
MARKETS_PER_CYCLE=8          mercados por ciclo
FORECAST_CONCURRENCY=1       1 en CPU, 2-3 con GPU
STRATEGIST_INTERVAL_MIN=240  cada 4 horas
```

**Concurrencia 1 en CPU no es conservadurismo**: dos pronósticos a la vez
se pelean los mismos núcleos y los dos tardan el doble. No ganas nada.

Con un modelo de 8b sin GPU, 8 mercados son unos 10 minutos de ciclo. Si
vas a dejarlo corriendo solo, eso está bien. Si quieres verlo trabajar,
baja `MARKETS_PER_CYCLE` a 2-3 mientras pruebas.

---

## Qué esperar

**Las primeras semanas no vas a saber si funciona.** Los mercados de
eventos resuelven en días o semanas, y hacen falta ~30 resoluciones para
que el `brier_skill_score` signifique algo.

```
semana 1-2    pronostica; nada ha resuelto todavía
semana 3-4    llegan las primeras resoluciones
semana 5      30 resoluciones → la calibración se activa sola
semana 6      el reporte te dice si hay edge o hay que apagarlo
```

Ese número es el criterio, no el PnL: con 30 apuestas el PnL es ruido.

El agente arranca en `MODE=paper` — simula fills contra el book real sin
tocar dinero. Para pasar a `live` hace falta una wallet de Polygon con
pUSD, y la lista de verificación está en `docs/RUNBOOK.md`.

---

## Si algo falla

**Ollama no responde** — probablemente todavía está bajando el modelo:
```bash
docker compose logs ollama | tail -30
```

**SearXNG devuelve 403** — le falta `formats: [json]` en
`searxng/settings.yml`. Es el fallo más común montándolo para uso
programático.

**El agente se queda sin memoria** — el modelo es muy grande para la
máquina. Baja un escalón en `OLLAMA_MODEL`.

**Pronósticos vacíos o sin sentido** — mira si la búsqueda está trayendo
algo:
```bash
curl "http://localhost:8888/search?q=test&format=json" | head -40
```
Sin evidencia el modelo rellena con su prior, y ahí salen los pronósticos
sobreconfiados. El agente lo veta solo, pero vale la pena saberlo.

---

## Sobre "ilimitado"

Ollama es MIT y los destilados de DeepSeek-R1 también, uso comercial
incluido. No hay cuota de tokens ni clave que rotar.

**Pero el límite existe: es el tiempo de CPU.** DeepSeek-R1 razona mucho
antes de responder, y ese razonamiento se paga en segundos. Ilimitado en
cuota, no en reloj.

Y hay un costo de calidad que conviene tener presente: un modelo que cabe
en tu RAM está peor calibrado que uno de frontera, y la calibración es
justamente el trabajo aquí. Por eso el bucle de resolución importa — te
dice con **tus** datos si el modelo local sirve, en vez de asumirlo.
