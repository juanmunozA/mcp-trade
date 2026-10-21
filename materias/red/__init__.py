"""
La red neuronal propia del proyecto.

El agente lee la guia y razona con un modelo de lenguaje que entreno otro;
sus pesos no cambian por mucho que operemos. Esto es lo contrario: una red
pequena cuyos pesos salen de las velas de XAUUSD, y que se reentrena con
cada dato nuevo.

Va por capas, de lo simple a lo complejo, y cada capa tiene que ganarle a
la anterior con datos que no vio antes de subir a la siguiente:

    datos.py      velas del broker, guardadas en disco
    etiquetas.py  que paso despues de cada vela, medido en pips

Todo en numpy a secas. Smart App Control bloquea las extensiones nativas
de las librerias de ML que no reconoce, y una red pequena no necesita mas
que multiplicar y sumar.
"""
