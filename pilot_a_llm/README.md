# Piloto A — "El Arena"

Un LLM gestiona exposición direccional en mercados binarios. Capital real,
deliberadamente pequeño.

```powershell
python -m pilot_a_llm.run
```

## La hipótesis

¿Puede un LLM decidir *cuándo tener exposición* mejor que una regla fija o que
no tener ninguna?

Nota que la pregunta no es "¿puede predecir el mercado?". Es más modesta y más
comprobable: dado un régimen, ¿elige estar dentro o fuera mejor que el azar?

## El precedente

Alpha Arena (nof1.ai), Season 1: 18-oct a 3-nov de 2025. Seis LLMs, $10.000
reales cada uno, perps de crypto en Hyperliquid, mismo prompt, mismos datos,
cero intervención humana.

- Ganador: **Qwen 3 Max, +22.31%**
- Peores: **GPT-5 y Gemini, -60%+**

Léelo bien: mismo prompt, mismos datos, ~80 puntos porcentuales de dispersión
en 16 días. Eso es varianza, no alfa. El dato útil no es que uno ganara — es
que **sin límites externos duros, un LLM se autodestruye.**

Por eso aquí el modelo no puede ver ni tocar sus propios límites.

## Arquitectura

```
estratega (LLM)  cada 4 h  ──► Policy (JSON validado)
                                  │
                              clamp() ── recorta contra límites reales
                                  │
ejecutor (código) cada 30 s  ──► OrderIntent
                                  │
                            risk kernel ── ⛔ la última palabra
                                  │
                              exchange
```

El LLM emite **preferencias**, no órdenes. Tres capas entre el modelo y la plata.

## Qué significa "entrenar" aquí

No hay fine-tuning. Lo que hay es más barato y más efectivo:

1. **Journal.** Cada política escribe su tesis en `pilot_a.journal`. El ciclo
   siguiente lee las últimas 25 entradas **con su resultado real**. El modelo
   confronta sus propias tesis fallidas.
2. **Reproducibilidad.** `pilot_a.policies` guarda el prompt completo, la
   respuesta cruda, tokens y costo. Puedes reconstruir exactamente qué "pensaba"
   el modelo en cualquier trade.
3. **Iteración del prompt.** Los recortes de `clamp()` se loguean. Si el modelo
   pide 10× el límite todos los ciclos, el problema es el prompt, no el modelo.

## Qué medir

El benchmark **no** es "¿ganó plata?". Con 30 trades eso es una moneda al aire.

| Métrica | Qué te dice |
|---|---|
| vs. buy & hold | ¿El razonamiento aporta algo sobre no hacer nada? |
| % de ciclos en `flat` | Si nunca va flat, no está evaluando — está apostando |
| Recortes de `clamp()` por ciclo | Qué tan bien entiende su contexto |
| Costo LLM vs PnL bruto | Si el estratega cuesta más de lo que aporta, cierra el piloto |
| Distribución de `confidence` | "high" constante es sobreconfianza, no convicción |

## Costo

A `claude-opus-5` y 6 decisiones diarias: **$3–10/mes**. El estratega no es
donde vas a perder plata.
