"""
El vault de Obsidian: el lado humano del bucle de aprendizaje.

LA IDEA
───────
Los bucles automáticos —calibración isotónica, pesos del ensemble— corrigen
lo que se puede medir con un número: cuánta confianza tener. No pueden
corregir *en qué* basarla.

Eso lo ves tú leyendo pronósticos fallidos. Y hasta ahora ese aprendizaje no
tenía por dónde volver al agente.

CÓMO FUNCIONA
─────────────
    vault/
      pronosticos/     ← el agente ESCRIBE, tú lees
      post-mortems/    ← el agente ESCRIBE cuando algo resuelve
      contexto/        ← TÚ ESCRIBES, el agente lee   ★

La carpeta `contexto/` es la que importa. Abres el vault en Obsidian,
lees un pronóstico que salió mal, y escribes una nota:

    "Polymarket resuelve por la fuente citada en el reglamento, no por
     lo que sea verdad. Si el reglamento dice AP y Reuters lo reporta
     antes, no cuenta."

Esa nota entra en el prompt del siguiente pronóstico. Es el único camino
por el que tu criterio llega al agente.

SEPARACIÓN DE PROPIEDAD
───────────────────────
El agente nunca escribe en `contexto/` y tú nunca necesitas editar las otras
dos. Sin esa separación, un ciclo te sobrescribiría las notas.

Y a diferencia de los datos de mercado, tus notas SÍ son de confianza: las
escribiste tú. Por eso van fuera del bloque `<untrusted_market_data>`.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("vault")

MAX_CONTEXT_CHARS = 4000
"""
Tope de lo que se inyecta al prompt. Sin él, un vault que crece durante
meses termina costando más tokens que la evidencia del mercado — y
enterrando lo importante entre lo viejo.
"""


class Vault:
    def __init__(self, root: Path | str = "vault") -> None:
        self.root = Path(root)
        self.forecasts = self.root / "pronosticos"
        self.post_mortems = self.root / "post-mortems"
        self.context = self.root / "contexto"
        for d in (self.forecasts, self.post_mortems, self.context):
            d.mkdir(parents=True, exist_ok=True)
        self._seed_context()

    # ── lo que el agente LEE ─────────────────────────────────────────────

    def read_context(self, *, category: str = "") -> str:
        """
        Tus notas, listas para inyectar al prompt. Las más recientes primero:
        si el vault crece más allá del tope, se corta lo viejo, que es lo que
        más probablemente ya está obsoleto.
        """
        # Los archivos que empiezan con "_" son para ti, no para el agente.
        # El README del vault contiene ejemplos de MALAS notas; inyectarlo
        # haría que el modelo las leyera como lecciones.
        notes = sorted(
            (p for p in self.context.glob("*.md") if not p.name.startswith("_")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        chunks: list[str] = []
        used = 0
        for note in notes:
            try:
                text = note.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            body = _strip_frontmatter(text)
            if not body:
                continue
            # Una nota marcada con una categoría solo aplica a esa. Sin
            # frontmatter, o marcada "todas", aplica a cualquiera.
            if not _applies_to(text, category):
                continue
            piece = f"### {note.stem}\n{body}"
            if used + len(piece) > MAX_CONTEXT_CHARS:
                break
            chunks.append(piece)
            used += len(piece)

        if not chunks:
            return ""
        return "\n\n".join(chunks)

    # ── lo que el agente ESCRIBE ─────────────────────────────────────────

    def write_forecast(self, f, action: str, size_usd: float | None) -> Path:
        """Una nota por pronóstico, con el razonamiento completo."""
        c = f.candidate
        stamp = datetime.now(UTC)
        path = self.forecasts / f"{stamp:%Y-%m-%d}-{_slugify(c.slug)}.md"

        veredicto = f.veto_reason if f.vetoed else action
        cuerpo = f"""---
fecha: {stamp:%Y-%m-%d %H:%M} UTC
mercado: "{c.question[:120]}"
categoria: {c.category}
resuelve: {c.end_date:%Y-%m-%d if c.end_date else 'sin fecha'}
precio_mercado: {c.price:.3f}
probabilidad: {f.p_calibrated:.3f}
divergencia: {f.divergence:+.3f}
desacuerdo: {f.disagreement:.3f}
accion: {action}
tags: [pronostico, {c.category}]
---

# {c.question[:100]}

> **{veredicto}**

## Cómo entendió la pregunta
{f.means_yes or "_(sin paráfrasis)_"}

## Tasa base
**{f.reference_class or "sin clase de referencia"}** → {f.base_rate:.2f}

## Evidencia
{chr(10).join(f"- {e}" for e in f.key_evidence) or "_(ninguna)_"}

## Qué falta para que resuelva SÍ
{f.steps_required or "_(no especificado)_"}

## Qué lo refutaría
{f.what_would_change_my_mind or "_(no especificado)_"}

## Riesgo del reglamento
{f.resolution_risk or "_(ninguno detectado)_"}

## Modelos
{chr(10).join(f"- `{m}`: {p:.3f}" for m, p in f.per_model.items())}

---
*Confianza declarada: {f.confidence} · {size_usd and f"${size_usd:.2f}" or "sin posición"}*
"""
        path.write_text(cuerpo, encoding="utf-8")
        return path

    def write_post_mortem(self, resolved, row: dict, text: str) -> Path:
        """Cuando el mercado resuelve. Esto es lo que vale la pena leer."""
        stamp = datetime.now(UTC)
        acerto = resolved.brier < 0.25
        path = self.post_mortems / f"{stamp:%Y-%m-%d}-{_slugify(row['slug'])}.md"
        path.write_text(
            f"""---
fecha: {stamp:%Y-%m-%d} UTC
resultado: {"SÍ" if resolved.outcome else "NO"}
probabilidad: {resolved.p_forecast:.3f}
precio_mercado: {resolved.p_market:.3f}
brier: {resolved.brier:.4f}
brier_mercado: {resolved.brier_market:.4f}
le_ganamos_al_mercado: {resolved.beat_market}
pnl: {resolved.pnl_usd:+.2f}
tags: [post-mortem, {resolved.category}, {"acierto" if acerto else "error"}]
---

# {row['question'][:100]}

{text}

---
## Tu análisis

_Escribe aquí qué ves. Si encuentras un patrón que se repite, muévelo a
`contexto/` — solo eso llega al agente._
""",
            encoding="utf-8",
        )
        return path

    # ── semilla ──────────────────────────────────────────────────────────

    def _seed_context(self) -> None:
        readme = self.context / "_LEEME.md"
        if readme.exists():
            return
        readme.write_text(
            """---
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
""",
            encoding="utf-8",
        )


# ── utilidades ───────────────────────────────────────────────────────────


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower())[:60].strip("-")


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---"):
        return ""
    head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
    return m.group(1).strip() if m else ""


# Valores que significan "esta nota vale para cualquier categoría".
_ALL = {"", "todas", "todos", "all", "*"}


def _applies_to(text: str, category: str) -> bool:
    """
    Una nota sin `categoria`, o marcada `todas`, aplica siempre.

    El caso `todas` costó un bug silencioso: el filtro comparaba
    `category not in tags` y descartaba la nota semilla del vault para
    todas las categorías concretas. Los tests no lo vieron porque usaban
    notas sin frontmatter.
    """
    tags = _frontmatter_value(text, "categoria").lower().strip()
    if tags in _ALL:
        return True
    if not category:
        return True
    wanted = {t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()}
    return category.lower() in wanted
