# Runbook

Qué hacer cuando. Escrito para tu yo de las 3 a.m., que no va a razonar bien.

---

## 🚨 Llegó una alerta de kill switch

1. **No reinicies.** El kill switch sobrevive al reinicio a propósito.
2. Mira por qué:
   ```powershell
   Get-Content state\.KILLED_b
   ```
   ```sql
   SELECT ts, event, reason, snapshot
   FROM pilot_b.risk_events ORDER BY ts DESC LIMIT 5;
   ```
3. Confirma que no quedó exposición viva:
   ```sql
   SELECT token_id, side, sum(size) FROM pilot_b.fills GROUP BY 1,2;
   ```
   Contrástalo con la posición real en la interfaz de Polymarket. **Si no
   cuadran, la fuente de verdad es Polymarket** — cierra a mano.
4. Arregla la causa raíz. No el síntoma.
5. Solo entonces:
   ```powershell
   Remove-Item state\.KILLED_b
   ```

**Causas frecuentes y qué significan:**

| Razón | Qué pasó realmente |
|---|---|
| `pérdida diaria ... alcanzó el límite` | El sistema funcionó. Revisa si la estrategia se rompió o fue varianza normal |
| `desincronización de posición` | Perdimos un fill. **Bug serio.** No reanudes sin entenderlo |
| `datos congelados hace Ns` | Red, rate limit, o Polymarket caído. Revisa `market.collector_health` |
| `excepción en el risk kernel` | Bug en el kernel. Corre `pytest` y arregla antes de tocar nada |

---

## Antes de cada deploy

```powershell
pytest -q                      # 35 tests. TODOS verdes o no hay deploy.
python -m scripts.verify_sdk   # el SDK no cambió bajo tus pies
```

Y la regla que no se negocia: **nunca despliegues con posición abierta.**
Detén el piloto (Ctrl+C — cancela todo al salir), confirma flat, despliega.

---

## Pasar de paper a live

Checklist. Si una línea no está marcada, no pasas.

- [ ] 30 días corridos en paper sin intervención manual
- [ ] Cero desincronizaciones de posición en esos 30 días
- [ ] Kill switch disparado a propósito al menos una vez, y funcionó
- [ ] `python -m scripts.check_gate --phase 0` pasa
- [ ] Wallet dedicada, fondeada **solo** con el capital del piloto
- [ ] Límites de riesgo revisados a mano, línea por línea, en `.env`
- [ ] Alertas de Telegram probadas (manda una de prueba)
- [ ] `MODE=live` **y** `I_UNDERSTAND_LIVE=yes`
- [ ] Sabes exactamente cuánto estás dispuesto a perder, y está escrito

---

## Rutina diaria (5 minutos)

```powershell
python -m scripts.check_gate --phase 3 --pilot b
```

Mira tres cosas, en este orden:

1. **Rechazos por código.** Si uno domina, tus límites están mal calibrados o
   hay un bug. Un `RATE_LIMIT` constante significa que el motor está en un loop.
2. **Fill rate.** Si baja, estás perdiendo la carrera de latencia. En el piloto B
   ese es *el* indicador de si tienes infraestructura para competir.
3. **Slippage medio (bps).** Si supera tu edge teórico, el negocio no existe —
   sin importar lo bonito que se vea el backtest.

---

## Consultas útiles

```sql
-- ¿Por qué se rechazan las órdenes?
SELECT reject_code, count(*), avg(notional)
FROM pilot_b.order_intents
WHERE ts > now() - interval '24 hours' AND NOT approved
GROUP BY 1 ORDER BY 2 DESC;

-- ¿El edge sobrevive a los costos reales?
SELECT date_trunc('day', ts) d,
       count(*) n,
       sum(realized_pnl) pnl,
       sum(fee) fees,
       avg(slippage_bps) slip
FROM pilot_b.fills
GROUP BY 1 ORDER BY 1 DESC LIMIT 14;

-- Piloto A: ¿cuánto me cuesta el estratega, y qué tan seguido acierta?
SELECT date_trunc('day', p.ts) d,
       count(*) policies,
       sum(p.cost_usd) llm_cost,
       count(*) FILTER (WHERE NOT p.valid) fallos,
       mode() WITHIN GROUP (ORDER BY p.bias) sesgo_dominante
FROM pilot_a.policies p
GROUP BY 1 ORDER BY 1 DESC LIMIT 14;

-- Salud del collector: el gate de fase 0
SELECT event, count(*), max(gap_seconds)
FROM market.collector_health
WHERE ts > now() - interval '14 days'
GROUP BY 1;
```

---

## Apagar todo, ya

```powershell
# Ctrl+C en cada piloto: cancela órdenes al salir.
# Si no responde, fuerza el kill switch a mano:
"apagado manual de emergencia" | Out-File -Encoding utf8 state\.KILLED_a
"apagado manual de emergencia" | Out-File -Encoding utf8 state\.KILLED_b
```

Después entra a Polymarket y verifica a ojo que no quedó nada abierto.
El archivo impide que los pilotos abran posiciones nuevas, pero **no cierra
las que ya existan**.
