# MCP Trade

Ayuda para operar XAUUSD (oro) en MT5. Se responde en español.

## Cómo correr las cosas

- Python siempre con `.venv\Scripts\python.exe` (el Python del sistema no tiene MetaTrader5).
- Lectura en vivo: `materias.red.terminal.conectar()` se conecta por ruta, sin login. No usar scripts que hagan login: cambian la cuenta abierta en el terminal del usuario.
- `terminal.velas(mt5, tf, n)` devuelve solo velas cerradas; `copy_rates_from_pos` incluye la vela viva.
- Hora NY = hora del servidor − 7 h. El día del servidor empieza a las 17:00 NY.

## Lo medido (no repetir el estudio, leer su JSON o su `texto()`)

Cada estudio de `materias/red/` guarda su resultado en un `.json` con el mismo nombre y lo resume en `texto()`, que entra en el contexto del agente (`consejo.texto`). `evolucionar.py` los vuelve a medir cada semana.

| Pregunta | Estudio | Resultado |
|---|---|---|
| ¿Hasta dónde llega un rebote o un tramo? (15M, 1H, 4H) | `estaciones.py` | Tramo ~4,7 ATR, rebote ~3,8 ATR del marco; los niveles (OB, FVG, pivotes, redondos, Fibonacci) no lo frenan más que el azar |
| Profundidad del rebote en % de la pierna (5M, 15M) | `largo_rebote.py` | Pierna 4-6 ATR rebota ~82 %, 10+ ATR ~29 % |
| ¿Algún rasgo predice que rebote? | `investigador.py` | Ninguno pasa con entrada honesta al cierre |
| Entrar en el retroceso | `retroceso.py` | Solo gana la venta al 62-79 % en killzone |
| UT Bot con ventaja | `ventaja.py` | 30M a=3 ATR 10 + EMA 200 + filtro H4, salida en señal contraria |
| Vacíos, OB y extremos de 15M | `zonas.py` | Ver `zonas.json` |
| Giros del día | `autopsia.py` | Ver `autopsia.json` |

Proyectar siempre en ATR del momento, no en dólares fijos, y dar zonas de ~1 ATR.
