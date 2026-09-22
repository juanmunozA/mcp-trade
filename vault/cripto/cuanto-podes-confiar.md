**Las reglas que te pasaron suenan medidas. Ninguna sobrevive una prueba
estadística seria.** Esta nota existe para que no te vuelvas experto en ruido.

## Tus 237 observaciones son 77

Los ocho pares se predicen en el mismo instante y se mueven juntos. La
correlación intra-ciclo del resultado es **ρ = 0,303**. Con ciclos de 7,9
pares:

```
efecto de diseño = 1 + (m − 1)·ρ = 1 + 6,9 × 0,303 = 3,09
n efectivo = 237 / 3,09 = 77
```

**Todo intervalo de confianza hay que ensancharlo 1,76 veces.** Un 66% de
acierto sobre 91 filas no tiene la precisión de 91 datos: tiene la de unos 30.

Esto no es un tecnicismo. Es la diferencia entre "descubrí algo" y "vi una
cara en una nube".

## Las reglas, con el intervalo honesto

```
regla                       acierto   IC 95% agrupado     neto
RSI5m <40 compra / >60 vende  65,9%   [53,4 ; 78,5]     +20,20%
RSI5m <50 compra / >50 vende  59,9%   [53,5 ; 66,3]     +18,14%
contrario a cambio_1h         60,3%   [53,9 ; 66,8]     +14,31%
seguir flujo_agresor          51,9%   [41,6 ; 62,2]     −42,38%
el agente (lo que hizo)       48,5%   [41,5 ; 55,5]     −29,34%
```

Los límites inferiores están por encima de 50%. Pero **el umbral que importa
no es 50%: es p\* = 60,2%** — ver la nota del peaje. Y `53,4 < 60,2`.

**Ninguna regla demostró ser rentable. Solo demostraron no ser una moneda.**

## Corrección por pruebas múltiples

Se probaron unas doce reglas sobre los mismos datos. Con α = 0,05 se esperan
0,6 falsos positivos solo por azar — o sea que encontrar "una regla que
funciona" era lo esperable aunque no hubiera ninguna.

Bonferroni: α = 0,0042, z crítico = 2,86.

```
regla            contra 50%          contra p* = 60,2%
RSI <40/>60      z = +1,82  no pasa   z = +0,65  NO PASA
RSI <50/>50      z = +1,77  no pasa   z = −0,05  NO PASA
contrario a 1h   z = +1,84  no pasa   z = +0,02  NO PASA
```

**Nada pasa. Ni siquiera contra la moneda al aire.**

## La reversión tampoco está probada

```
corr(retorno 15m previo, retorno 15m siguiente) = −0,137
error estándar agrupado 0,080    t = −1,72
IC 95% = [−0,292 ; +0,019]   ← cruza el cero
```

Negativo, en la dirección de la reversión, pero **no distinguible de cero**.
Solo `cambio_4h` llega a t = −2,25, y eso con doce pruebas encima no
significa nada.

## Tu Brier, descompuesto

```
Brier = fiabilidad − resolución + incertidumbre
0,2511 =   0,0034  −   0,0011   +   0,2499
```

- **fiabilidad 0,0034**: tu calibración está *bien*. Cuando decís 0,60, pasa
  cerca del 60%. Ese nunca fue el problema.
- **resolución 0,0011**: prácticamente cero. **No separás los casos.** Tus
  probabilidades no distinguen lo que va a subir de lo que va a bajar.

Esa es la enfermedad, con nombre. No sos sobreconfiado: sos ciego. La cura no
es moderar los números, es encontrar una variable que discrimine.

## Lo único que se validó fuera de muestra

Una regresión logística sobre seis variables, entrenada dejando un ciclo
afuera por vez y evaluada en el ciclo que nunca vio:

```
acierto fuera de muestra 57,4%    Brier 0,2539    neto +19,54%
```

57,4% es mejor que tu 48,5% y sigue **por debajo del 60,2% que hace falta**.
Y el Brier de 0,2539 es peor que el del azar, aunque el neto sea positivo —
porque acierta donde el movimiento es grande. Es señal débil, real, e
insuficiente.

## Qué hacer con todo esto

**Usá las reglas, porque son lo mejor que hay, y no creas que funcionan.**

Las dos cosas a la vez. Aplicá la reversión de RSI porque es la única con
respaldo, y mantené la convicción baja porque ese respaldo no aguanta una
prueba seria. Un 0,60 está justificado; un 0,35 no lo está con estos datos.

Y mirá tu boletín. Si el neto acumulado baja mientras seguís estas reglas, no
es mala racha: es que la regla nunca estuvo probada y ahora lo estás viendo.
