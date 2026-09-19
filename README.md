# MCP Trade — dos pilotos de trading autónomo en Polymarket

Monorepo con **dos experimentos independientes** que comparten un núcleo común
(`core/`) pero corren como procesos separados, con wallets, límites de riesgo,
esquemas de base de datos y kill switches **distintos**.

```
                        ┌──────────────┐
                        │    core/     │  cliente CLOB, risk kernel,
                        │  (compartido)│  storage, alertas, config
                        └──────┬───────┘
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
        ┌─────────────────┐        ┌────────────────────┐
        │  pilot_a_llm/   │        │  trade/     │
        │  "El Arena"     │        │  "norm1e69"        │
        │  LLM decide     │        │  complete sets     │
        │  wallet A       │        │  wallet B          │
        │  schema pilot_a │        │  schema pilot_b    │
        └─────────────────┘        └────────────────────┘
```

---

## Los dos pilotos

### `pilot_a_llm/` — El Arena
Un LLM actúa como **estratega**, no como ejecutor. Cada N horas lee el estado del
mercado, su propio journal de trades y las métricas recientes, y emite una
**política** (JSON validado), no una orden. Un ejecutor determinista traduce esa
política a órdenes, y el risk kernel las valida.

- **Hipótesis:** ¿puede un LLM gestionar exposición direccional mejor que buy&hold?
- **Precedente:** Alpha Arena (nof1.ai) — 6 LLMs, $10k c/u. Ganador +22%, peores -60%.
- **Expectativa honesta:** retorno esperado ≈ 0 o negativo. El valor es el aprendizaje.

### `trade/` — Complete Sets
Replica la mecánica del usuario `norm1e69` descrita en el tweet de @RetroValix:
acumular **ambos lados** (Up y Down) del mismo mercado rotando entre ellos hasta
armar sets donde `precio(Up) + precio(Down) < $1`. El inventario sin parear queda
como residual direccional.

- **Hipótesis:** el edge es de ejecución (≈1.31¢ sobre un costo de 98.69¢), no de predicción.
- **Sin LLM en el hot path.** Determinista, asyncio, milisegundos.
- **Riesgo principal:** competencia. La duración media de una oportunidad de arbitraje
  cayó a ~2.7s y la mayoría se la llevan bots sub-100ms. Mide tu fill rate desde el día 1.

---

## Regla de oro

> **El LLM nunca toca la plata.** Emite políticas. El risk kernel (`core/risk/`)
> valida toda orden contra límites que viven en variables de entorno y que
> ningún modelo puede leer ni modificar.

---

## Modos de ejecución

| Modo | Qué hace | Cómo se activa |
|---|---|---|
| `collect` | Solo graba order books a Postgres. Sin órdenes. | default |
| `paper` | Simula fills contra el book real. Sin plata. | `MODE=paper` |
| `live` | Órdenes reales. | `MODE=live` **+** `I_UNDERSTAND_LIVE=yes` |

`live` requiere las dos variables. Es a propósito.

---

## Arranque

```powershell
# 1. Entorno
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Infra (postgres + n8n + grafana)
docker compose up -d

# 3. Config
Copy-Item .env.example .env    # y edítalo

# 4. Schema
python -m scripts.init_db

# 5. Verificar que el SDK instalado tiene los métodos que espera el adapter
python -m scripts.verify_sdk

# 6. Empezar a grabar datos (fase 0 — déjalo 14 días)
python -m core.polymarket.collector

# 7. Tests del risk kernel (correr SIEMPRE antes de tocar live)
pytest -q
```

---

## Gates de fase

No pases de fase sin cumplir el gate. Si no lo cumples, te devuelves.

| Fase | Duración | Gate de salida |
|---|---|---|
| **0 · Infra y datos** | 2 sem | 14 días de book sin gaps >60s; reconexión probada 3× a mano |
| **1 · Backtest** | 2 sem | Sharpe >1.0 *out-of-sample* **después** de fees+slippage; maxDD <15% |
| **2 · Paper** | 4 sem | 30 días sin intervención; 0 desincronizaciones; kill switch disparado a propósito y funcionó |
| **3 · Live micro** | 8 sem | 60 días, maxDD <10%, >100 trades, expectancy positiva |
| **4 · Escalar o matar** | — | Sube 2× máximo, solo tras 30 días estables en cada nivel |

---

## Nota operativa sobre acceso

Polymarket está bloqueado por orden de Coljuegos a los ISP colombianos desde
sep-2025, y sus ToS prohíben evadir geobloqueos. El riesgo concreto no es penal:
es que la cuenta pase a *close-only* o los fondos queden congelados, sin
contraparte a quien reclamar. Dimensiona el capital asumiendo que **puede
quedar atrapado completo**. Ver `docs/RIESGOS.md`.

---

## Estructura

```
core/
  config.py            settings tipadas, una sola fuente de verdad
  storage/
    schema.sql         tablas (es tu contabilidad Y tu dataset)
    db.py              pool asyncpg + repositorios
  polymarket/
    client.py          adapter sobre py-clob-client (read/paper/live)
    collector.py       websocket 24/7 → postgres
    models.py          tipos del dominio
  risk/
    kernel.py          ⛔ EL RISK KERNEL. Léelo antes que nada.
  alerts.py            telegram
pilot_a_llm/           piloto A
trade/          piloto B
scripts/               init_db, verify_sdk, replay
tests/                 tests del kernel (los que importan)
docs/                  RIESGOS.md, RUNBOOK.md
```
