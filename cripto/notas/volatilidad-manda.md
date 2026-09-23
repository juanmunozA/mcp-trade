**Operás los pares que se mueven, no los famosos.** La razón es aritmética: la
comisión es fija (0,10%) y el movimiento no.

Medido sobre 500 velas de 15 minutos, antes de empezar:

```
NEAR    0,599%   →  seis veces la comisión
ENA     0,557%
UNI     0,468%
SUI     0,360%
AVAX    0,308%
...
BTC     0,090%   →  MENOS que la comisión
```

Y después, medido sobre las 165 operaciones reales con esos pares:

```
mediana realizada   0,566%      media   0,787%
```

La predicción se cumplió. Con estos pares hay movimiento de sobra: el 88% de
las veces superó el peaje. **La restricción ya no es el tamaño del
movimiento — es acertar el lado.**

## Subestimaste el tamaño por más del doble

```
lo que dijiste que se movería (mov_esperado)   0,248% promedio
lo que se movió de verdad                      0,566% mediana
```

Eso tuvo consecuencia directa: como el sistema reetiqueta a `sin_senal`
cualquier fundamento cuyo movimiento esperado no pague la comisión, y vos
estimabas chico, terminaste declarando "no veo nada" en 63 casos.

Peor: creer que el movimiento es de 0,25% cuando es de 0,57% te hace apostar
a 0,52 cuando la situación pedía 0,60. Perdiste convicción donde sí la había.

**Ancla nueva: con estos pares, el movimiento base a 15 minutos es 0,5%, no
0,077% ni 0,25%.** Si tu estimación queda muy por debajo de eso, revisala.

## Pero la moneda va en las dos direcciones

Más volatilidad también significa que equivocarse duele seis veces más. Lo
viste: tu peor operación fue −3,5% en quince minutos. Con estos pares el
acierto importa de verdad; antes daba casi igual porque perdías igual.

**El spread es más ancho.** El costo real está por encima del 0,10% teórico.
Si el movimiento es de 0,6% sigue valiendo, pero no asumas que el peaje es el
de BTC.

**Se mueven juntos.** Es lo bastante importante como para tener nota propia:
ver ocho apuestas son una.
