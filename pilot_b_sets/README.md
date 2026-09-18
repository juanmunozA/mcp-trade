# Piloto B — "Complete Sets"

Réplica de la mecánica de `norm1e69`, el usuario descrito en el tweet de
[@RetroValix](https://x.com/RetroValix/status/2100375324244447353).

```powershell
python -m pilot_b_sets.run
```

## El caso original

+$111.945 con **50% de win rate**. Ese win rate es la pista: no es un negocio
de predicción, es un negocio de spread.

- Compró **ambos lados** en 88.76% de los mercados.
- Acumula un lado → espera que se mueva el subyacente → compra el opuesto,
  ahora más barato → rota entre ambos mientras el mercado repricea.
- Costo medio del set completo: **98.69¢** → edge ≈ **1.31¢** (~1.3%).
- El inventario sin parear queda como residual direccional, dirigido por su
  señal.

**Aquí no hay LLM.** No debería haberlo: la inferencia tarda segundos y este
juego se decide en milisegundos.

## Las dos formas de armar un set

### Atómica
El book ya ofrece `ask_up + ask_down < $1`. Compras ambos lados a la vez. Cero
riesgo direccional.

**Este juego probablemente ya lo perdiste.** Reportes de 2026 ponen la ventana
media de una oportunidad de arbitraje en ~2.7 segundos (contra 12.3s en 2024),
con la mayoría del PnL capturado por bots sub-100ms.

Está implementado igual, pero como **instrumento de medición**: tu fill rate en
`kind='atomic'` te dice en números si tienes infraestructura para competir. Si
es cercano a cero, no la tienes, y eso es información valiosa que te ahorra
meses.

### Legging
Acumulas un lado, esperas el movimiento, compras el otro más barato.
`norm1e69` hizo esto en el 88.76% de sus mercados. **Aquí sí hay espacio para
alguien sin colocation.**

El precio: mientras estés desbalanceado tienes **riesgo direccional puro**. Ese
es el "residual direccional" del tweet, y es lo que puede comerse 1.31¢ × 30
sets en un solo movimiento adverso.

Por eso `max_imbalance_shares` fuerza rebalanceo antes que edge: perder medio
centavo cerrando el lado corto es infinitamente mejor que quedar largo 200
shares de UP cuando el BTC se cae.

## Parámetros

| Variable | Default | Qué controla |
|---|---|---|
| `SETS_MAX_COMBINED_COST` | `0.988` | Umbral de edge. Más alto = más trades, menos margen |
| `SETS_MAX_IMBALANCE_SHARES` | `50` | Cuánto riesgo direccional toleras antes de forzar pareo |
| `SETS_MARKET_SLUGS` | *(vacío)* | Whitelist. **Vacío = el kernel rechaza todo** (fail closed) |

## Qué medir

| Métrica | Qué te dice | Señal de alarma |
|---|---|---|
| `avg_combined_cost` | Tu costo real por set | Si está sobre 0.99, no hay negocio |
| **Fill rate en `atomic`** | Si compites en latencia | Cerca de 0 → no compites |
| `worst_imbalance` | Riesgo direccional acumulado | Se acerca al máximo → el motor no está pareando |
| `locked_edge_usd` vs PnL real | Cuánto del edge teórico llega a tu bolsillo | La diferencia son fees y slippage |
| Slippage medio (bps) | Tu costo de ejecución | Si supera 131 bps, el edge no existe |

Esa última línea es el negocio entero: **el edge es 1.31¢ sobre ~$1. Si tu
slippage medio supera eso, estás operando en pérdida y el backtest más bonito
del mundo no lo cambia.**

## Latencia

Si vas en serio con la parte atómica, el VPS tiene que estar en la región del
matching engine (AWS us-east-1). Desde Frankfurt o desde Oracle Free Tier en
otra región, la oportunidad se evapora antes de que tu paquete llegue.

Para el legging la latencia importa mucho menos — otra razón por la que ahí
está tu espacio real.
