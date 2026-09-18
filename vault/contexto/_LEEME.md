---
categoria: todas
---

# Cómo funciona esta carpeta

Todo lo que escribas aquí entra en el prompt del agente en su próximo
pronóstico. Es el único camino por el que tu criterio llega a él.

**Escribe lecciones, no noticias.** Una nota útil se parece a:

> Polymarket resuelve según la fuente citada en el reglamento, no según lo
> que sea verdad. Si dice "según AP" y Reuters lo reporta dos días antes,
> no cuenta hasta que AP lo publique.

Una nota inútil se parece a:

> Hoy Lula subió en las encuestas.

La primera sirve para siempre. La segunda ya es falsa mañana.

## Para acotar una nota a una categoría

Ponle frontmatter:

```
---
categoria: geopolitica
---
```

Sin `categoria`, la nota aplica a todos los pronósticos.

## Límite

Se inyectan hasta 4.000 caracteres, las notas más recientes primero. Si el
vault crece mucho, lo viejo deja de entrar — así que borra lo que ya no
aplique en vez de acumular.
