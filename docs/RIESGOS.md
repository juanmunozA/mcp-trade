# Riesgos

Documento vivo. Cuando algo salga mal, vuelve aquí y añade la fila.

---

## 1. Acceso y regulación

Polymarket **no geobloquea IPs colombianas**, pero Coljuegos ordenó a los ISP
bloquear el acceso en septiembre de 2025 por operar sin licencia local. No hay
legislación específica de mercados de predicción en Colombia; el regulador los
trata como juego en línea no autorizado.

Los ToS de Polymarket prohíben evadir geobloqueos, y la plataforma reporta
detección mejorada de evasión. Consecuencias documentadas: cuenta en modo
*close-only*, o fondos congelados.

**El riesgo concreto no es penal — es de contraparte.** Si la cuenta se congela,
no hay a quién reclamar. Por eso:

- Dimensiona el capital asumiendo que **puede quedar atrapado completo**.
- No acumules saldo: retira las ganancias periódicamente en lugar de dejarlas
  componiendo en la plataforma.
- Wallet dedicada por piloto, sin conexión a tus fondos principales.
- Ten una ruta de salida definida *antes* de depositar, no después.

Este repo no incluye ni incluirá herramientas de evasión de detección
(rotación de proxies residenciales, spoofing de fingerprint, etc.). Si vas a
usar VPN, la decisión y el riesgo son tuyos; lo único relevante desde el punto
de vista de ingeniería es que la inconsistencia (IP que salta de país, patrones
de depósito/retiro irregulares) es más señal que el uso en sí.

**Impuestos:** las ganancias de trading son renta gravable en Colombia. Tu tabla
`fills` es tu contabilidad desde el día uno. No la borres.

---

## 2. Técnicos

| Riesgo | Impacto | Mitigación en este repo |
|---|---|---|
| Loop infinito de órdenes | Ruina en minutos | `max_orders_per_min` en el kernel · `test_rate_limit_corta_el_loop_infinito` |
| Desincronización posición local vs exchange | Doble exposición silenciosa | `reconcile()` cada 10s, dispara kill switch |
| Datos congelados con posición abierta | Exposición a ciegas | `watchdog_loop` → cancel_all + kill |
| Reinicio que borra el estado de riesgo | "Se cayó, lo reinicié, siguió perdiendo" | Kill switch persistido en `state/.KILLED_*` |
| Excepción no capturada en el kernel | Órdenes sin validar | `check()` atrapa todo y hace *fail closed* |
| Precio alucinado / fat finger | Orden absurda | `max_price_deviation` contra el mid del book |
| Deploy con posición abierta | Cualquier cosa | Regla operativa: ver RUNBOOK |
| Reloj desincronizado | Firmas rechazadas | NTP obligatorio en el VPS |

---

## 3. De mercado

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Overfitting del backtest | Falso positivo → pérdida real | Walk-forward; el motor de replay usa `cost_to_fill`, no el mid |
| Competencia más rápida | El edge atómico no existe para ti | Mide tu fill rate. Si cae, ya perdiste la carrera |
| Residual direccional (piloto B) | 30 pérdidas se comen 1.31¢ × 30 sets | `max_imbalance_shares` → fuerza rebalanceo |
| Régimen cambia | La estrategia deja de servir | El piloto A lo detecta y va a `flat`; el B no lo detecta — vigílalo tú |
| Liquidez fantasma | Barres el book y mueves el precio | `depth_fraction = 0.25`: nunca tomamos más de ¼ de lo visible |

---

## 4. Específicos de la IA (piloto A)

| Riesgo | Impacto | Mitigación |
|---|---|---|
| **Alucinación de ticker o tamaño** | Orden absurda | `messages.parse` con esquema Pydantic + `clamp()` + whitelist del kernel |
| **Inyección de prompt vía preguntas de mercado** | Agente manipulado | Datos de terceros dentro de `<untrusted_market_data>`; el kernel nunca lee texto del modelo |
| **Espiral de revancha** (pierde → arriesga más) | Ruina | Los límites viven en env vars; `Settings.redacted()` nunca los expone al modelo |
| **Sobreconfianza narrativa** | Tesis preciosas, PnL feo | Journal obligatorio; el prompt exige nombrar qué lo refutaría |
| **No determinismo** | No puedes depurar una decisión | `pilot_a.policies` guarda prompt + respuesta cruda + tokens + costo |
| **El estratega se cae o da timeout** | Operar con política vieja a ciegas | `SAFE_DEFAULT` = flat. Nunca se hereda exposición |

El peor caso de una inyección exitosa debe ser *"el agente opera mal dentro de
sus límites"*, nunca *"el agente vacía la wallet"*. Si algún cambio rompe esa
propiedad, es un bug crítico.

---

## 5. Seguridad

| Riesgo | Mitigación |
|---|---|
| Private key en el repo | `.gitignore` cubre `.env`. Revisa `git status` antes de cada commit |
| Una wallet para ambos pilotos | Wallets separadas: `PILOT_A_*` y `PILOT_B_*`. Un piloto comprometido no toca al otro |
| Fondos de más en la wallet caliente | Solo el capital del piloto. El resto, en frío |
| VPS comprometido | SSH solo con llave, fail2ban, firewall default-deny |
| El LLM ve secretos | El estratega recibe `Settings.redacted()` y el snapshot del kernel. Nunca keys, nunca límites |

---

## 6. Sesgos del operador (el riesgo que nadie documenta)

| Sesgo | Cómo se ve | Antídoto |
|---|---|---|
| Escalar tras una buena semana | "Va subiendo, le meto más" | `scripts/check_gate.py` exige \|t\| > 2 y 100+ trades |
| Desarmar el kill switch sin investigar | `rm state/.KILLED_b` y a seguir | `reset_kill()` exige confirmación explícita; el evento queda en `risk_events` |
| Tunear hasta que el backtest dé bonito | Overfitting con pasos extra | Out-of-sample sagrado. Si lo tocaste, ya no es out-of-sample |
| Seguir con un sistema muerto | "Ya invertí tres meses" | Fase 4 dice: escalar **o matar**. Matar es un resultado válido |
