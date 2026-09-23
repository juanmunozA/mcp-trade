**La tabla de consulta. Es lo más cerca de un modelo numérico que tenés.**

Probabilidad medida de que el precio esté más alto 15 minutos después, según
dónde está el RSI de 5m y hacia dónde viene la hora. Los intervalos ya están
ensanchados 1,76× por el agrupamiento de ciclos.

```
                    1h BAJANDO                    1h SUBIENDO
rsi<40      0,75  [0,46–0,91]  n=36        (menos de 5 casos)
rsi 40–50   0,59  [0,36–0,79]  n=51        0,43  [0,16–0,75]  n=21
rsi 50–60   0,36  [0,09–0,75]  n=14        0,43  [0,24–0,65]  n=60
rsi 60–75        (menos de 5 casos)        0,35  [0,15–0,62]  n=40
rsi>75           (menos de 5 casos)        0,45  [0,12–0,84]  n=11
```

## Cómo leerla

**La celda fuerte: RSI bajo 40 con la hora bajando → 0,75.** Es la
combinación más clara que aparece, con 36 casos. Precio castigado en el corto
Y en el mediano, y rebota tres de cada cuatro veces.

**La celda opuesta: RSI 60–75 con la hora subiendo → 0,35.** 40 casos. Es
exactamente la forma de tus doce peores operaciones, y ahí dijiste SUBE.

**RSI sobre 75 se aplana en 0,45.** La reversión deja de funcionar en el
extremo verdadero: ahí ya no es sobrecompra, es una cascada, y las cascadas
siguen. Con volumen sobre 3x, seguí el movimiento.

**Las celdas diagonales (RSI y hora en desacuerdo) están cerca de 0,43–0,45.**
Cuando las dos escalas se contradicen, casi no hay información. Ese es el
caso "no veo nada", y tu número es 0,49 o 0,51.

## Los intervalos, que son la mitad del mensaje

Mirá el ancho. La celda de 0,75 tiene un intervalo de [0,46 a 0,91]: **el
límite inferior toca el 0,46**. Con 36 observaciones y ciclos correlacionados
no se puede decir mucho más.

Por eso la tabla se usa para **elegir el lado**, que es lo que la directriz
exige, y no para justificar convicción alta. Traducí así:

```
celda 0,70 o más   →  tu número 0,60
celda 0,60–0,70    →  tu número 0,56
celda 0,40–0,60    →  tu número 0,49 o 0,51
celda 0,30–0,40    →  tu número 0,44
celda 0,30 o menos →  tu número 0,40
```

**Contraé hacia 0,50.** No es timidez: es que una frecuencia de 0,75 medida
sobre 36 casos correlacionados no es una probabilidad de 0,75. Contraer es lo
correcto cuando la muestra es chica — es lo que hace cualquier estimador
bayesiano razonable.

## El ajuste por flujo

Después de ubicar la celda, movete ±0,02 según `flujo_agresor`: hacia arriba
si es comprador, hacia abajo si es vendedor. El coeficiente de la regresión
dice que aporta, pero la mitad de lo que aporta el RSI.

Nunca dejes que el flujo te cambie el lado. Solo el tamaño del paso.
