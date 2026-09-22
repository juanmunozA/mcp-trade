**Tenés que elegir un lado siempre. No existe quedarse afuera.** La pregunta
no es "¿vale la pena operar?" sino "¿cuál de los dos lados es menos malo
ahora mismo?".

Este orden NO es teoría prestada: es el resultado de 229 predicciones tuyas
resueltas contra el precio real. La versión anterior de esta nota tenía otro
orden y perdió plata. Este es el corregido.

## Orden, del que pagó al que no

**1. Extremo de RSI de 5 minutos.** Lo único que ganó plata con claridad.

```
rsi_5m 25–35   →  después subió 66,7%    comprá
rsi_5m 35–45   →  61,0%                  comprá
rsi_5m 45–55   →  48,2%                  nada, quedate en 0,50
rsi_5m 55–65   →  32,7%                  vendé
rsi_5m 65–75   →  37,5%                  vendé
rsi_5m 75+     →  45,5%                  cuidado, ver abajo
```

Como regla: `<40 comprá, >60 vendé` dio 67% de acierto y +22,96% neto sobre
88 operaciones. Con `volumen_vs_medio` sobre 1,2 subió a 73%.

**Por encima de RSI 75 la regla se rompe** y vuelve a ser moneda al aire. Ahí
ya no es un extremo que revierte: es una cascada, y las cascadas siguen.
Si RSI pasa de 75 **con volumen sobre 3x**, seguí el movimiento en vez de
apostar contra él.

**2. Contradicción entre continuidad y extremo.** Cuando la tendencia de 1h
dice una cosa y el RSI dice la otra, hacele caso al RSI. Ese fue el caso de
tus doce peores operaciones: todas tenían tendencia alcista y RSI alto, y
todas cayeron.

**3. El resto: nada.** Medido, sin adornos:

```
flujo_agresor        −2,6 pp    nulo, y caro
libro_desequilibrio  −0,6 pp    exactamente cero
contagio con BTC     38,5% de acierto en 13 usos
seguir la vela de 5m 47,6%      peor que la moneda
```

Ninguno de estos justifica alejarse de 0,50. Podés usarlos para desempatar
cuando no hay extremo, pero no para tener convicción.

## Cuando de verdad no ves nada

La regla vieja era "seguí las últimas dos velas de 5 minutos". **Medida: 42,9%
de acierto.** Es peor que tirar una moneda y fue tu segundo fundamento más
caro.

La regla nueva: si no hay extremo de RSI, **quedate a 0,49 o 0,51**. Elegiste
lado, que es lo que la directriz exige, y no fingiste una convicción que no
tenés. El daño de una posición a 0,51 mal elegida es el peaje y nada más.

## Calibrá el número, no el lado

El lado lo elegís siempre. La confianza no. Y la confianza no se inventa: se
deriva de la tabla condicional, contraída hacia 0,50 porque la muestra es
chica.

```
sin extremo de RSI            →  0,49 o 0,51
extremo moderado (RSI 40–60)  →  0,44 o 0,56
extremo claro (<40 o >60)     →  0,40 o 0,60
```

**Nunca más allá de 0,35–0,65, y solo llegás ahí si además el movimiento
esperado es grande.** La versión anterior de esta nota permitía 0,35 con
señal normal. Con los intervalos de confianza calculados, eso no está
justificado: la celda más fuerte de toda la tabla tiene un límite inferior
que toca el 0,46.

## Y comprometete con el número

Una señal buena en este oficio mueve la probabilidad 8 o 10 puntos, no 30.
Eso ya está contemplado en la escala de arriba: por eso el máximo es 0,60 y
no 0,80.

Lo que **no** hay que hacer es achicar todavía más porque la comisión no da.
Con entrada forzada el peaje se paga igual elijas el lado que elijas, así que
no entra en esta decisión — ver la nota del peaje. Si la tabla dice 0,35, tu
número es 0,44. Un número honesto es lo único que después se puede medir.
