"""
El vault de Obsidian: el lado humano del bucle de aprendizaje.

LA IDEA
───────
Los bucles automáticos —calibración isotónica, pesos del ensemble— corrigen
lo que se puede medir con un número: cuánta confianza tener. No pueden
corregir *en qué* basarla.

Eso lo ves tú leyendo pronósticos fallidos. El vault es el camino de vuelta.

LA ESTRUCTURA REFLEJA EL PIPELINE
─────────────────────────────────
    1-flujo/        la columna vertebral: los 6 pasos, enlazados en orden
    2-decisiones/   qué pasó: órdenes y tipos de veto
    3-mercados/     cada pronóstico, colgando de los tres anteriores
    4-temas/        por categoría
    5-modelos/      quién opinó
    contexto/       TUS lecciones  ★ lo único que llega al agente

Esa jerarquía es lo que hace legible la vista de grafo: el flujo queda como
espina central, las decisiones alrededor, y los mercados en la periferia.
Un grafo plano de mercados sueltos no dice nada; uno donde ves un racimo
gordo colgando de `veto-divergencia` te cuenta el problema de un vistazo.

SEPARACIÓN DE PROPIEDAD
───────────────────────
El agente nunca escribe en `contexto/` y tú nunca necesitas editar el resto.
Y a diferencia de los datos de mercado, tus notas SÍ son de confianza: las
escribiste tú. Por eso van fuera del bloque `<untrusted_market_data>`.
"""

from __future__ import annotations

import json
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

# ── la columna vertebral del grafo ───────────────────────────────────────
# Cada paso enlaza al siguiente. Eso es lo que dibuja la espina central.
FLUJO: list[tuple[str, str, str]] = [
    ("1-seleccion", "Selección de mercados",
     "De ~1.200 activos a ~15 candidatos. Sin IA y sin costo: precio entre "
     "0.12 y 0.88, liquidez media (el óptimo está en ~$80k, no en el máximo: "
     "los mercados gigantes ya están arbitrados), resuelve en 2-150 días."),
    ("2-investigacion", "Investigación",
     "Gemini con búsqueda web — el único proveedor que navega. Devuelve "
     "hechos con fecha y fuente. Tiene prohibido opinar sobre probabilidades: "
     "si sugiere un número, el ensemble se ancla y deja de ser independiente."),
    ("3-ensemble", "Ensemble en cascada",
     "llama-3.1-8b (4.5 neurons) mira todos los mercados. DeepSeek-R1 "
     "(175 neurons, 39× más caro) solo opina donde el barato ve un desajuste. "
     "Antes corría el caro en todo y un ciclo agotaba el cupo diario entero."),
    ("4-vetos", "Los tres vetos",
     "Cualquiera fuerza PASS, por atractivo que parezca el edge. Es donde se "
     "descarta el 67% de lo analizado."),
    ("5-decision", "Decisión y riesgo",
     "Calibración isotónica → Kelly fraccional sobre el saldo real → risk "
     "kernel. Nada de esto usa IA: el modelo aporta un número, la aritmética "
     "decide qué hacer con él."),
    ("6-aprendizaje", "Bucle de aprendizaje",
     "Cada 6 h busca mercados resueltos. Brier real contra Brier del mercado. "
     "Lo único que convierte esto en algo que aprende."),
]

# A qué paso del flujo pertenece cada resultado.
DECISIONES: dict[str, tuple[str, str, str]] = {
    "orden-yes": ("Órdenes YES", "5-decision",
                  "El agente apostó a que el evento SÍ ocurre."),
    "orden-no": ("Órdenes NO", "5-decision",
                 "El agente apostó a que NO ocurre."),
    "pass-sin-edge": ("Pass · sin edge suficiente", "5-decision",
                      "El desajuste no supera el umbral. Coincidir con el "
                      "mercado es lo normal y lo correcto: un agente que "
                      "encuentra oportunidad en todo no está evaluando."),
    "veto-divergencia": ("Veto · divergencia extrema", "4-vetos",
                         "Discrepó del mercado más de 35 puntos. Un edge real "
                         "son 5-15; si sale 70, lo probable no es haber "
                         "encontrado oro sino haber entendido otra pregunta."),
    "veto-desacuerdo": ("Veto · desacuerdo del ensemble", "4-vetos",
                        "Los modelos no convergieron. La pregunta es ambigua "
                        "aunque ninguno lo haya declarado."),
    "veto-ambiguedad": ("Veto · ambigüedad declarada", "4-vetos",
                        "El modelo leyó el reglamento, vio dos lecturas "
                        "posibles y se detuvo solo."),
    "veto-evidencia": ("Veto · evidencia insuficiente", "2-investigacion",
                       "Sin hechos el modelo rellena con su prior, y ahí "
                       "aparecen los pronósticos sobreconfiados."),
}

# Paleta del grafo. Obsidian guarda el color como entero decimal RGB.
COLORES = {
    "flujo": 0xD97757,      # arcilla — la espina central
    "decisiones": 0xC88A4A,  # ámbar
    "orden": 0x6FA37D,      # verde — lo que sí operó
    "veto": 0xC8615B,       # rojo — lo descartado
    "mercados": 0x7C8AA0,   # gris azulado — la periferia
    "temas": 0x8B6FA8,      # morado
    "modelos": 0xC8A85B,    # oro
    "contexto": 0x5BA8A0,   # verde azulado — TUS lecciones
}


class Vault:
    def __init__(self, root: Path | str = "vault") -> None:
        self.root = Path(root)
        self.flow = self.root / "1-flujo"
        self.decisions = self.root / "2-decisiones"
        self.forecasts = self.root / "3-mercados"
        self.themes = self.root / "4-temas"
        self.models = self.root / "5-modelos"
        self.context = self.root / "contexto"
        self.post_mortems = self.root / "post-mortems"
        for d in (self.flow, self.decisions, self.forecasts, self.themes,
                  self.models, self.context, self.post_mortems):
            d.mkdir(parents=True, exist_ok=True)
        self._seed_context()
        self._seed_flow()
        self._seed_decisions()

    # ── lo que el agente LEE ─────────────────────────────────────────────

    def read_context(self, *, category: str = "") -> str:
        """
        Tus notas, listas para inyectar al prompt. Las más recientes primero:
        si el vault crece más allá del tope, se corta lo viejo, que es lo que
        más probablemente ya está obsoleto.
        """
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
            if not body or not _applies_to(text, category):
                continue
            piece = f"### {note.stem}\n{body}"
            if used + len(piece) > MAX_CONTEXT_CHARS:
                break
            chunks.append(piece)
            used += len(piece)
        return "\n\n".join(chunks)

    # ── lo que el agente ESCRIBE ─────────────────────────────────────────

    def write_forecast(self, f, action: str, size_usd: float | None) -> Path:
        c = f.candidate
        stamp = datetime.now(UTC)
        path = self.forecasts / f"{stamp:%Y-%m-%d}-{_slugify(c.slug)}.md"
        resuelve = f"{c.end_date:%Y-%m-%d}" if c.end_date else "sin fecha"
        resultado = _nodo_resultado(f, action)
        veredicto = f.veto_reason if f.vetoed else action

        # Los [[enlaces]] son lo que dibuja el grafo. Cada mercado cuelga de
        # tres ejes: qué pasó, de qué tema, quién opinó.
        self._ensure(self.themes, c.category, "tema", COLORES["temas"])
        for m in f.per_model:
            self._ensure(self.models, m, "modelo", COLORES["modelos"])

        modelos = " · ".join(f"[[{m}]] `{p:.3f}`" for m, p in f.per_model.items())

        path.write_text(f"""---
fecha: {stamp:%Y-%m-%d %H:%M}
mercado: "{c.question[:120]}"
tema: {c.category}
resuelve: {resuelve}
precio_mercado: {c.price:.3f}
probabilidad: {f.p_calibrated:.3f}
divergencia: {f.divergence:+.3f}
desacuerdo: {f.disagreement:.3f}
accion: {action}
tags: [mercado, {c.category}, {"veto" if f.vetoed else "orden"}]
---

# {c.question[:100]}

**[[{resultado}]]** · [[{c.category}]] · resuelve en {c.days_to_resolution} días

> {veredicto}

| | |
|---|---|
| Mercado | `{c.price:.3f}` |
| Agente | `{f.p_calibrated:.3f}` |
| Divergencia | `{f.divergence:+.3f}` |
| Tamaño | {f"${size_usd:.2f}" if size_usd else "—"} |

## Cómo entendió la pregunta
{f.means_yes or "_(sin paráfrasis)_"}

## Tasa base
**{f.reference_class or "sin clase de referencia"}** → `{f.base_rate:.2f}`

## Evidencia
{chr(10).join(f"- {e}" for e in f.key_evidence) or "_(ninguna)_"}

## Qué falta para que resuelva SÍ
{f.steps_required or "_(no especificado)_"}

## Qué lo refutaría
{f.what_would_change_my_mind or "_(no especificado)_"}

## Riesgo del reglamento
{f.resolution_risk or "_(ninguno detectado)_"}

---
{modelos}

*Confianza: {f.confidence} · [[3-ensemble]] · [[5-decision]]*
""", encoding="utf-8")
        return path

    def write_post_mortem(self, resolved, row: dict, text: str) -> Path:
        stamp = datetime.now(UTC)
        acerto = resolved.brier < 0.25
        path = self.post_mortems / f"{stamp:%Y-%m-%d}-{_slugify(row['slug'])}.md"
        path.write_text(f"""---
fecha: {stamp:%Y-%m-%d}
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

**[[6-aprendizaje]]** · [[{resolved.category}]]

{text}

---
## Tu análisis

_Si encuentras un patrón que se repite, muévelo a `contexto/` — solo eso
llega al agente._
""", encoding="utf-8")
        return path

    # ── nodos del grafo ──────────────────────────────────────────────────

    def _seed_flow(self) -> None:
        """La espina central: cada paso enlaza al siguiente."""
        for i, (slug, titulo, desc) in enumerate(FLUJO):
            p = self.flow / f"{slug}.md"
            if p.exists():
                continue
            siguiente = FLUJO[i + 1][0] if i + 1 < len(FLUJO) else FLUJO[0][0]
            anterior = FLUJO[i - 1][0] if i > 0 else FLUJO[-1][0]
            p.write_text(
                f"---\ntags: [flujo]\norden: {i + 1}\n---\n\n"
                f"# {i + 1}. {titulo}\n\n{desc}\n\n"
                f"---\n← [[{anterior}]] · [[{siguiente}]] →\n",
                encoding="utf-8",
            )

    def _seed_decisions(self) -> None:
        """Los resultados, cada uno colgando de su paso del flujo."""
        for slug, (titulo, paso, desc) in DECISIONES.items():
            p = self.decisions / f"{slug}.md"
            if not p.exists():
                tipo = "orden" if slug.startswith("orden") else "veto"
                p.write_text(
                    f"---\ntags: [decision, {tipo}]\n---\n\n"
                    f"# {titulo}\n\n{desc}\n\n---\n[[{paso}]]\n",
                    encoding="utf-8",
                )

    def _ensure(self, folder: Path, name: str, tag: str, _color: int) -> None:
        p = folder / f"{name}.md"
        if not p.exists():
            p.write_text(
                f"---\ntags: [{tag}]\n---\n\n# {name}\n", encoding="utf-8"
            )

    def write_graph_config(self) -> None:
        """
        Colores y física de la vista de grafo. Sin esto todo sale del mismo
        color, los nodos se amontonan y no se distingue nada.
        """
        cfg = self.root / ".obsidian"
        cfg.mkdir(exist_ok=True)

        def grupo(query: str, color: int) -> dict:
            return {"query": query, "color": {"a": 1, "rgb": color}}

        (cfg / "graph.json").write_text(json.dumps({
            "collapse-filter": False,
            # Sin filtro, las notas de mercado —que son cientos y crecen cada
            # ciclo— tapan la espina del flujo y el grafo deja de leerse. Se
            # quita desde la UI cuando se quiere ver de qué mercado salió cada
            # decisión.
            "search": "-path:3-mercados",
            "showTags": False,
            "showAttachments": False,
            "hideUnresolved": True,
            "showOrphans": False,
            "collapse-color-groups": False,
            "colorGroups": [
                grupo("path:1-flujo", COLORES["flujo"]),
                grupo("tag:#orden path:2-decisiones", COLORES["orden"]),
                grupo("tag:#veto path:2-decisiones", COLORES["veto"]),
                grupo("path:contexto", COLORES["contexto"]),
                grupo("path:4-temas", COLORES["temas"]),
                grupo("path:5-modelos", COLORES["modelos"]),
                grupo("tag:#orden", COLORES["orden"]),
                grupo("tag:#veto", COLORES["veto"]),
                grupo("path:3-mercados", COLORES["mercados"]),
            ],
            "collapse-display": False,
            # Flechas: sin ellas no se ve la dirección del flujo.
            "showArrow": True,
            "textFadeMultiplier": -1.2,
            "nodeSizeMultiplier": 1.15,
            "lineSizeMultiplier": 0.55,
            "collapse-forces": False,
            # Repulsión alta y enlaces largos: separa los racimos en vez de
            # dejar una bola apretada donde no se lee nada.
            "centerStrength": 0.28,
            "repelStrength": 16,
            "linkStrength": 0.55,
            "linkDistance": 260,
            "scale": 0.42,
            "close": True,
        }, indent=2), encoding="utf-8")

        # Tema oscuro: el grafo se lee mucho mejor y los colores resaltan.
        (cfg / "appearance.json").write_text(
            json.dumps({"theme": "obsidian", "accentColor": "#D97757"}, indent=2),
            encoding="utf-8",
        )

    # ── semilla ──────────────────────────────────────────────────────────

    def _seed_context(self) -> None:
        readme = self.context / "_LEEME.md"
        if readme.exists():
            return
        readme.write_text("""---
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

## Para acotar una nota a un tema

```
---
categoria: geopolitica
---
```

Sin `categoria`, la nota aplica a todos los pronósticos.

## Límite

Se inyectan hasta 4.000 caracteres, las notas más recientes primero. Borra
lo que ya no aplique en vez de acumular.
""", encoding="utf-8")


# ── utilidades ───────────────────────────────────────────────────────────


def _nodo_resultado(f, action: str) -> str:
    """
    A qué nodo se engancha este pronóstico. Agrupa por lo que pasó, no por
    el mercado: así el grafo muestra los patrones de decisión en vez de una
    lista de puntos sueltos.
    """
    if not f.vetoed:
        return f"orden-{action.lower()}"
    r = f.veto_reason
    for clave, nodo in (
        ("ambigua", "veto-ambiguedad"),
        ("desacuerdo", "veto-desacuerdo"),
        ("divergencia", "veto-divergencia"),
        ("evidencia", "veto-evidencia"),
    ):
        if clave in r:
            return nodo
    return "pass-sin-edge"


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


_ALL = {"", "todas", "todos", "all", "*"}


def _applies_to(text: str, category: str) -> bool:
    """
    Una nota sin `categoria`, o marcada `todas`, aplica siempre.

    El caso `todas` costó un bug silencioso: el filtro comparaba
    `category not in tags` y descartaba la nota semilla para todas las
    categorías concretas. Los tests no lo vieron porque usaban notas sin
    frontmatter.
    """
    tags = _frontmatter_value(text, "categoria").lower().strip()
    if tags in _ALL or not category:
        return True
    wanted = {t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()}
    return category.lower() in wanted
