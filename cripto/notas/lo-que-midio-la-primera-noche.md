**Esto no es teoría. Son 229 predicciones tuyas, resueltas contra el precio
real, la noche del 21 de septiembre de 2026.** Perdiste. Leé por qué antes de
volver a apostar.

```
agente    46,1% de acierto    Brier 0,2550    neto −28,97%
inercia   (el tonto)          Brier 0,2480    ← te ganó
azar                          Brier 0,2500    ← también te ganó
```

Le perdiste a la moneda al aire y a la estrategia más tonta que existe. Eso
no es mala suerte: es una forma sistemática de equivocarse, y está
identificada.

## Lo que te mató: compraste techos

Tus doce peores operaciones tienen la misma forma. Precio subiendo en 5m,
subiendo en 1h, RSI alto, flujo agresor comprador — y vos dijiste SUBE.

```
SUI   RSI 89  1h +4,1%  flujo +0,29  →  dijiste 0,650  →  cayó 3,5%
UNI   RSI 73  1h +3,5%  flujo +0,18  →  dijiste 0,650  →  cayó 2,0%
NEAR  RSI 70  1h +3,5%  flujo +0,36  →  dijiste 0,560  →  cayó 2,0%
```

Tus mejores fueron exactamente lo contrario:

```
PEPE  RSI 92 sobrecomprado  →  dijiste 0,360  →  cayó 2,0%   +2,0%
XRP   RSI alto, máximo 24h  →  dijiste 0,350  →  cayó 1,9%   +1,9%
```

Vos ya sabías hacerlo bien. Lo hiciste dos veces y ganaste las dos. El resto
del tiempo hiciste lo contrario.

## Cada indicador de tendencia apuntó al revés

Mirá la columna de la derecha. Es cuánto más subió el precio cuando el
indicador era positivo versus cuando era negativo. Debería ser positiva:

```
precio_vs_ema50      43,1% vs 65,5%    −22,4 pp
cambio_1h            38,3% vs 61,0%    −22,7 pp
ema9_vs_ema21        41,7% vs 60,0%    −18,3 pp
cambio_15m           40,8% vs 57,9%    −17,1 pp
flujo_reciente       45,7% vs 55,3%     −9,6 pp
flujo_agresor        49,4% vs 52,0%     −2,6 pp
libro_desequilibrio  48,1% vs 48,7%     −0,6 pp
```

Todas negativas. No hubo una sola variable de continuidad que funcionara.
`libro_desequilibrio` fue exactamente cero, como decía la teoría. Y
`flujo_agresor` — el que la literatura llama el predictor más sólido a
horizonte corto — también fue cero.

## Lo único que pagó

```
RSI 5m <40 compra / >60 vende     67,0% acierto    +22,96% neto   n=88
contrario a cambio_1h             61,1%            +18,60%        n=229
RSI extremo + volumen >1,2x       73,3%            +10,00%        n=15
```

Y el gradiente completo, que es limpio y ordenado:

```
rsi_5m 25–35   →  después subió el 66,7%
rsi_5m 35–45   →  61,0%
rsi_5m 45–55   →  48,2%
rsi_5m 55–65   →  32,7%
rsi_5m 65–75   →  37,5%
rsi_5m 75+     →  45,5%   ← acá se rompe: es la cascada
```

Por encima de 75 la reversión deja de funcionar. Eso tiene sentido: ahí ya no
es un extremo, es una cascada de liquidaciones, y esas siguen.

## La advertencia, que importa igual que los números

**Son 3 horas y media de una sola noche.** Un mercado que revierte a 15
minutos durante tres horas no es una ley del universo: es un régimen. Puede
darse vuelta mañana, y si tratás esto como regla fija vas a hacer exactamente
lo que la nota de sobreajuste describe.

Lo que sí es permanente y no depende del régimen:

- que ocho pares correlacionados son una apuesta, no ocho
- que acertar seguido y perder plata es perfectamente posible
- que subestimaste el tamaño del movimiento por más del doble

Lo que es del régimen y hay que re-medir: que la reversión ganó.

**La forma correcta de usar esto: cuando la señal de continuidad y el extremo
se contradigan, hacele caso al extremo.** No "sé siempre contrario".
