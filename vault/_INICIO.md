---
tags: [indice]
---

# Agente de pronóstico

> Este archivo empieza con `_`: el agente **no** lo lee. Es tu tablero.

**33** pronósticos · **11** órdenes · **22** descartados · tasa de descarte **67%**

## El flujo

1. [[1-seleccion|Selección de mercados]]
2. [[2-investigacion|Investigación]]
3. [[3-ensemble|Ensemble en cascada]]
4. [[4-vetos|Los tres vetos]]
5. [[5-decision|Decisión y riesgo]]
6. [[6-aprendizaje|Bucle de aprendizaje]]

## Por qué no operó

- **8** — divergencia extrema
- **7** — edge insuficiente
- **5** — desacuerdo del ensemble
- **2** — ambigüedad declarada

## Órdenes abiertas

- `YES` [[2026-09-18-us-x-iran-diplomatic-meeting-by-september-30-2026]] — agente 0.325 vs mercado 0.135
- `YES` [[2026-09-18-nato-x-russia-military-clash-by-december-31-2026-244-53]] — agente 0.425 vs mercado 0.285
- `NO` [[2026-09-18-will-flvio-bolsonaro-finish-in-second-place-in-the-firs]] — agente 0.450 vs mercado 0.705
- `NO` [[2026-09-18-will-flvio-bolsonaro-finish-in-second-place-in-the-firs]] — agente 0.500 vs mercado 0.695
- `NO` [[2026-09-18-will-luiz-incio-lula-da-silva-finish-in-second-place-in]] — agente 0.050 vs mercado 0.291
- `NO` [[2026-09-18-us-x-iran-diplomatic-meeting-by-december-31-2026-745]] — agente 0.150 vs mercado 0.395
- `NO` [[2026-09-18-will-gadi-eizenkot-be-the-next-prime-minister-of-israel]] — agente 0.350 vs mercado 0.518
- `NO` [[2026-09-18-will-flvio-bolsonaro-finish-in-second-place-in-the-firs]] — agente 0.425 vs mercado 0.695
- `NO` [[2026-09-18-us-announces-end-of-iranian-blockade-by-october-31-2026]] — agente 0.100 vs mercado 0.325
- `NO` [[2026-09-18-will-gadi-eizenkot-be-the-next-prime-minister-of-israel]] — agente 0.300 vs mercado 0.518
- `NO` [[2026-09-18-2026-balance-of-power-d-senate-d-house-949]] — agente 0.400 vs mercado 0.600

## Cómo leer el grafo

```
naranja   1-flujo/       la espina: los 6 pasos en orden
verde     órdenes        lo que sí operó
rojo      vetos          lo descartado, agrupado por motivo
morado    4-temas/       por categoría
oro       5-modelos/     quién opinó
gris      3-mercados/    cada pronóstico, en la periferia
turquesa  contexto/      TUS lecciones — lo único que llega al agente
```

Cuando un pronóstico salga mal, busca **en qué paso** se rompió. Si ves un patrón que se repite, escríbelo en `contexto/`.

---
*generado 2026-09-18 22:56 UTC*
