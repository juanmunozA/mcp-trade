**Toda la decisión se reduce a una fórmula. Aprendétela porque decide todo lo
demás.**

```
EV = m·(2p − 1) − c
```

`m` es cuánto se va a mover el precio en valor absoluto, `p` es tu
probabilidad de acertar el lado, `c` es la comisión (0,10%).

Despejando el punto donde empatás:

```
p* = 0,5 + c / (2m)
```

## El número que importa

Con la mediana medida de estos pares, `m = 0,488%`:

```
p* = 0,5 + 0,10 / (2 × 0,488) = 0,602
```

**Con entrada forzada necesitás acertar el 60,2% solo para no perder.** No el
51%. El 60,2%.

Vos acertaste el 48,5%. Te faltan 11,7 puntos porcentuales, que en este
oficio es una distancia enorme.

## La tabla completa — consultala antes de alejarte de 0,50

EV en % por operación:

```
                 50%      55%      58,8%    60%      65%      70%
m = 0,20%      −0,100   −0,080   −0,065   −0,060   −0,040   −0,020
m = 0,40%      −0,100   −0,060   −0,030   −0,020   +0,020   +0,060
m = 0,566%     −0,100   −0,043    0,000   +0,013   +0,070   +0,126
m = 0,80%      −0,100   −0,020   +0,041   +0,060   +0,140   +0,220
m = 1,20%      −0,100   +0,020   +0,111   +0,140   +0,260   +0,380
```

Leela así: **en un par que se mueve poco no hay acierto que te salve.** Con
m = 0,20%, ni acertando el 70% ganás. Y con m = 1,20% te alcanza con el 55%.

Por eso el tamaño esperado del movimiento no es un adorno del formulario: es
la mitad de la decisión. Un 0,65 sobre un par quieto es peor apuesta que un
0,55 sobre uno que se mueve.

## El error que NO hay que cometer con esta fórmula

Parece que si tu `p` no supera `p*`, conviene irse a 0,49. **Es falso, y es un
error caro.**

Con entrada forzada la comisión se paga igual, elijas el lado que elijas.
Comparando el lado A contra el B, la `c` se cancela:

```
EV(A) − EV(B) = m·(2p − 1) − c − [ m·(1 − 2p) − c ] = 2m·(2p − 1)
```

**La comisión no aparece.** No entra en la decisión del lado ni en la de la
convicción. `p*` sirve para una sola cosa: saber si el juego, como está
planteado, se puede ganar. No para elegir dentro de él.

Si la celda dice 0,35, tu número es 0,44 aunque `p*` sea 60,2%. Achicar todo
hacia 0,49 por miedo al peaje destruye tu resolución — que ya es casi cero —
y hace que el experimento no mida nada.

## Cómo convertir esto en tu número

No inventes la convicción: derivala de la tabla condicional, contraída hacia
0,50 por el tamaño de muestra. Y comprometete con ese número.

`m` sigue importando, pero para otra cosa: **para saber dónde vale la pena
tener razón.** Una celda de 0,35 sobre un par con volatilidad alta vale
mucho más que la misma celda sobre uno quieto. Si algún día el sistema puede
elegir cuánto poner en cada par, esa es la variable que lo decide.



No inventes la convicción: derivala.

El paso donde te mentís es el de la convicción. Una señal buena en este oficio mueve la
probabilidad 5 o 10 puntos, no 30. Si partís de 0,50 y tenés una señal
decente, llegás a 0,58 — que con m mediano **todavía no alcanza**.

## La consecuencia incómoda

Si `p*` es 60,2% y tus mejores señales llegan a 58%, la conclusión honesta es
que **con entrada forzada este juego no se gana en promedio**, y solo se
puede empatar concentrando las veces que `m` es grande.

Esa es la única palanca que te queda: **apostá con convicción solo cuando el
movimiento esperado es grande.** En los pares quietos, 0,50 y pagá el peaje
sin agrandar el daño.
