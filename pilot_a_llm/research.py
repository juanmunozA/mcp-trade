"""
Investigación: qué se sabe del evento, con fechas.

Solo Gemini hace esto, por una razón concreta: **es el único proveedor con
búsqueda web**. Los modelos de Cloudflare no navegan — saben lo que había
en su corpus de entrenamiento y nada más. Para "¿va a quedar primero el
partido gobernante?" eso basta; para "¿firmaron el acuerdo la semana
pasada?" es inútil.

Por eso el reparto: Gemini trae los hechos frescos, y los modelos abiertos
razonan sobre ellos. Sale barato (2-4 llamadas a Gemini al día) y cada
modelo hace lo que sabe hacer.

El prompt pide hechos con fecha y prohíbe explícitamente opinar sobre
probabilidades. Si el investigador sugiere un número, el pronosticador se
ancla en él y el ensemble deja de ser independiente.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pilot_a_llm.markets import Candidate
from pilot_a_llm.prompts import RESEARCH_PROMPT
from pilot_a_llm.providers import Provider, ProviderError

log = logging.getLogger("research")


@dataclass(slots=True)
class Evidence:
    text: str
    sources: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = True
    error: str = ""

    @property
    def is_thin(self) -> bool:
        """
        Evidencia pobre no es neutral: hace que el modelo rellene con su
        prior en vez de con hechos, y ahí aparecen los pronósticos
        sobreconfiados. Mejor saltar el mercado.
        """
        return not self.ok or len(self.text) < 200 or not self.sources


class Researcher:
    def __init__(self, provider: Provider, *, max_tokens: int = 8192) -> None:
        self.provider = provider
        self.max_tokens = max_tokens

    async def investigate(self, c: Candidate) -> Evidence:
        prompt = RESEARCH_PROMPT.format(
            question=c.question,
            rules=c.rules[:1500],
            today=f"{datetime.now(UTC):%Y-%m-%d}",
            days=c.days_to_resolution,
        )
        try:
            # search=True impide usar responseSchema (son incompatibles en
            # Gemini). Aquí queremos prosa con citas, así que está bien.
            comp = await self.provider.complete(
                prompt, search=True, max_tokens=self.max_tokens
            )
        except ProviderError as exc:
            log.warning("investigación falló para %s: %s", c.slug, exc)
            return Evidence("", ok=False, error=str(exc))

        ev = Evidence(
            text=comp.text.strip(),
            sources=comp.grounded_urls,
            cost_usd=comp.cost_usd,
            latency_ms=comp.latency_ms,
        )
        if ev.is_thin:
            log.info(
                "evidencia pobre en %s (%d chars, %d fuentes)",
                c.slug, len(ev.text), len(ev.sources),
            )
        return ev


class LocalResearcher:
    """
    Investigación sin depender de nadie: SearXNG busca, se descargan las
    páginas, y el modelo local destila el texto crudo en evidencia con
    fechas.

    Devuelve el mismo `Evidence` que el investigador de Gemini, así que el
    resto del pipeline no distingue con cuál está trabajando.

    Es más lento y peor: Gemini entrega síntesis con citas verificadas;
    aquí llega HTML crudo y lo resume un modelo más pequeño. Ese es el
    precio de no depender de ninguna nube.
    """

    def __init__(self, search, provider: Provider, *, max_tokens: int = 2048) -> None:
        self.search = search
        self.provider = provider
        self.max_tokens = max_tokens

    async def investigate(self, c: Candidate) -> Evidence:
        from pilot_a_llm.search import consultas_para

        t0 = time.perf_counter()
        queries = consultas_para(c.question, c.rules)

        busquedas = [await self.search.buscar(q) for q in queries]
        utiles = [b for b in busquedas if b.ok]
        if not utiles:
            motivo = busquedas[0].error if busquedas else "sin resultados"
            log.info("búsqueda vacía para %s: %s", c.slug[:40], motivo)
            return Evidence("", ok=False, error=motivo)

        crudo = "\n\n".join(b.como_evidencia() for b in utiles)
        fuentes = [r.url for b in utiles for r in b.resultados if r.util]

        # El modelo local destila. Se le prohíbe opinar sobre probabilidades
        # por lo mismo que a Gemini: si sugiere un número, el pronosticador
        # se ancla y el ensemble deja de ser independiente.
        prompt = f"""\
Debajo hay resultados de búsqueda sobre este evento. Extrae SOLO hechos \
verificables, cada uno con su fecha.

PREGUNTA: {c.question}
REGLA DE RESOLUCIÓN: {c.rules[:800]}
HOY ES: {datetime.now(UTC):%Y-%m-%d}. Resuelve en {c.days_to_resolution} días.

{crudo[:20000]}

Devuelve en máximo 250 palabras:
1. ESTADO ACTUAL — qué ya ocurrió, con fechas.
2. DATOS — cifras, encuestas, conteos, con su fuente y fecha.
3. QUÉ FALTA — qué debe pasar todavía para que resuelva SÍ.
4. SEÑALES EN CONTRA — evidencia de que NO ocurrirá.

No estimes probabilidades. No opines si es buena apuesta. Solo hechos.
Si los resultados no hablan del evento, dilo y no inventes nada."""

        try:
            comp = await self.provider.complete(prompt, max_tokens=self.max_tokens)
        except ProviderError as exc:
            log.warning("destilado falló para %s: %s", c.slug[:40], exc)
            # El crudo sirve mejor que nada: el pronosticador puede leerlo.
            return Evidence(crudo[:6000], sources=fuentes[:10])

        return Evidence(
            text=comp.text.strip(),
            sources=fuentes[:10],
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
