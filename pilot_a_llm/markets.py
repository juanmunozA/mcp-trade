"""
Selección de mercados: dónde gastar el presupuesto de investigación.

Este módulo es el jefe de research. No estima probabilidades — decide qué
vale la pena mirar, que es una decisión comercial y no analítica. Por eso
aquí sí manda el criterio de rentabilidad, mientras que en `prompts.py`
(el analista) está deliberadamente prohibido.

DÓNDE ESTÁ EL EDGE, SEGÚN LOS DATOS
───────────────────────────────────
· Banda de consenso medio: filtrar a 70-90% de consenso llevó el ROI de
  +4.0% a +9.1% en el estudio de Lacuna. En los extremos el mercado ya
  tiene razón y el spread se come cualquier ventaja.
· Sesgo de optimismo: los humanos sobrevaloran sistemáticamente que las
  cosas SÍ ocurran. Los mercados con fecha límite ("¿se firma antes de
  2027?") heredan ese sesgo: la narrativa es vívida y la tasa base baja.
· Política y geopolítica: PolyBench midió "alpha robusto" donde hay datos
  de encuestas parseables. Cripto dio "miscalibración severa" y retornos
  profundamente negativos.
· Liquidez MEDIA, no máxima. Contraintuitivo pero central: los mercados
  más grandes están saturados de operadores profesionales. Quieres
  liquidez suficiente para entrar y salir, no la que atrae a todo el mundo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Categorías donde la información vive en TEXTO, que es donde un LLM puede
# leer mejor o más rápido que el consenso.
CATEGORY_PATTERNS: dict[str, str] = {
    # EL ORDEN MANDA: gana el primero que coincida, asi que lo especifico va
    # arriba. `deportes` antes que `entretenimiento` porque "Oscar Piastri"
    # es un piloto de F1, no un premio.
    #
    # Casi todo lleva \b, y casi todo lleva \w* o plural. Ambas cosas salieron
    # de medir contra 800 mercados reales, no de imaginarlas:
    #   · sin \b, `rocket` coincidia dentro de "C-rocket-t" (Jasmine Crockett)
    #   · con \b pero sin \w*, `\biran\b` no alcanzaba "Iranian", ni
    #     `\belection\b` alcanzaba "elections"
    "cripto": r"\bbitcoin\b|\bbtc\b|\bethereum\b|\beth\b|\bsolana\b|\bcrypto\w*"
              r"|\bstablecoins?\b|\btokens?\b|\bhalving\b|\bdefi\b|\bbinance\b"
              r"|\bcoinbase\b|\bmemecoins?\b|\bnft\b|\bdogecoin\b|\baltcoins?\b",
    "tecnologia": r"\bopenai\b|\banthropic\b|\bgpt-?\d|\bllm\b|artificial intelligence"
                  r"|\bchatgpt\b|\bnvidia\b|\bsemiconductors?\b|\bspacex\b|\bstarship\b"
                  r"|\brockets?\b|\bsatellites?\b|\bquantum\b|\brobotaxi\b|self-driving"
                  r"|\bagi\b|data ?center|\bchips?\b",
    "deportes": r"\bnba\b|\bnfl\b|\bmlb\b|\bnhl\b|\bsoccer\b|\bfifa\b|\bchampionships?\b"
                r"|super bowl|world cup|world series|premier league|\bucl\b|\bolympics?\b"
                r"|\bvs\.|\bmatch\b|\btournament\b|\bplayoffs?\b|\bf1\b|formula 1"
                r"|ballon d'or|\bfinals\b|\bleague\b",
    "entretenimiento": r"\boscars\b|academy award|\bgrammys?\b|\bemmys?\b|golden globe"
                       r"|box office|\bmovies?\b|\bfilms?\b|\balbums?\b|\bbillboard\b"
                       r"|\bnetflix\b|\bspotify\b|rotten tomatoes|\bconcerts?\b"
                       r"|\bsingers?\b|\bactors?\b|\bactress\b|\bcelebrity\b",
    "politica": r"\belections?\b|\bpresident\w*|\bsenate\b|\bcongress\b|\bnominations?\b"
                r"|\bprimary\b|\bgovernor\b|\bparliament\w*|\bvote[sd]?\b|\bimpeach\w*"
                r"|\bcabinet\b|\bministers?\b|\bcandidat\w*|\bpolls?\b|\breferend\w*"
                r"|\bmayor\b|\bchancellor\b|\bleader of\b|\bmidterms?\b|signed into law"
                r"|\bh\.r\.\d|\bparty\b|\bcoalition\b|\bout (?:before|by)\b",
    "geopolitica": r"\bukrain\w*|\brussia\w*|\bchina\b|\bchinese\b|\btaiwan\w*|\bisrael\w*"
                   r"|\bgaza\b|\biran\w*|\bnato\b|\bwar\b|\bceasefire\b|\bsanctions?\b"
                   r"|\btreaty\b|\bsummit\b|\bnuclear\b|\binvad\w*|\btroops\b"
                   r"|\bhostages?\b|\bvenezuela\b|\bgreenland\b|\bstrait\b|\bhormuz\b"
                   r"|\bregime\b|xi jinping",
    "economia": r"\bfed\b|interest rates?|\binflation\b|\bcpi\b|\bgdp\b|\brecession\b"
                r"|\btariffs?\b|\bunemployment\b|jobs report|central bank|\becb\b"
                r"|s&p 500|stock market",
    "empresas": r"\bipo\b|\bceo\b|\bacquir\w*|\bacquisitions?\b|\bmergers?\b|\bantitrust\b"
                r"|\blawsuits?\b|\bbankrupt\w*|\bearnings\b|\blayoffs?\b|\bresigns?\b"
                r"|\bshareholders?\b|market cap|\bdelisted?\b",
    "social": r"supreme court|\babortion\b|\bimmigration\b|\bdeport\w*|\bprotests?\b"
              r"|\bclimate\b|\bhurricanes?\b|\bpandemic\b|\bvaccines?\b|\bshootings?\b"
              r"|\bverdict\b|\bindict\w*|\bpardons?\b|\bepstein\b|\bnobel\b|\bprize\b",
}

# Deportes tiene un subconjunto que SÍ sirve: lo que se decide fuera de la
# cancha es un problema de leer noticias, no de modelar rendimiento.
SPORTS_NARRATIVE = r"fired|sacked|hired|transfer|sign|contract|retire|suspend|ban|trade"

# Preguntas con fecha límite: donde vive el sesgo de optimismo.
DEADLINE_PATTERN = r"\b(before|by|prior to)\b.{0,30}\b(20\d\d|january|february|march"\
                   r"|april|may|june|july|august|september|october|november|december)\b"

# Señal de datos cuantitativos parseables en texto — el alfa de PolyBench.
QUANTITATIVE_HINT = r"poll|survey|percent|seats|votes|index|rate|count|margin"


@dataclass(frozen=True, slots=True)
class MarketFilter:
    """
    Compuertas duras. Si un mercado no las pasa, no se gasta ni un token.
    Barato de evaluar: solo aritmética sobre metadatos.
    """

    min_price: float = 0.12
    max_price: float = 0.88
    """Fuera de esta banda el mercado ya tiene razón y el spread se lo come."""

    min_liquidity_usd: float = 15_000
    max_liquidity_usd: float = 2_000_000
    """
    El techo es deliberado. Los mercados gigantes están saturados de
    profesionales; no es donde un recién llegado encuentra desajustes.
    """

    min_days: int = 2
    max_days: int = 150
    """
    Menos de 2 días: no alcanzas a investigar ni a que se mueva el precio.
    Más de 150: demasiadas cosas pueden pasar, y tu capital queda inmóvil
    esperando una resolución lejana.
    """

    allowed_categories: frozenset[str] = frozenset(CATEGORY_PATTERNS) | {"otros"}
    sports_narrative_only: bool = False

    max_per_category: int = 2
    """
    Cuantos mercados como maximo de una misma categoria por ciclo.

    Sin esto el ciclo sale casi todo politica: es el 45% de lo que tiene
    Polymarket y ademas puntua alto, asi que copa los primeros puestos. Un
    ciclo de 12 daba 9 politicos.

    Repartir importa por dos razones. La calibracion ahora es por categoria
    y cada una necesita sus propias 30 muestras: concentrarse en una las
    deja a todas las demas dormidas para siempre. Y doce apuestas sobre la
    misma clase de evento no son doce apuestas, son una grande.
    """
    """Si deportes está permitido, solo la parte de narrativa (fichajes, despidos)."""

    exclude_categories: frozenset[str] = frozenset()
    """
    Vacío a propósito. Antes excluía `cripto` por la miscalibración que mide
    PolyBench, pero eso era necesario cuando la calibración era UNA curva
    para todo: el sesgo de cripto se comía la corrección de política.

    Ahora `CalibrationMap` segmenta por categoría, así que cada una aprende
    su propio sesgo. Cripto puede entrar: si el modelo es sobreconfiado ahí,
    su curva lo encoge sin tocar a las demás.
    """


@dataclass(slots=True)
class Candidate:
    """Un mercado que pasó las compuertas, con su puntaje de oportunidad."""

    condition_id: str
    slug: str
    question: str
    rules: str
    token_yes: str
    token_no: str
    price: float
    liquidity: float
    volume: float
    days_to_resolution: int
    category: str
    end_date: datetime | None
    score: float = 0.0
    score_detail: dict[str, float] = field(default_factory=dict)

    @property
    def is_deadline_shaped(self) -> bool:
        """Preguntas '¿ocurrirá antes de X?' — donde vive el sesgo de optimismo."""
        return bool(re.search(DEADLINE_PATTERN, self.question, re.I))


CATEGORIAS: list[str] = [*CATEGORY_PATTERNS, "otros"]
"""Los valores que `categorize` puede devolver. Se publican en /candidates."""


def categorize(slug: str, question: str) -> str:
    text = f"{slug} {question}".lower()
    for cat, pattern in CATEGORY_PATTERNS.items():
        if re.search(pattern, text):
            return cat
    return "otros"


def opportunity_score(c: Candidate) -> tuple[float, dict[str, float]]:
    """
    Dónde es más probable que el precio esté mal, no dónde es más probable
    que el evento ocurra. Son cosas distintas y confundirlas es caro.

    Puntaje en [0, 1]. Sirve para ordenar, no como probabilidad.
    """
    d: dict[str, float] = {}

    # ── precio: el centro es donde hay desacuerdo genuino ────────────────
    # 0.50 es máximo desacuerdo; los extremos son consenso y no pagan.
    d["precio"] = 1.0 - abs(c.price - 0.5) * 2

    # ── liquidez: campana, no rampa ──────────────────────────────────────
    # El óptimo está alrededor de $80k: suficiente para entrar y salir,
    # poco para atraer a los operadores que ya lo arbitraron todo.
    optimal = 80_000
    ratio = c.liquidity / optimal
    d["liquidez"] = min(ratio, 1 / ratio) if ratio > 0 else 0.0

    # ── horizonte: semanas, no meses ─────────────────────────────────────
    # 3 semanas es la ventana dulce: da tiempo a investigar y a que el
    # mercado reaccione, sin inmovilizar capital medio año.
    ideal_days = 21
    d["horizonte"] = 1.0 / (1.0 + abs(c.days_to_resolution - ideal_days) / ideal_days)

    # ── sesgo de optimismo explotable ────────────────────────────────────
    d["fecha_limite"] = 0.8 if c.is_deadline_shaped else 0.0

    # ── datos cuantitativos en texto = el alfa medido ────────────────────
    has_numbers = re.search(QUANTITATIVE_HINT, f"{c.question} {c.rules}", re.I)
    d["datos_parseables"] = 0.7 if has_numbers else 0.0

    # ── categoría ────────────────────────────────────────────────────────
    d["categoria"] = {"politica": 1.0, "geopolitica": 0.9, "economia": 0.7}.get(
        c.category, 0.3
    )

    # ── actividad relativa ───────────────────────────────────────────────
    # Volumen alto contra liquidez baja = el mercado se está moviendo y el
    # precio puede no haber terminado de ajustarse a una noticia reciente.
    turnover = c.volume / max(c.liquidity, 1)
    d["rotacion"] = min(turnover / 10, 1.0)

    weights = {
        "precio": 0.22,
        "liquidez": 0.18,
        "horizonte": 0.15,
        "fecha_limite": 0.12,
        "datos_parseables": 0.13,
        "categoria": 0.13,
        "rotacion": 0.07,
    }
    total = sum(d[k] * w for k, w in weights.items())
    return round(total, 4), {k: round(v, 3) for k, v in d.items()}


def passes(market: dict, f: MarketFilter, now: datetime) -> Candidate | None:
    """
    Aplica las compuertas duras a un mercado crudo de la Gamma API.
    Devuelve None si no pasa — sin gastar un solo token de LLM.
    """
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            return None
    if not prices or len(prices) != 2:
        return None

    tokens = market.get("clobTokenIds")
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except (json.JSONDecodeError, TypeError):
            return None
    if not tokens or len(tokens) != 2:
        return None

    try:
        price = float(prices[0])
        liquidity = float(market.get("liquidityNum") or 0)
        volume = float(market.get("volumeNum") or 0)
    except (TypeError, ValueError):
        return None

    if not f.min_price <= price <= f.max_price:
        return None
    if not f.min_liquidity_usd <= liquidity <= f.max_liquidity_usd:
        return None

    end_raw = market.get("endDate")
    if not end_raw:
        return None
    try:
        end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    days = (end - now).days
    if not f.min_days <= days <= f.max_days:
        return None

    slug = market.get("slug", "")
    question = market.get("question", "")
    category = categorize(slug, question)

    if category in f.exclude_categories:
        return None
    if category not in f.allowed_categories:
        # Deportes entra solo por la puerta de narrativa.
        narrative_ok = (
            category == "deportes"
            and "deportes" in f.allowed_categories | {"deportes"}
            and f.sports_narrative_only
            and re.search(SPORTS_NARRATIVE, question, re.I)
        )
        if not narrative_ok:
            return None

    c = Candidate(
        condition_id=market.get("conditionId", ""),
        slug=slug,
        question=question,
        rules=(market.get("description") or "")[:2000],
        token_yes=tokens[0],
        token_no=tokens[1],
        price=price,
        liquidity=liquidity,
        volume=volume,
        days_to_resolution=days,
        category=category,
        end_date=end,
    )
    c.score, c.score_detail = opportunity_score(c)
    return c


def select(
    markets: list[dict],
    f: MarketFilter | None = None,
    *,
    limit: int = 20,
    now: datetime | None = None,
    vistos: frozenset[str] = frozenset(),
) -> list[Candidate]:
    """
    De cientos de mercados a los N que merecen investigación.

    `limit` es un presupuesto, no una meta. Si solo 6 mercados valen la
    pena, se investigan 6. Forzar 20 significa gastar en los 14 peores.

    `vistos` son los slugs ya pronosticados hace poco. Van al final de su
    categoría, no se descartan: si no hay nada nuevo se vuelven a elegir.

    Sin esto el puntaje es determinista y los mismos mercados ganan todos
    los ciclos. Medido en producción: 178 pronósticos sobre 26 mercados,
    uno repetido 33 veces. Cada repetición cuesta plata y no aporta una
    muestra nueva de calibración, porque todas resuelven igual.
    """
    now = now or datetime.now(UTC)
    f = f or MarketFilter()
    candidates = [c for m in markets if (c := passes(m, f, now)) is not None]
    candidates.sort(key=lambda c: (c.slug in vistos, -c.score))

    # Reparto por categoria. Se recorre en orden de puntaje y se toma el
    # mejor de cada una por ronda, asi que dentro de cada categoria siguen
    # ganando los mejores y ninguna copa el ciclo.
    if f.max_per_category > 0:
        por_cat: dict[str, list[Candidate]] = {}
        for c in candidates:
            por_cat.setdefault(c.category, []).append(c)
        repartidos: list[Candidate] = []
        for ronda in range(f.max_per_category):
            for cat in sorted(por_cat, key=lambda k: -por_cat[k][0].score):
                if ronda < len(por_cat[cat]):
                    repartidos.append(por_cat[cat][ronda])
        repartidos.sort(key=lambda c: (c.slug in vistos, -c.score))
        candidates = repartidos

    return candidates[:limit]


def summarize(candidates: list[Candidate]) -> str:
    """Una línea por candidato, para el log y para que tú lo revises."""
    if not candidates:
        return "(ningún mercado pasó el filtro)"
    lines = [f"{'score':>6} {'precio':>7} {'liq':>10} {'días':>5}  {'cat':<12} pregunta"]
    for c in candidates:
        mark = " ⏱" if c.is_deadline_shaped else "  "
        lines.append(
            f"{c.score:>6.3f} {c.price:>7.3f} {c.liquidity:>10,.0f} "
            f"{c.days_to_resolution:>5}{mark}{c.category:<12} {c.question[:58]}"
        )
    return "\n".join(lines)
