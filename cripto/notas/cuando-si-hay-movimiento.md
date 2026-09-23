**Casi siempre hay movimiento. Eso ya no es el problema.**

Medido sobre tus 229 predicciones resueltas:

```
movimiento mediano a 15 min       0,478%
movimientos que superaron el peaje (0,10%)   202 de 229  =  88,2%
```

Con los pares volátiles la mediana sube a **0,566%**, casi seis veces la
comisión. La vieja advertencia de "el movimiento típico es 0,077%, menor que
el peaje" era del tiempo en que se operaba BTC. Con estos pares es falsa y
te volvió tímido sin motivo.

**Tu problema no es que no haya movimiento. Es que elegís mal el lado.**

## Los momentos que de verdad pagan

**Extremo de RSI con volumen.** El único que midió ganancia consistente:
RSI 5m fuera de 40–60 con `volumen_vs_medio` sobre 1,2 dio 73,3% de acierto.
Ahí sí vale 0,35 o 0,65.

**Cascadas de liquidación.** Volumen sobre 3x el medio con movimiento de 5m
mayor a 0,3%. Una vez que arranca, sigue: cada liquidación produce la orden
que causa la siguiente. Es la excepción a la regla de reversión — acá el
movimiento continúa. Se distingue del extremo normal por el volumen: 1,2x es
reversión, 3x es cascada.

**Financiación extrema.** Avisa de movimiento grande en cualquier dirección,
no de dirección. Sirve para subir el tamaño esperado, no para elegir lado.

## El volumen anuncia el tamaño, no el lado

```
volumen_vs_medio < 0,8   →  |movimiento| mediano 0,416%
volumen_vs_medio 1,2–2   →  |movimiento| mediano 0,755%
```

Casi el doble. Usalo para saber cuánto vas a jugarte, nunca para saber hacia
dónde.

## Lo que NO es señal, confirmado con datos

Un cruce de medias: `ema9_vs_ema21` midió **−18,3 pp**, al revés. Una
tendencia de 1h de la que ya habla todo el mundo: `cambio_1h` midió **−22,7
pp**, al revés. El libro cargado: **−0,6 pp**, cero.

Todo eso ya está en el precio. Actuar sobre ello es pagar comisión por nada,
y peor: es comprar justo donde el que ya está adentro quiere vender.
