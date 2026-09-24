# Conectar el bot a Tickmill

## Lo primero: la cuenta que tienes en TradingView no sirve

Tickmill tiene **dos familias de cuenta que no se hablan entre si**:

| | plataforma | ¿la puede manejar un bot? |
|---|---|---|
| **Tickmill Trader** (DXtrade) | TradingView, web | **no** |
| **MT4 / MT5** | MetaTrader | **si**, con Python |

La que usas en TradingView es del primer tipo. Para automatizar hace falta
abrir una **cuenta demo de MT5 aparte**, gratis y al instante desde el
Client Area. No sincronizan: lo que el bot abra en MT5 no aparece en
TradingView, y al reves.

Y tampoco hay atajo por API: Tickmill ofrece FIX API **solo en Tickmill
Prime**, con 500.000 USD de saldo minimo y 5.000 al mes de comisiones. En
retail no existe, ni REST ni FIX.

## Antes de empezar: TradingView no puede hacer esto

TradingView **no tiene API para enviar órdenes**. Lo único que ofrece para
automatizar es mandar un webhook desde una alerta de Pine Script a un
servidor propio — y Pine corre dentro del gráfico, sin acceso a Postgres ni
a la serie de otro activo, que es lo que necesita la SMT divergence del
capítulo 15.5. Traducir los nueve modelos a Pine sería reescribirlo todo
para perder la mitad.

Y hay un segundo problema: **la integración de Tickmill con TradingView solo
funciona con cuentas "Tickmill Trader"**, que son un tipo distinto de las
MT4/MT5. No sincronizan entre sí. Una orden que el bot abra por MT5 no
aparece en el panel de TradingView.

Así que el camino es MT5, que además es el primero que la guía lista en el
capítulo 18.5: *«Python + paquete MetaTrader5: permite leer datos y enviar
órdenes a una terminal MT5 desde Python»*.

TradingView te sigue sirviendo para mirar gráficos a mano. Para ver lo que
hace el bot está el tablero en `/ui`, que muestra cada orden con su modelo,
sus confluencias y su resultado.

---

## El error que esto corrige

El sistema analizaba `GC=F` — el futuro de oro de COMEX, bajado de Yahoo —
y se pretendía operar XAUUSD. **No son el mismo instrumento.** Medido el
2026-09-23:

```
GC=F      4.325,50     lo que analizaba
XAUUSD    4.287,08     lo que se opera
          ────────
          38,42 dólares de diferencia
```

Los stops de este sistema van de 1 a 15 dólares. Un desfase de 38 es varias
veces el riesgo entero de la operación: el stop quedaría del otro lado del
precio y el objetivo en otro sitio.

Por eso `materias.operar` lee las velas **del broker**, no de Yahoo. Mismo
instrumento, mismo precio, mismo spread, mismo horario.

---

## Dónde corre cada cosa

El paquete `MetaTrader5` de Python **solo existe para Windows**. No hay
versión de Linux, así que el bot que manda órdenes no puede correr en
Render. Quedan dos procesos:

| dónde | qué corre | qué hace |
|---|---|---|
| Render | `materias.correr` | papel sobre GC=F + el tablero, 24/5, sin depender de tu PC |
| tu PC Windows | `materias.operar` | órdenes reales sobre XAUUSD en Tickmill, con MT5 abierto |

Los dos escriben en la misma base de Neon, así que el tablero muestra ambos
y los separa: una operación del broker vale más como evidencia que una de
papel, porque pagó spread de verdad y se llenó a un precio de verdad.

**Tu PC tiene que estar encendido y con MT5 abierto** para que el segundo
opere. Si se apaga, las órdenes ya colocadas siguen vivas en el servidor de
Tickmill con su SL y su TP — eso no se pierde —, pero no se abren nuevas y
la reconciliación se pone al día cuando vuelva.

---

## Pasos

### 1. Cuenta MT5 en Tickmill

En el Client Area de Tickmill, abre una **cuenta demo de MT5**. No sirve la
que ya tienes conectada a TradingView: esa es "Tickmill Trader", otro tipo.

Apunta tres cosas: **número de cuenta**, **contraseña** y **nombre del
servidor** (algo como `Tickmill-Demo`).

### 2. Instalar MT5

Descarga la terminal desde Tickmill, instálala, entra con esas credenciales.

Después, dentro de MT5:

- **Herramientas → Opciones → Asesores Expertos** → marca **«Permitir
  trading algorítmico»**. Sin esto el bot conecta pero toda orden se
  rechaza.
- **Ver → Observación de Mercado** → busca XAUUSD y asegúrate de que está
  en la lista. Si no aparece, clic derecho → Mostrar todo.

El nombre exacto del símbolo cambia entre brokers y entre tipos de cuenta:
puede ser `XAUUSD`, `XAUUSD.r`, `GOLD`… El paso 4 te lo dice.

### 3. Dependencias

```bash
pip install -r requirements.txt
```

### 4. Comprobar antes de operar

```bash
python -m materias.broker
```

Esto mira todo lo que suele fallar en el primer intento contra un broker de
verdad, y lo cuenta junto en vez de que aparezca de a uno:

- si conecta y si el trading algorítmico está permitido
- si la cuenta es demo o real
- el nombre exacto del símbolo (y si no lo encuentra, te lista los parecidos)
- **la hora del servidor, medida y no supuesta** — el capítulo 10 avisa que
  marcar las killzones corridas una hora las vuelve inútiles, y el servidor
  de un broker suele ir en GMT+2 o +3
- tamaño de contrato, lote mínimo y paso
- **`trade_stops_level`**: la distancia mínima a la que el broker acepta un
  stop. Si un stop del sistema cae por debajo, la orden se rechaza entera
- si el volumen es real o de ticks (en un CFD es de ticks, cap. 9.1)

Si el símbolo se llama distinto, ponlo en el `.env`:

```
MT5_SIMBOLO=XAUUSD.r
```

### 5. Correr en seco

Hay dos cosas que puedes correr contra el broker, y hacen cosas distintas:

```bash
# El piloto: cada 4 horas el AGENTE elige direccion leyendo la guia entera,
# entra si o si, y la posicion vive 3h50m. Es lo que corre en Render, pero
# contra tu XAUUSD en vez de contra el futuro de Yahoo.
PILOTO_BROKER=1 python -m materias.piloto

# El sistema normal: los nueve modelos deterministas del capitulo 11, que
# solo entran cuando el checklist del 11.9 pasa entero. Opera poco.
python -m materias.operar
```

Los dos **por defecto no mandan nada**. Analizan, calculan el lote,
deciden la orden y la registran con el motivo. Deja uno corriendo al
menos una sesion entera y mira en el tablero que habria hecho.

**Por defecto no manda nada.** Analiza, calcula el lote, decide la orden y
la registra como `no_enviada`, con el motivo. Déjalo así al menos una sesión
de Londres y otra de NY, y mira en el tablero qué habría mandado.

Ahí es donde se ven los problemas que sólo aparecen en vivo: un stop más
cerca del mínimo del broker, un lote por debajo del mínimo, un símbolo que
no cotiza a esa hora.

### 6. Soltarlo

Cuando lo de arriba se vea bien, en el `.env`:

```
MATERIAS_ENVIAR=1
```

A partir de ahí manda órdenes de verdad **a la cuenta demo**.

Si la cuenta fuera real, hace falta además `MATERIAS_ACEPTO_REAL=1`, y el
proceso se niega a arrancar sin ella. No es paranoia: el capítulo 20.1 pone
el forward test en demo como paso 7 y el dinero real después, y el backtest
todavía no pasa tres de los ocho listones del capítulo 18.7 — n=153 de 300,
Sharpe 0,14 de 1, y una sola operación vale el 54 % del resultado.

---

## Variables

```bash
# Cuenta. Se pueden omitir si la terminal ya está abierta y logueada.
MT5_LOGIN=12345678
MT5_CLAVE=...
MT5_SERVIDOR=Tickmill-Demo
MT5_RUTA=                    # ruta al terminal64.exe, si hay varias terminales

MT5_SIMBOLO=XAUUSD           # el paso 4 te dice el nombre exacto
MT5_MAGIA=20260923           # marca las órdenes del bot; no lo cambies a mitad
MT5_COMISION=0               # por lote, ida y vuelta. Raw/Pro cobran; Classic no

MATERIAS_ENVIAR=             # 1 para que mande de verdad
MATERIAS_ACEPTO_REAL=        # 1 sólo si la cuenta es real
MATERIAS_CADA_S=300

DATABASE_URL=postgres://...  # la misma de Render, para compartir tablero
```

`MT5_COMISION` importa más de lo que parece. En Tickmill las cuentas Raw/Pro
cobran comisión y tienen spread casi cero; las Classic no cobran y la meten
en el spread. Ponerla en 0 teniendo una Raw hace que la expectativa medida
salga mejor de lo que es, que es el error nº 9 del capítulo 20.4: *«ignorar
el spread, comisiones y swap al evaluar resultados»*.

---

## Horarios

El oro tiene una pausa diaria. Cierra a las **17:00 ET** y reabre a las
**18:00 ET**, que en Colombia son las **17:00** (Colombia va una hora atrás
de Nueva York en horario de verano).

Eso no choca con nada: la última ventana operable del sistema es `ny_pm`,
que termina a las 16:00 ET. El bot ya está fuera de ventana cuando llega la
pausa.

Las ventanas donde sí opera, en hora de Nueva York:

| ventana | ET | Colombia |
|---|---|---|
| London Killzone | 02:00–05:00 | 01:00–04:00 |
| NY AM | 07:00–10:00 | 06:00–09:00 |
| London Close | 10:00–12:00 | 09:00–11:00 |
| NY PM | 13:30–16:00 | 12:30–15:00 |

Fuera de ahí el ciclo sigue corriendo pero sólo reconcilia: *«fuera de ellas
el precio suele lateralizar»* (cap. 10).

---

## Lo que no está probado

`broker.py` y `operar.py` se escribieron **sin una terminal MT5 delante**.
La lógica de análisis es la misma que lleva 153 operaciones de backtest,
pero el trozo que habla con el broker no se ha ejecutado nunca.

Lo que más probablemente falle en el primer intento:

- el nombre del símbolo
- el modo de llenado que acepta Tickmill (`_modo_llenado` elige entre los
  que el símbolo declara, pero eso sólo se ve en vivo)
- `trade_stops_level` mayor que algún stop del sistema

Los tres los detecta `python -m materias.broker`, y el modo seco del paso 5
los saca sin arriesgar nada. Hazlos en ese orden.
