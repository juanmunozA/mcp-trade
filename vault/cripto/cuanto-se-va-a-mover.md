**La mitad de la decisión es el tamaño, no el lado.** Y el tamaño sí se puede
estimar con una fórmula, porque depende de cosas observables.

## La distribución real, medida sobre 237 operaciones

```
p25 = 0,228%      p50 = 0,488%      p75 = 0,998%      p90 = 1,743%
media 0,703%   (mayor que la mediana: la cola pesa)
```

Si no sabés nada más, **usá 0,49%**. Es la mediana y es mejor ancla que
cualquier corazonada. La primera noche estimabas 0,25% y te quedabas corto
por más del doble en casi todos los casos.

## La regresión

```
log|mov_15m| = −0,402 + 0,665·log(volatilidad_pct)
                      + 0,178·log(volumen_vs_medio)
                      + 0,004·|cambio_15m|

R² = 0,130      corr(predicho, real) = 0,368
```

Qué dice cada coeficiente:

- **volatilidad_pct manda.** El exponente 0,665 significa que si la
  volatilidad se duplica, el movimiento esperado crece un 59% (2^0,665).
  Es de lejos el término más fuerte.
- **el volumen ayuda poco.** Exponente 0,178: duplicar el volumen agrega 13%
  al tamaño esperado. Real pero chico.
- **el movimiento previo no aporta nada.** 0,004 es ruido. Que se haya movido
  mucho recién no dice que se vaya a mover mucho ahora.

## La regla de bolsillo

```
|movimiento esperado| ≈ volatilidad_pct × 0,83
```

Con `volatilidad_pct = 0,6` esperás 0,50%. Con 1,2 esperás 1,00%. Es una
cuenta de cabeza y captura casi todo lo que captura la regresión.

## Lo que el R² de 0,13 significa

**Que el 87% del tamaño del movimiento es impredecible.** No es un defecto de
la regresión: es la naturaleza del problema. Podés estimar el promedio, nunca
el caso particular.

Consecuencia práctica: **nunca apuestes como si supieras que el movimiento va
a ser grande.** Sabés que la distribución tiene una mediana de 0,49% y una
cola larga. Eso es todo lo que sabés, y alcanza para calcular tu `p*`.

## Cómo usarlo, en orden

1. Mirá `volatilidad_pct` y multiplicá por 0,83. Ese es tu `m`.
2. Ajustalo un poco hacia arriba si `volumen_vs_medio` está sobre 1,5.
3. Calculá `p* = 0,5 + 0,10/(2m)` — ver la nota del peaje.
4. Si tu señal no te lleva por encima de ese `p*`, tu número es 0,49 o 0,51.

Declarar un `movimiento_esperado_pct` honesto no es burocracia: es lo que
después permite medir si tus estimaciones sirven. La primera noche la
comparación fue 0,253% dicho contra 0,566% real, y ese número solo apareció
porque estaba anotado de antemano.
