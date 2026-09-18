"""
Los archivos que solo existen en la entrega.

El LEEME está escrito para quien recibe la carpeta: no conoce el proyecto
y no puede clonar el repo, que es privado.
"""

from __future__ import annotations

PLANTILLAS: dict[str, str] = {}


PLANTILLAS["requirements.txt"] = """\
py-clob-client-v2>=1.1.0
web3>=6.20.0
asyncpg>=0.29.0
httpx>=0.27.0
pydantic>=2.8.0
pydantic-settings>=2.4.0
python-dotenv>=1.0.1
"""


PLANTILLAS["LEEME.md"] = r"""# Agente de pronóstico — Polymarket

Lee mercados de eventos de Polymarket (¿ganará X?, ¿pasará Y antes de tal
fecha?), busca información, estima una probabilidad y apuesta solo cuando
difiere del precio lo suficiente para pagar el spread.

Arranca simulando. No toca dinero hasta que alguien cambie dos variables
a propósito.

Todo corre en tu máquina. No hay cuentas que abrir ni claves que pagar.

---

## 1. Qué necesita

**Docker** — lo único obligatorio.
[Docker Desktop](https://docs.docker.com/get-started/get-docker/) en
Windows/Mac, `docker` + `docker compose` en Linux.

**RAM libre** (libre, no total — el modelo debe caber entero):

| RAM libre | modelo | sin GPU | |
|---|---|---|---|
| 4 GB | `deepseek-r1:1.5b` | rápido | solo para ver que arranca |
| 16 GB | `deepseek-r1:8b` | ~60 s | mínimo razonable |
| **32 GB** | `deepseek-r1:14b` | ~2 min | **recomendado** |
| 64 GB | `deepseek-r1:32b` | ~4 min | el mejor que cabe |

**Disco:** ~25 GB.

**GPU NVIDIA ≥8 GB** — opcional, pasa de minutos a segundos. Descomenta el
bloque `deploy` de `ollama` en `docker-compose.yml`. Las GPU integradas de
Intel y AMD no sirven.

---

## 2. Arrancar

```bash
cp .env.example .env
```

Pon tres cosas en `.env`:

- `OLLAMA_MODEL` — el que quepa en tu RAM
- `AGENT_API_TOKEN` — algo largo y aleatorio
- `SEARXNG_SECRET` — otro distinto

```bash
./arrancar.sh       # Linux / Mac
.\arrancar.ps1      # Windows
```

Revisa que la máquina dé, levanta los servicios, espera la descarga del
modelo y prepara la base. **El primer arranque tarda**: 5-20 GB.

A mano sería:

```bash
docker compose up -d
docker compose logs -f ollama
docker compose exec agent python -m scripts.init_db
```

---

## 3. Qué queda corriendo

| | dónde |
|---|---|
| agente | http://localhost:10000/health |
| n8n | http://localhost:5678 |
| ollama | http://localhost:11434 |
| searxng | http://localhost:8888 |
| postgres | localhost:5432 |

```bash
docker compose logs -f agent
```

Cada 4 horas elige mercados y pronostica:

```
ORDEN YES ganara-x-la-eleccion | p=0.720 vs 0.550 | edge +0.170 | $5.00
VETO  pasara-y-antes-de-marzo — desacuerdo del ensemble 0.31
INVESTIGAR cierre-de-frontera — evidencia insuficiente
```

`VETO` e `INVESTIGAR` no son fallos: es el agente decidiendo no apostar.
La mayoría de los mercados terminan así.

---

## 4. n8n

El agente corre solo; n8n te deja verlo y meterle mano.

http://localhost:5678, crea una cuenta local (no sale a internet) e
importa los cinco archivos de `n8n/` con *Import from File*.

Después, dos cosas a mano:

1. *Credentials → New → Header Auth*. Name `Authorization`, Value
   `Bearer <tu AGENT_API_TOKEN>`. Asígnala a los nodos que llaman al agente.
2. En cada workflow: *Settings → Error workflow → `4 · Errores`*. Sin eso
   ese workflow nunca dispara y un fallo pasa desapercibido.

| | |
|---|---|
| `1-ciclo` | pronostica; un mercado = una ejecución que puedes reintentar sola |
| `2-vigilancia` | salud cada 10 min |
| `3-aprendizaje` | registra resultados y dice si vale la pena seguir |
| `4-errores` | atrapa cualquier workflow que reviente |
| `5-mando` | webhook para disparar o parar desde el celular |

Para alertas por Telegram: `@BotFather` crea el bot, `@userinfobot` da el
chat id, y van al `.env`. Sin eso corre igual, solo que no avisa.

---

## 5. Obsidian

**Empieza por aquí si quieres entender qué hace esto.** `vault/` ya viene
con todo el sistema documentado como notas enlazadas — no hay que esperar
a que corra.

Abre `vault/` como vault en [Obsidian](https://obsidian.md), entra a
`_INICIO` y pulsa `Ctrl+G`. Cada color es una capa:

| | |
|---|---|
| `0-arranque` | los pasos de esta guía |
| `1-flujo` | qué le hace a cada mercado |
| `2-decisiones` | en qué puede terminar |
| `6-servicios` | los cinco contenedores |
| `7-orquestacion` | los workflows de n8n |
| `contexto` | lo que escribes tú |

Y al revés: lo que escribas en `vault/contexto/` entra al prompt del
agente. Lee `_LEEME` antes.

Sus pronósticos van apareciendo en `3-mercados/` con el razonamiento
completo, enlazados al veto o la orden que salió.

---

## 6. Qué esperar

**Las primeras semanas no vas a saber si funciona.** Los mercados resuelven
en días o semanas y hacen falta ~30 resoluciones para que el número que
mide si sirve signifique algo.

```
semana 1-2   pronostica; nada ha resuelto
semana 3-4   primeras resoluciones
semana 5     30 resoluciones → la calibración se activa sola
semana 6     el reporte dice si hay ventaja o hay que apagarlo
```

**El criterio no es cuánta plata ganó.** Con 30 apuestas la plata es ruido:
puede ir ganando sin tener ninguna ventaja real. El criterio es si le gana
al precio pronosticando:

```bash
curl http://localhost:10000/report -H "Authorization: Bearer <token>"
```

`skill_vs_market > 0` → pronostica mejor que el mercado.
`skill_vs_market <= 0` → **apágalo**, por bien que se vea el PnL.

Está construido para poder decirte que no sirve.

---

## 7. Comandos

```bash
# disparar un ciclo sin esperar
curl -X POST http://localhost:10000/cycle -H "Authorization: Bearer <token>"

curl http://localhost:10000/health

# botón de pánico
curl -X POST http://localhost:10000/kill -H "Authorization: Bearer <token>"

docker compose down
docker compose up -d

# cambiar de modelo
docker compose exec ollama ollama pull deepseek-r1:14b
#   → cambia OLLAMA_MODEL en .env
docker compose restart agent
```

El botón de pánico sobrevive reinicios: deja un archivo en `state/`.
Revivirlo es a mano, a propósito:

```bash
rm state/.KILLED_a && docker compose restart agent
```

---

## 8. Si algo falla

**Ollama no responde** — casi siempre sigue bajando el modelo:
`docker compose logs ollama | tail -30`

**Se queda sin memoria** — el modelo es muy grande. Baja un escalón en
`OLLAMA_MODEL` y `docker compose restart agent`.

**SearXNG da 403** — le falta `formats: [json]` en `searxng/settings.yml`.
El que viene ya lo tiene; si lo editaste, revisa eso.

**Pronósticos raros o vacíos** — mira si la búsqueda trae algo:
`curl "http://localhost:8888/search?q=test&format=json" | head -40`
Sin información el modelo rellena con lo que recuerda y salen pronósticos
sobreconfiados. El agente los veta solo, pero conviene saberlo.

**Lentísimo** — normal sin GPU. Baja `MARKETS_PER_CYCLE` a 2-3. Deja
`FORECAST_CONCURRENCY=1`: en CPU dos pronósticos a la vez se pelean los
mismos núcleos y los dos tardan el doble.

---

## 9. Dinero real

**Todavía no.** Deja que junte 30 resoluciones y mira `skill_vs_market`.

Después hacen falta una wallet de **Polygon** con pUSD y poner `MODE=live`
**más** `I_UNDERSTAND_LIVE=yes` — dos variables para que no pase por
accidente.

Polymarket vive en **Polygon (chain 137)**. Fondos enviados por BNB Smart
Chain u otra red no llegan y no se recuperan.

---

## 10. Lo que no está probado

- El stack completo en Docker **nunca se levantó entero**: se armó en una
  máquina sin RAM para correrlo. La primera vez van a salir cosas.
- Los workflows de n8n están validados en estructura y su lógica se probó
  con datos reales, pero **nunca se importaron a un n8n real**.
- El ciclo de aprendizaje **nunca corrió completo**: ningún mercado
  pronosticado ha resuelto todavía.

Sí está probado, de punta a punta con modelos en la nube: elegir mercados,
investigar, pronosticar y decidir. Y los límites de riesgo, con 35 pruebas
propias.
"""


PLANTILLAS[".env.example"] = r"""# cp .env.example .env   y edita las tres cosas de abajo.

LOCAL_MODE=true

# paper = simula contra el libro real, sin tocar dinero.
# Pasar a real exige LAS DOS variables. Es a propósito.
MODE=paper
I_UNDERSTAND_LIVE=no


# ═══ 1. MODELO — según tu RAM LIBRE, no la total ═══
#   deepseek-r1:1.5b    4 GB RAM    solo para ver que arranca
#   deepseek-r1:8b     16 GB RAM    mínimo razonable
#   deepseek-r1:14b    32 GB RAM    recomendado
#   deepseek-r1:32b    64 GB RAM    el mejor que cabe
OLLAMA_MODEL=deepseek-r1:8b

# Modelo chico solo para resumir páginas. Vacío = usa el de arriba.
OLLAMA_MODEL_TRIAGE=


# ═══ 2. DOS CLAVES ALEATORIAS — ponlas ═══
# La primera protege la API: sin ella cualquiera en tu red puede
# disparar ciclos o parar el agente.
#   Linux/Mac:  openssl rand -hex 24
#   Windows:    -join ((1..48) | % { '{0:x}' -f (Get-Random -Max 16) })
AGENT_API_TOKEN=
SEARXNG_SECRET=


# ═══ 3. ALERTAS (opcional) ═══
# @BotFather crea el bot · @userinfobot da el chat id
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=


# ═══ 4. GROQ (opcional, gratis) ═══
# Segunda opinión de otro modelo. Con uno solo se pierde el veto por
# desacuerdo, que es el que frena las apuestas donde dos no coinciden.
# https://console.groq.com/keys
# Lo que ata son los tokens diarios, no las peticiones:
#   llama-3.3-70b-versatile  ~18 pronósticos/día
#   openai/gpt-oss-120b      ~36 pronósticos/día
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile


# ═══ 5. RITMO ═══
# Sin GPU, 8 mercados son ~10 min de ciclo. Para probar, baja a 2-3.
MARKETS_PER_CYCLE=8
MARKET_PAGES=8
STRATEGIST_INTERVAL_MIN=240

# 1 si no tienes GPU: en CPU dos a la vez se pelean los núcleos y los dos
# tardan el doble.
FORECAST_CONCURRENCY=1


# ═══ 6. NO TOCAR ═══
DATABASE_URL=postgresql://trade:trade@postgres:5432/mcptrade
OLLAMA_HOST=http://ollama:11434
SEARXNG_HOST=http://searxng:8080
AGENT_URL=http://agent:10000
CLOB_HOST=https://clob.polymarket.com
GAMMA_HOST=https://gamma-api.polymarket.com
CLOB_WS=wss://ws-subscriptions-clob.polymarket.com/ws
CHAIN_ID=137
SIGNATURE_TYPE=1
STALE_DATA_SECONDS=300


# ═══ 7. DINERO REAL — déjalas vacías ═══
# Polymarket vive en Polygon (chain 137). Fondos por BNB Smart Chain u
# otra red NO llegan y no se recuperan.
PILOT_A_PRIVATE_KEY=
PILOT_A_FUNDER=


# ═══ LÍMITES DE RIESGO ═══
# Van en variables de entorno a propósito: el modelo nunca los ve. Si
# estuvieran donde él puede leerlos, razonaría sobre cómo llegar a ellos.
RISK_A_MAX_ORDER_USD=5
RISK_A_MAX_POSITION_USD=25
RISK_A_MAX_GROSS_EXPOSURE_USD=50
RISK_A_MAX_DAILY_LOSS_USD=10
RISK_A_MAX_ORDERS_PER_MIN=6
RISK_A_MAX_OPEN_ORDERS=10
RISK_A_MAX_PRICE_DEVIATION=0.03
RISK_A_MIN_PRICE=0.02
RISK_A_MAX_PRICE=0.98
"""


PLANTILLAS["arrancar.sh"] = r"""#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

rojo()  { printf '\033[91m  [X] %s\033[0m\n' "$*"; }
verde() { printf '\033[92m  [ok] %s\033[0m\n' "$*"; }
ojo()   { printf '\033[93m  [!] %s\033[0m\n' "$*"; }

echo; echo "  Comprobaciones"; echo "  --------------"
fallo=0

command -v docker >/dev/null 2>&1 || {
  rojo "Docker no está instalado"
  echo "      https://docs.docker.com/get-started/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || {
  rojo "Docker instalado pero el demonio no responde"
  echo "      Abre Docker Desktop, o: sudo systemctl start docker"; exit 1; }
verde "Docker responde"

[[ -f .env ]] || { rojo "Falta el .env"; echo "      cp .env.example .env"; exit 1; }

leer() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '[:space:]'; }
for clave in OLLAMA_MODEL AGENT_API_TOKEN SEARXNG_SECRET; do
  [[ -n "$(leer "$clave")" ]] || { rojo "$clave está vacío en el .env"; fallo=1; }
done
(( fallo )) || verde ".env completo"

modelo=$(leer OLLAMA_MODEL)
case "$modelo" in
  *1.5b) pide=4 ;; *8b) pide=16 ;; *14b) pide=32 ;; *32b) pide=64 ;; *) pide=0 ;;
esac

if [[ "$(uname)" == "Darwin" ]]; then
  libre_gb=$(( $(vm_stat | awk '/Pages free/ {gsub(/\./,"",$3); print $3}') * 4096 / 1073741824 ))
else
  libre_gb=$(( $(free -m | awk '/^Mem:/ {print $7}') / 1024 ))
fi

if (( pide > 0 && libre_gb + 2 < pide )); then
  ojo "RAM libre ${libre_gb} GB — '${modelo}' pide ~${pide} GB"
  echo "      Va a ir a swap o el contenedor va a morir. Baja un escalón."
  fallo=1
else
  verde "RAM libre ${libre_gb} GB para '${modelo}'"
fi

disco_gb=$(df -BG . | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
if (( disco_gb < 25 )); then
  ojo "Disco libre ${disco_gb} GB — hacen falta ~25 GB"; fallo=1
else
  verde "Disco libre ${disco_gb} GB"
fi

(( fallo )) && { echo; ojo "Arregla lo de arriba y vuelve a correr esto."; exit 1; }

echo; echo "  Levantando...  (el primer arranque baja 5-20 GB)"
docker compose up -d || exit 1

echo; echo "  Esperando a Ollama..."
for i in $(seq 1 240); do
  if docker compose exec -T ollama ollama list 2>/dev/null | grep -q "${modelo%%:*}"; then
    verde "modelo '${modelo}' listo"; break
  fi
  printf '\r  ... %d min' $(( i / 4 )); sleep 15
done
echo

echo "  Preparando la base..."
docker compose exec -T agent python -m scripts.init_db && verde "base lista"

echo; verde "Arriba."
echo
echo "    agente   http://localhost:10000/health"
echo "    n8n      http://localhost:5678"
echo
echo "    docker compose logs -f agent"
echo
"""


PLANTILLAS["arrancar.ps1"] = r"""$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Ok($m)  { Write-Host "  [ok] $m" -ForegroundColor Green }
function Mal($m) { Write-Host "  [X]  $m" -ForegroundColor Red }
function Ojo($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }

Write-Host ""; Write-Host "  Comprobaciones"; Write-Host "  --------------"
$fallo = $false

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Mal "Docker no esta instalado"
    Write-Host "       https://docs.docker.com/get-started/get-docker/"; exit 1
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Mal "Docker instalado pero el demonio no responde"
    Write-Host "       Abre Docker Desktop y espera a 'Engine running'."; exit 1
}
Ok "Docker responde"

if (-not (Test-Path .env)) {
    Mal "Falta el .env"; Write-Host "       copy .env.example .env"; exit 1
}
$cfg = @{}
foreach ($l in Get-Content .env) {
    if ($l -match '^\s*([A-Z_0-9]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
}
foreach ($k in @("OLLAMA_MODEL", "AGENT_API_TOKEN", "SEARXNG_SECRET")) {
    if (-not $cfg[$k]) { Mal "$k esta vacio en el .env"; $fallo = $true }
}
if (-not $fallo) { Ok ".env completo" }

$modelo = $cfg["OLLAMA_MODEL"]
$pide = switch -Wildcard ($modelo) {
    "*1.5b" { 4 } "*8b" { 16 } "*14b" { 32 } "*32b" { 64 } default { 0 }
}
$libreGb = [math]::Round((Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).AvailableMBytes / 1024, 1)
if ($pide -gt 0 -and ($libreGb + 2) -lt $pide) {
    Ojo "RAM libre $libreGb GB - '$modelo' pide ~$pide GB"
    Write-Host "       Va a ir a swap o el contenedor va a morir. Baja un escalon:"
    Write-Host "       1.5b (4 GB) - 8b (16) - 14b (32) - 32b (64)"
    $fallo = $true
} else {
    Ok "RAM libre $libreGb GB para '$modelo'"
}

$discoGb = [math]::Round((Get-PSDrive (Get-Location).Drive.Name).Free / 1GB, 0)
if ($discoGb -lt 25) { Ojo "Disco libre $discoGb GB - hacen falta ~25 GB"; $fallo = $true }
else { Ok "Disco libre $discoGb GB" }

if ($fallo) { Write-Host ""; Ojo "Arregla lo de arriba y vuelve a correr esto."; exit 1 }

Write-Host ""; Write-Host "  Levantando...  (el primer arranque baja 5-20 GB)"
docker compose up -d
if ($LASTEXITCODE -ne 0) { Mal "docker compose fallo"; exit 1 }

Write-Host ""; Write-Host "  Esperando a Ollama..."
$base = ($modelo -split ':')[0]
for ($i = 1; $i -le 240; $i++) {
    if ((docker compose exec -T ollama ollama list 2>$null) -match [regex]::Escape($base)) {
        Ok "modelo '$modelo' listo"; break
    }
    Write-Host ("`r  ... {0} min" -f [math]::Floor($i / 4)) -NoNewline
    Start-Sleep -Seconds 15
}
Write-Host ""

Write-Host "  Preparando la base..."
docker compose exec -T agent python -m scripts.init_db
if ($LASTEXITCODE -eq 0) { Ok "base lista" }

Write-Host ""; Write-Host "  Arriba." -ForegroundColor Green
Write-Host ""
Write-Host "    agente   http://localhost:10000/health"
Write-Host "    n8n      http://localhost:5678"
Write-Host ""
Write-Host "    docker compose logs -f agent"
Write-Host ""
"""
