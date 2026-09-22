**La literatura dice que el desequilibrio de flujo de órdenes es el predictor
más sólido a horizonte corto. En tus datos no lo fue.** Las dos cosas son
ciertas y hay que sostenerlas juntas.

Medido sobre 229 predicciones propias:

```
flujo_agresor positivo   →  después subió el 49,4%
flujo_agresor negativo   →  después subió el 52,0%
                            diferencia: −2,6 pp   (al revés, y nula)

seguir el flujo agresor  →  51,1% de acierto, −45,33% neto
```

Acertaba la mitad de las veces y fue la estrategia más cara de todas las
probadas: acertaba en los movimientos chicos y erraba en los grandes.

Y cuando lo usaste como fundamento declarado — 68 veces, alineado con el
signo del flujo en 57 — sacaste 52,6%. Moneda al aire.

## Por qué probablemente falló

`flujo_agresor` resume las últimas ~1000 operaciones, que en un par líquido
pueden ser varios minutos. A 15 minutos de horizonte eso ya está en el
precio: para cuando vos lo ves, los que agredieron el libro ya movieron la
cotización. Estás leyendo la huella, no la intención.

La microestructura mide el flujo en ventanas de segundos y predice segundos.
Estirar eso a 15 minutos no es la misma afirmación.

## La distinción que sí se confirmó

- **Profundidad del libro** (`libro_desequilibrio`): órdenes límite esperando.
  Se cancelan, se falsean, no cuestan nada de poner.
- **Flujo agresor**: quién *ejecutó*. Dinero que ya se movió.

`libro_desequilibrio` midió **−0,6 pp**: exactamente cero, como la teoría
predecía. Ahí la teoría acertó de lleno. Trátalo como ruido, no como pista.

## Qué hacer

**El flujo agresor no es razón suficiente para alejarse de 0,50.** Si es lo
único que tenés, quedate en 0,48 o 0,52.

Sirve como desempate cuando otra señal más fuerte ya eligió el lado. No
sirve como la señal principal, y sobre todo **no sirve para justificar
convicción alta**: cada vez que le pusiste 0,65 por flujo agresor, perdiste.
