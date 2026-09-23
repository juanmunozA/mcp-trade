**El flujo agresor solo no vale nada. Condicionado al RSI, es la variable más
fuerte que tenés.** Las dos afirmaciones son ciertas y entender por qué es lo
que separa a un operador de alguien que mira indicadores.

## Solo, midió cero

```
flujo_agresor positivo  →  después subió el 49,4%
flujo_agresor negativo  →  después subió el 52,0%
                           diferencia: −2,6 pp

seguirlo como estrategia  →  51,9% de acierto, −42,38% neto
```

Acertaba la mitad de las veces y fue lo más caro de todo lo probado: acertaba
los movimientos chicos y erraba los grandes.

## Pero en la regresión múltiple es el más significativo

Regresión logística con seis variables, errores agrupados por ciclo:

```
variable                coef      z       odds por 1 desvío
rsi_5m                 −0,602   −2,03          0,55  *
flujo_agresor          +0,328   +2,96          1,39  *
volumen_vs_medio       −0,264   −1,20          0,77
cambio_1h              +0,170   +0,68          1,19
pos_en_rango_24h       +0,086   +0,43          1,09
libro_desequilibrio    −0,079   −0,42          0,92
```

`flujo_agresor` tiene el z más alto de todos: **+2,96**. Con signo positivo,
el que la teoría predice.

## Por qué se contradicen: supresión

El flujo agresor y el RSI están correlacionados — cuando el precio viene
subiendo, el RSI está alto **y** el flujo es comprador, porque son dos formas
de medir lo mismo que acaba de pasar.

El RSI alto predice caída (reversión). El flujo comprador predice suba
(continuación). **Cuando mirás el flujo solo, estás mirando los dos efectos
sumados, y se cancelan.** Por eso midió −2,6 pp: no es que no haya señal, es
que hay dos señales opuestas encimadas.

Al controlar por dónde está el RSI, el efecto del flujo aparece limpio.

## La regla que sale de esto

**Nunca uses el flujo agresor solo. Usalo siempre después de ubicar el RSI.**

```
RSI bajo (<40) + flujo comprador   →  las dos apuntan arriba. Es lo mejor
                                       que vas a ver. 0,60–0,65.
RSI bajo + flujo vendedor          →  se contradicen. 0,53. La reversión
                                       manda apenas.
RSI alto (>60) + flujo vendedor    →  las dos apuntan abajo. 0,35–0,40.
RSI alto + flujo comprador         →  se contradicen, y ESTA es la trampa
                                       que te costó la primera noche. 0,47.
```

La última línea merece énfasis. Tus doce peores operaciones fueron todas RSI
alto con flujo comprador, y en todas seguiste al flujo. El coeficiente dice
que el flujo aporta, pero el del RSI es más grande en valor absoluto
(−0,602 contra +0,328): **cuando se pelean, gana el RSI, por casi el doble.**

## Lo que sí se confirmó de la teoría

`libro_desequilibrio` midió −0,079 con z = −0,42: **cero, solo y acompañado.**
Órdenes límite que se cancelan y no cuestan nada de poner. La distinción
entre profundidad del libro y flujo ejecutado era correcta — ignorá el libro.

## La advertencia

El pseudo-R² de McFadden de toda la regresión es **0,059**. El modelo entero,
con las seis variables, explica el 6% de lo que pasa. Un z de +2,96 sobre
n efectivo de 77 y con doce hipótesis probadas encima no es un
descubrimiento: es una pista que hay que volver a medir.

**Y se volvió a medir.** Con 24 operaciones más, el mismo coeficiente cayó de
z = +2,96 a **z = +1,46**: dejó de ser significativo. El del RSI se mantuvo
en −2,16.

Guardá ese episodio, porque es la lección entera de este proyecto en un solo
número. Un hallazgo que se evapora al agregar el 10% de datos nunca fue un
hallazgo. **El flujo agresor sigue sin justificar nada por sí solo; el ajuste
de ±0,02 se queda porque es inofensivo, no porque esté probado.**
