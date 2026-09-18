"""
Una sola interfaz para todos los modelos.

Existe por tres razones, en orden de importancia:

 1. **Los free tiers se mueren.** Cerebras mató el suyo en agosto de 2026,
    Google sacó Pro del free en abril. Cambiar de proveedor tiene que ser
    una variable de entorno, no una refactorización.
 2. **El ensemble necesita familias distintas.** Los errores de Google y de
    DeepSeek están descorrelacionados; los de dos modelos de Google, no.
    `calibration.ensemble_weights()` los pondera solo por Brier real.
 3. **Triaje y juicio son trabajos distintos.** Filtrar 500 mercados es
    volumen con salida corta (Cloudflare, modelos abiertos, ilimitado en
    la práctica). Estimar una probabilidad calibrada es juicio, y ahí
    pagas por calidad (Gemini).

Todos los proveedores devuelven `Completion`, con tokens y costo, para que
el costo del experimento sea medible y no una sorpresa a fin de mes.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# $/1M tokens (entrada, salida). 0 = free tier.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.0, 0.0),
    "gemini-2.5-flash-lite": (0.0, 0.0),
    "gemini-3-flash-preview": (0.0, 0.0),
    "cloudflare": (0.0, 0.0),
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class ProviderError(RuntimeError):
    pass


class QuotaExhausted(ProviderError):
    """
    El proveedor devolvió 429: se acabó la cuota del día.

    Tiene su propia clase porque exige una reacción distinta a un fallo
    normal. Medido en producción: el presupuesto vive en memoria, así que
    al reiniciar el proceso cree tener el cupo entero. Recibía 429 de
    Cloudflare, no aprendía, y seguía gastando llamadas a Gemini —cuota
    aparte y también limitada— investigando mercados cuyo pronóstico iba
    a fallar de todos modos.

    Un 429 es la única fuente autorizada sobre el estado real de la cuota.
    """


@dataclass(slots=True)
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cost_usd: float = 0.0
    neurons: float = 0.0
    """Consumo real de Cloudflare, de la cabecera `cf-ai-neurons`."""
    latency_ms: int = 0
    finish_reason: str = ""
    grounded_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None

    def json(self) -> dict | None:
        """
        Parsea la respuesta como JSON, tolerando el cercado en markdown.

        Hace falta: `gemini-2.5-flash-lite` envuelve en ```json aunque el
        prompt lo prohíba explícitamente. Un modelo que no sigue una
        instrucción de formato tampoco va a seguir una de calibración —
        vale la pena registrarlo, no solo perdonarlo.
        """
        text = self.text.strip()
        if m := _FENCE.match(text):
            text = m.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # último recurso: el primer objeto balanceado del texto
            start = text.find("{")
            if start == -1:
                return None
            depth = 0
            for i, ch in enumerate(text[start:], start):
                depth += (ch == "{") - (ch == "}")
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
            return None

    @property
    def used_fence(self) -> bool:
        """True si el modelo ignoró la instrucción de no usar markdown."""
        return bool(_FENCE.match(self.text.strip()))


class Provider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        search: bool = False,
    ) -> Completion: ...


def _cost(model: str, inp: int, out: int) -> float:
    prices = PRICING.get(model, (0.0, 0.0))
    return round(inp / 1e6 * prices[0] + out / 1e6 * prices[1], 6)


# `responseSchema` de Gemini es un subconjunto de OpenAPI, no JSON Schema
# completo. Solo acepta estos campos; cualquier otro devuelve un 400.
_GEMINI_OK = {
    "type", "format", "description", "nullable", "enum",
    "items", "properties", "required", "propertyOrdering",
}


def _para_gemini(schema: dict) -> dict:
    """
    Poda el esquema a lo que Gemini acepta.

    Hace falta porque el mismo esquema se usa con Cloudflare, que sí quiere
    `additionalProperties: false` para forzar salida estricta. Enviárselo a
    Gemini rompía el respaldo entero:

        400 Unknown name "additionalProperties" at
            'generation_config.response_schema'

    Y lo peor: solo se veía cuando Cloudflare ya estaba agotado, que es
    justo cuando el respaldo tiene que funcionar.
    """
    if not isinstance(schema, dict):
        return schema
    podado: dict[str, Any] = {}
    for k, v in schema.items():
        if k not in _GEMINI_OK:
            continue
        if k == "properties" and isinstance(v, dict):
            podado[k] = {pk: _para_gemini(pv) for pk, pv in v.items()}
        elif k == "items":
            podado[k] = _para_gemini(v)
        else:
            podado[k] = v
    return podado


# ───────────────────────────── Gemini ────────────────────────────────────


class GeminiProvider:
    """
    Capa de juicio. Free tier: 250 req/día en flash, 1.000 en flash-lite.

    Dos capacidades que lo hacen la elección correcta aquí:
      · `responseSchema` — salida estructurada garantizada por el servidor,
        no por un prompt que ruega.
      · `google_search` — grounding nativo, sin API de noticias aparte.
    """

    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.name = "gemini"
        self.model = model
        self._key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        search: bool = False,
        thinking_budget: int | None = None,
    ) -> Completion:
        """
        `thinking_budget`: Gemini 2.5 razona por defecto y **esos tokens
        cuentan contra `maxOutputTokens`**. Medido: 621 tokens de thinking
        de un presupuesto de 800 dejaron 164 para el JSON, que llegó
        cortado a media frase.

        Reglas:
          · extracción estructurada → `thinking_budget=0` (es puro desperdicio)
          · pronóstico de probabilidad → dejar razonar, pero con
            `max_tokens` holgado (8k+), porque el razonamiento es justo lo
            que mejora la calibración
        """
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if thinking_budget is not None:
            body["generationConfig"]["thinkingConfig"] = {
                "thinkingBudget": thinking_budget
            }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        if schema is not None:
            # Gemini no permite schema y búsqueda a la vez: la búsqueda
            # devuelve texto con citas. Se resuelve en dos pasos —
            # investigar con búsqueda, estructurar después.
            if search:
                raise ProviderError(
                    "responseSchema y google_search son incompatibles: "
                    "usa dos llamadas (investigar → estructurar)"
                )
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = _para_gemini(schema)
        elif search:
            body["tools"] = [{"google_search": {}}]

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(
                f"{self.BASE}/models/{self.model}:generateContent",
                params={"key": self._key},
                json=body,
            )
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            raise ProviderError(f"gemini HTTP {r.status_code}: {r.text[:300]}")

        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(f"gemini sin candidatos: {json.dumps(data)[:300]}")

        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        finish = cand.get("finishReason", "")

        usage = data.get("usageMetadata", {})
        inp = usage.get("promptTokenCount", 0)
        out = usage.get("candidatesTokenCount", 0)
        thoughts = usage.get("thoughtsTokenCount", 0)

        # Fail loud, nunca silencioso. Una respuesta truncada devuelve JSON
        # inválido, el pronóstico cae al fallback, y el piloto se queda flat
        # sin que nadie sepa por qué. Preferimos el error.
        if finish == "MAX_TOKENS":
            raise ProviderError(
                f"gemini truncó la respuesta (max_tokens={max_tokens}): "
                f"{thoughts} tokens de thinking + {out} de salida. "
                f"Sube max_tokens o pasa thinking_budget=0."
            )
        if finish not in ("STOP", "", None):
            raise ProviderError(f"gemini terminó con finishReason={finish}")

        urls: list[str] = []
        for chunk in cand.get("groundingMetadata", {}).get("groundingChunks", []):
            if uri := chunk.get("web", {}).get("uri"):
                urls.append(uri)

        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            thought_tokens=thoughts,
            cost_usd=_cost(self.model, inp, out),
            latency_ms=latency,
            finish_reason=finish,
            grounded_urls=urls,
            raw=data,
        )


# ─────────────────────────── Cloudflare ──────────────────────────────────


class CloudflareProvider:
    """
    Capa de triaje. 10.000 Neurons/día, sin tarjeta.

    El pool de Neurons es **compartido entre todos los modelos** y el costo
    por Neuron escala con el tamaño. `llama-3.1-8b` filtra cientos de
    mercados con el presupuesto que `gpt-oss-120b` gasta en unas decenas.
    Usa el grande solo cuando el chico no alcance.
    """

    BASE = "https://api.cloudflare.com/client/v4/accounts"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = "@cf/meta/llama-3.1-8b-instruct",
    ) -> None:
        self.name = "cloudflare"
        self.model = model
        self._account = account_id
        self._token = api_token

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        search: bool = False,
    ) -> Completion:
        if search:
            raise ProviderError("Workers AI no tiene búsqueda web; usa Gemini")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": schema}

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(
                f"{self.BASE}/{self._account}/ai/run/{self.model}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=body,
            )
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code == 429:
            raise QuotaExhausted("cloudflare: se acabaron los Neurons del día")
        if r.status_code != 200:
            raise ProviderError(f"cloudflare HTTP {r.status_code}: {r.text[:300]}")

        data = r.json()
        if not data.get("success", True):
            raise ProviderError(f"cloudflare: {data.get('errors')}")

        result = data.get("result") or {}
        response = result.get("response", "")
        # Algunos modelos devuelven dict ya parseado con response_format
        text = response if isinstance(response, str) else json.dumps(response)

        usage = result.get("usage", {})
        inp = usage.get("prompt_tokens", 0)
        out = usage.get("completion_tokens", 0)

        # Consumo REAL, no estimado. Cloudflare lo devuelve por respuesta;
        # sin esto el presupuesto se descubre al agotarlo.
        neurons = float(r.headers.get("cf-ai-neurons", usage.get("neurons", 0) or 0))

        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=0.0,
            neurons=neurons,
            latency_ms=latency,
            raw=data,
        )


# ───────────────────────────── Groq ──────────────────────────────────────


class GroqProvider:
    """
    Inferencia en LPU: ~100× más rápida que un modelo local en CPU.

    PARA QUÉ SIRVE AQUÍ
    Corriendo local, el ensemble usa un solo modelo y con eso se pierde el
    veto por desacuerdo — el que evitó la peor apuesta que el sistema
    habría hecho. Groq lo devuelve: es otra familia (Llama vs DeepSeek),
    así que sus errores están descorrelacionados, que es lo que hace
    informativo el desacuerdo.

    EL LÍMITE REAL NO ES EL QUE ANUNCIAN
    El free tier dice 1.000 peticiones/día, pero lo que ata son los tokens
    diarios:

        llama-3.3-70b-versatile   100K TPD  →  ~18 pronósticos/día
        openai/gpt-oss-120b       200K TPD  →  ~36 pronósticos/día

    (con prompts de ~5.500 tokens, que es lo que mide este agente con
    evidencia incluida). Por eso Groq va SOLO en el segundo escalón de la
    cascada: los ~4 mercados por ciclo que el modelo local marca como
    interesantes, no los 15.

    Los límites son por organización, no por key: varias keys no dan más.
    """

    BASE = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.name = "groq"
        self.model = model
        self._key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        search: bool = False,
        thinking_budget: int | None = None,
    ) -> Completion:
        if search:
            raise ProviderError("Groq no navega; la búsqueda la hace SearXNG o Gemini")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if schema is not None:
            # `strict: true` usa decodificación restringida: el servidor
            # garantiza que la salida encaja en el esquema. Mejor que pedirlo
            # en el prompt y esperar que obedezca.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "forecast",
                    "schema": schema,
                    "strict": True,
                },
            }

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(
                f"{self.BASE}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=body,
            )
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code == 429:
            raise QuotaExhausted(
                "groq: cuota agotada. Lo que ata suele ser el límite de "
                "tokens diarios, no el de peticiones."
            )
        if r.status_code != 200:
            raise ProviderError(f"groq HTTP {r.status_code}: {r.text[:300]}")

        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content", "")
        usage = data.get("usage", {})

        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=0.0,
            latency_ms=latency,
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )


# ─────────────────────────── Ollama local ────────────────────────────────


class OllamaProvider:
    """
    Modelos abiertos corriendo en tu propia máquina o en un contenedor.

    NO PROBADO en este entorno: el portátil de desarrollo (i5-1135G7, sin
    GPU, 16 GB) no aguanta un modelo útil a velocidad razonable. El código
    está escrito contra la API documentada de Ollama; la primera vez que
    corras el contenedor, verifica que `/api/chat` responde antes de
    meterlo al ensemble.

    CUÁNDO USARLO
    Cuando quieras volumen sin límite de cuota: triaje de cientos de
    mercados, extracción de reglas de resolución, resúmenes. Para el juicio
    calibrado siguen siendo mejores los modelos grandes por API — un modelo
    que cabe en tu RAM está peor calibrado, y la calibración es el trabajo.

    MODELOS SEGÚN LA MÁQUINA
        qwen3:4b          ~2.5 GB   triaje, cabe casi en cualquier lado
        qwen3:8b          ~5 GB     razonamiento básico
        qwen3:14b         ~9 GB     el mínimo para pronóstico serio
        gpt-oss:20b       ~12 GB    necesita 16 GB libres de verdad
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        *,
        timeout: float = 600.0,
        keep_alive: str = "-1",
    ) -> None:
        self.name = "ollama"
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        """
        `-1` mantiene el modelo cargado en RAM indefinidamente. Sin esto
        Ollama lo descarga a los 5 minutos y la siguiente llamada paga de
        nuevo la carga completa — que en CPU son minutos, no segundos.
        """

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        search: bool = False,
        thinking_budget: int | None = None,
    ) -> Completion:
        if search:
            raise ProviderError("Ollama no navega; la búsqueda web la hace Gemini")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        # Ollama acepta un JSON Schema en `format` y fuerza la salida.
        if schema is not None:
            body["format"] = schema

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                r = await http.post(f"{self.host}/api/chat", json=body)
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Ollama no responde en {self.host}: {exc}. "
                "¿Está el contenedor arriba? `docker compose up -d ollama`"
            ) from exc
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code == 404:
            raise ProviderError(
                f"modelo '{self.model}' no descargado. "
                f"Corre: docker compose exec ollama ollama pull {self.model}"
            )
        if r.status_code != 200:
            raise ProviderError(f"ollama HTTP {r.status_code}: {r.text[:300]}")

        data = r.json()
        text = (data.get("message") or {}).get("content", "")

        # Los modelos de razonamiento (qwen3, deepseek-r1) emiten <think>.
        # Completion.json() ya busca el último objeto balanceado, así que
        # el bloque de razonamiento no estorba.
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            cost_usd=0.0,
            neurons=0.0,
            latency_ms=latency,
            finish_reason=data.get("done_reason", ""),
            raw=data,
        )

    async def available(self) -> bool:
        """¿Está el contenedor arriba y el modelo descargado?"""
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(f"{self.host}/api/tags")
                if r.status_code != 200:
                    return False
                names = [m["name"] for m in r.json().get("models", [])]
                return any(n.startswith(self.model.split(":")[0]) for n in names)
        except Exception:  # noqa: BLE001
            return False


# ───────────────────────────── factory ───────────────────────────────────


def build_providers(settings) -> dict[str, Provider]:
    """
    Construye lo que haya credenciales para construir. Nunca falla por un
    proveedor ausente: el ensemble funciona con los que estén.
    """
    out: dict[str, Provider] = {}

    if key := getattr(settings, "gemini_api_key", None):
        secret = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key)
        out["gemini"] = GeminiProvider(secret, settings.strategist_model)

    acc = getattr(settings, "cloudflare_account_id", None)
    tok = getattr(settings, "cloudflare_api_token", None)
    if acc and tok:
        secret = tok.get_secret_value() if hasattr(tok, "get_secret_value") else str(tok)
        out["cloudflare"] = CloudflareProvider(str(acc), secret, settings.triage_model)

    if not out:
        raise ProviderError(
            "ningún proveedor configurado: falta GEMINI_API_KEY o CLOUDFLARE_*"
        )
    return out
