"""
Configuración tipada. Una sola fuente de verdad.

Nota de diseño importante: **los límites de riesgo NO viven aquí.**
`Settings` es el objeto que circula por la app y que eventualmente termina
serializado en logs, en contexto del LLM, en dashboards. Los límites se leen
aparte con `RiskLimits.from_env()`, directamente del entorno, y nunca entran
a un objeto que el estratega pueda ver. Si un límite está en `Settings`,
tarde o temprano el modelo lo lee y empieza a razonar sobre cómo llegar a él.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Puente obligatorio: pydantic-settings lee el .env hacia SU objeto, pero el
# risk kernel lee `os.environ` directamente y a propósito — para que los
# límites nunca entren a un objeto que el LLM pueda ver. Sin esto, los
# límites no existen en local y `RiskLimits.from_env` revienta.
# En Render no hace falta (la plataforma los inyecta), pero es idempotente.
load_dotenv(override=False)


class Mode(StrEnum):
    COLLECT = "collect"   # solo graba datos, nunca ordena
    PAPER = "paper"       # simula fills contra el book real
    LIVE = "live"         # plata real


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    mode: Mode = Mode.COLLECT
    i_understand_live: str = "no"

    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    clob_ws: str = "wss://ws-subscriptions-clob.polymarket.com/ws"
    chain_id: int = 137
    signature_type: int = 1

    pilot_a_private_key: SecretStr | None = None
    pilot_a_funder: str | None = None
    pilot_b_private_key: SecretStr | None = None
    pilot_b_funder: str | None = None

    database_url: str = "postgresql://trade:trade@localhost:5432/mcptrade"

    # ── modelos ──
    gemini_api_key: SecretStr | None = None
    cloudflare_account_id: str | None = None
    cloudflare_api_token: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # ── modo local: sin nubes, sin cuotas, sin credenciales ──
    local_mode: bool = False
    """
    Todo corre en la máquina: Ollama para los modelos, SearXNG para buscar,
    Postgres en contenedor. No se usa ninguna API externa, así que no hay
    cuotas que agotar ni claves que rotar.

    El precio: los modelos que caben en RAM están peor calibrados que los
    de frontera, y la calibración es justamente el trabajo. Para eso está
    el bucle de resolución — mide si sirve o no con TUS datos.
    """
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "deepseek-r1:8b"
    """
    Según la RAM libre:
        deepseek-r1:1.5b   ~1.1 GB   solo triaje
        deepseek-r1:8b     ~5   GB   mínimo razonable  ← default
        deepseek-r1:14b    ~9   GB   notablemente mejor
        deepseek-r1:32b    ~20  GB   el que medimos en Cloudflare
    """
    ollama_model_triage: str = ""
    """
    Modelo chico para el primer escalón de la cascada. Vacío = se usa el
    mismo para todo, que en local es más simple y casi igual de rápido:
    el costo aquí es tiempo de CPU, no cuota.
    """
    searxng_host: str = "http://localhost:8888"

    # Groq: segunda opinión rápida en modo local. Opcional.
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    """
    Lo que ata en el free tier no son las peticiones sino los tokens
    diarios, y con prompts de ~5.500 tokens:

        llama-3.3-70b-versatile   100K TPD  →  ~18 pronósticos/día
        openai/gpt-oss-120b       200K TPD  →  ~36 pronósticos/día

    Por eso solo se usa en el segundo escalón de la cascada. DeepSeek-R1
    fue deprecado en Groq en septiembre de 2025.
    """

    # Juicio: probabilidad calibrada. Pocas llamadas, alta calidad.
    strategist_model: str = "gemini-2.5-flash"
    # Triaje: filtrar cientos de mercados. Volumen, salida corta.
    triage_model: str = "@cf/meta/llama-3.1-8b-instruct"
    strategist_interval_min: int = 240

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    sets_max_combined_cost: float = 0.988
    sets_max_imbalance_shares: float = 50.0
    sets_market_slugs: str = ""

    stale_data_seconds: int = Field(default=30, ge=5)

    @model_validator(mode="after")
    def _live_needs_two_keys(self) -> Settings:
        """Pasar a live exige dos variables, no una. Es a propósito."""
        if self.mode is Mode.LIVE and self.i_understand_live.strip().lower() != "yes":
            raise ValueError(
                "MODE=live requiere además I_UNDERSTAND_LIVE=yes. "
                "Si estás leyendo esto por accidente, ese es justamente el punto."
            )
        return self

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE

    @property
    def market_slugs(self) -> list[str]:
        return [s.strip() for s in self.sets_market_slugs.split(",") if s.strip()]

    def credentials(self, pilot: str) -> tuple[SecretStr | None, str | None]:
        """Cada piloto tiene su propia wallet. Nunca se comparten."""
        match pilot.lower():
            case "a":
                return self.pilot_a_private_key, self.pilot_a_funder
            case "b":
                return self.pilot_b_private_key, self.pilot_b_funder
            case _:
                raise ValueError(f"piloto desconocido: {pilot}")

    def redacted(self) -> dict:
        """Lo único que se puede loguear o mandar a un LLM."""
        return {
            "mode": str(self.mode),
            "chain_id": self.chain_id,
            "strategist_model": self.strategist_model,
            "strategist_interval_min": self.strategist_interval_min,
            "market_slugs": self.market_slugs,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
