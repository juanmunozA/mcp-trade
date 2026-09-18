"""
Búsqueda web local, sin API keys.

EL PROBLEMA QUE RESUELVE
────────────────────────
Ollama no navega. Un modelo local sabe lo que había en su corpus de
entrenamiento y nada más — para "¿va a quedar primero el partido
gobernante?" alcanza, pero para "¿firmaron el acuerdo la semana pasada?"
es inútil. Y este agente pronostica eventos que están ocurriendo.

En la nube eso lo cubría el grounding de Gemini. Corriendo todo local hay
que reemplazarlo.

CÓMO
────
SearXNG es un metabuscador autohospedado: consulta a Google, Bing, Brave
y demás, agrega los resultados y te los devuelve en JSON. Sin cuenta, sin
API key, sin cuota. Corre en un contenedor al lado del agente.

Después se descargan las páginas más relevantes y se extrae el texto. Ese
texto crudo va al modelo local, que lo destila en evidencia con fechas —
el mismo formato que devolvía Gemini, así que el resto del pipeline no
se entera de con cuál de los dos está trabajando.

LO QUE SE PIERDE
────────────────
Gemini devuelve evidencia ya sintetizada y con citas verificadas. Aquí
llega HTML crudo del que hay que extraer el texto, y la síntesis la hace
un modelo más pequeño. Es peor, y es el precio de no depender de nadie.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("search")

# Motores que devuelven resultados útiles para eventos de actualidad.
# `news` prioriza lo reciente, que es justo lo que importa aquí.
MOTORES = "google,bing,duckduckgo,brave"
CATEGORIA = "news,general"

MAX_CHARS_POR_PAGINA = 3500
"""
Tope por página. Sin él, un artículo largo llena el contexto del modelo
local —que suele ser de 8k a 32k tokens— y desplaza a las demás fuentes.
"""


@dataclass(slots=True)
class Resultado:
    titulo: str
    url: str
    resumen: str
    fecha: str = ""
    texto: str = ""

    @property
    def util(self) -> bool:
        return len(self.texto) > 300 or len(self.resumen) > 120


@dataclass(slots=True)
class Busqueda:
    query: str
    resultados: list[Resultado] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and any(r.util for r in self.resultados)

    def como_evidencia(self) -> str:
        """Formato que el modelo recibe. Fecha primero: es lo que más pesa."""
        bloques = []
        for i, r in enumerate(self.resultados, 1):
            if not r.util:
                continue
            cuerpo = r.texto or r.resumen
            fecha = f"[{r.fecha}] " if r.fecha else ""
            bloques.append(
                f"--- FUENTE {i} {fecha}{r.titulo[:120]}\n"
                f"{r.url}\n{cuerpo[:MAX_CHARS_POR_PAGINA]}"
            )
        return "\n\n".join(bloques)


class LocalSearch:
    """
    Cliente de SearXNG. `host` apunta al contenedor: dentro de la red de
    Docker es `http://searxng:8080`, desde tu máquina `http://localhost:8888`.
    """

    def __init__(
        self,
        host: str = "http://localhost:8888",
        *,
        max_resultados: int = 6,
        descargar_paginas: int = 4,
        timeout: float = 45.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.max_resultados = max_resultados
        self.descargar_paginas = descargar_paginas
        """
        Cuántas páginas se descargan completas. Las demás se quedan con el
        resumen del buscador. Descargar todas multiplica el tiempo del
        ciclo sin mejorar mucho: los primeros resultados concentran la
        información.
        """
        self.timeout = timeout

    async def buscar(self, query: str) -> Busqueda:
        b = Busqueda(query=query)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                r = await http.get(
                    f"{self.host}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "engines": MOTORES,
                        "categories": CATEGORIA,
                        "language": "en",
                        # Solo lo reciente: un evento que resuelve en semanas
                        # no se decide con noticias de hace dos años.
                        "time_range": "month",
                    },
                )
                if r.status_code != 200:
                    b.error = f"searxng HTTP {r.status_code}"
                    return b

                for item in r.json().get("results", [])[: self.max_resultados]:
                    b.resultados.append(Resultado(
                        titulo=item.get("title", "")[:200],
                        url=item.get("url", ""),
                        resumen=(item.get("content") or "")[:600],
                        fecha=(item.get("publishedDate") or "")[:10],
                    ))

                await self._descargar(http, b.resultados[: self.descargar_paginas])

        except httpx.RequestError as exc:
            b.error = (
                f"SearXNG no responde en {self.host}: {exc}. "
                "¿Está el contenedor arriba? `docker compose up -d searxng`"
            )
            log.warning(b.error)
        except Exception as exc:  # noqa: BLE001
            b.error = repr(exc)
            log.warning("búsqueda falló: %s", exc)

        return b

    async def _descargar(self, http: httpx.AsyncClient, rs: list[Resultado]) -> None:
        """En paralelo: son esperas de red, y una página lenta no debe frenar al resto."""
        async def una(r: Resultado) -> None:
            try:
                resp = await http.get(
                    r.url,
                    timeout=20,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; mcp-trade/1.0)"},
                )
                if resp.status_code == 200 and "html" in resp.headers.get(
                    "content-type", ""
                ):
                    r.texto = extraer_texto(resp.text)
            except Exception:  # noqa: BLE001 — una página caída no es un error del ciclo
                pass

        await asyncio.gather(*(una(r) for r in rs))


# ── extracción de texto ──────────────────────────────────────────────────

_QUITAR = re.compile(
    r"<(script|style|nav|header|footer|aside|form|noscript)\b[^>]*>.*?</\1>",
    re.S | re.I,
)
_TAGS = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"\n\s*\n\s*\n+")


def extraer_texto(html_crudo: str) -> str:
    """
    Texto legible de una página, sin dependencias.

    No es readability de verdad —no puntúa densidad de texto por nodo— pero
    quitar scripts, navegación y etiquetas deja el cuerpo lo bastante limpio
    para que un modelo lo lea. Meter una librería de extracción aquí
    añadiría una dependencia pesada para una mejora marginal.
    """
    t = _QUITAR.sub(" ", html_crudo)
    t = _TAGS.sub("\n", t)
    t = html.unescape(t)
    lineas = [ln.strip() for ln in t.splitlines()]
    # Las líneas cortas casi siempre son menús, botones y migas de pan.
    lineas = [ln for ln in lineas if len(ln) > 40]
    return _ESPACIOS.sub("\n\n", "\n".join(lineas)).strip()[:12000]


def consultas_para(question: str, rules: str) -> list[str]:
    """
    Dos consultas por mercado: la pregunta tal cual, y una acotada a los
    términos con peso. Los buscadores responden distinto a cada forma y
    juntar ambas cubre más.
    """
    limpia = re.sub(r"^(will|would|does|is|are|did)\s+", "", question.strip(), flags=re.I)
    limpia = limpia.rstrip("?").strip()
    palabras = [
        w for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9-]{4,}", limpia)
        if w.lower() not in {"before", "after", "between", "would", "there", "their"}
    ]
    return [limpia, " ".join(palabras[:7]) + " latest news"]
