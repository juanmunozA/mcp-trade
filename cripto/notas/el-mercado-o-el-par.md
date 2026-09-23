**La regla del RSI casi no elige pares: elige la dirección del mercado. Y eso
significa que no tenés 261 apuestas, tenés 33.**

Es la prueba que hay que hacerle a cualquier señal antes de creerle, y la
única que separa un hallazgo útil de una ilusión de tamaño de muestra.

## Cómo se separa

El retorno de un par se parte en dos:

```
retorno del par  =  retorno del mercado ese ciclo  +  cuánto batió al mercado
```

Y el RSI se parte igual: su nivel medio en el ciclo, y cuánto más
sobrecomprado está ese par que sus hermanos. Después se mide cada mitad por
separado.

## El resultado

**Serie de tiempo — el RSI medio del ciclo contra el retorno del mercado:**

```
corr = −0,538    t = −3,55    IC 95% = [−0,744 ; −0,239]    n = 33 ciclos
regla "RSI medio bajo 50 → el mercado sube": acierta 21/33 = 63,6%
```

Fuerte y significativo. **Pero n = 33.** Tres horas y media de una noche.

**Corte transversal — dentro del mismo ciclo, ¿el más sobrevendido sube más?**

```
corr = −0,244    t = −2,19    IC 95% = [−0,463 ; −0,026]
regla "el más sobrevendido del ciclo le gana al mercado": 52,5%
```

Existe, apenas. El intervalo casi toca el cero y el 52,5% no paga nada.

## Por qué importa la diferencia

Un efecto **transversal** se diversifica: apostás a ocho pares, los errores no
están correlacionados, y con muchas repeticiones el promedio se estabiliza.
Es el tipo de ventaja sobre la que se puede construir algo.

Un efecto de **serie de tiempo** no se diversifica. Ocho posiciones del mismo
lado son una sola apuesta grande. Y con 33 observaciones, un t de −3,55 es
mucho menos de lo que parece: una noche, un régimen, y la certeza de que en
otro régimen se puede dar vuelta entero.

**El grueso de lo que medimos es del segundo tipo.** Esa es la conclusión
honesta, e implica que la evidencia real detrás de la regla del RSI es mucho
más flaca que lo que sugería un "66,7% sobre 102 operaciones".

## Se degrada dentro de la misma noche

```
primera mitad (22:36–01:00)   n=60   acierta 73,3%
segunda mitad (01:00–02:27)   n=42   acierta 57,1%
```

En tres horas y media perdió 16 puntos. Ni siquiera aguantó una sesión.

## Y no funciona igual en todos los pares

```
UNI   80,0%     AVAX  64,3%     NEAR  42,9%
TAO   78,6%     SUI   57,1%     PEPE  30,8%   ← la rompe
```

En PEPE la regla acierta menos de un tercio: aplicarla ahí es apostar en
contra de uno mismo. Dejando un par afuera por vez, el peor caso es sin TAO
y queda 64,8% sobre n=88 — así que no es un solo par el que carga todo, pero
la dispersión es enorme para una regla supuestamente general.

## Qué hacer con esto

**Usá el RSI en dos niveles distintos, y sabé cuál es cuál.**

1. **El promedio del ciclo te da la inclinación del conjunto.** Si casi todos
   los pares están sobrecomprados, el mercado entero tiende a ceder. Eso es
   real y es lo más fuerte que medimos — con n = 33 detrás, así que inclinate,
   no te tires de cabeza.

2. **El RSI relativo te dice a cuál pegarle más.** Dentro del ciclo, el más
   sobrevendido de los ocho es el mejor candidato para el lado largo. Señal
   débil, útil para ordenar, no para convicción.

**Y no repitas el mismo lado en los ocho creyendo que diversificás.** Cuando
lo hacés estás tomando la apuesta de serie de tiempo, que es la que tiene 33
observaciones atrás. Si la vas a tomar, tomala sabiendo lo que es.
