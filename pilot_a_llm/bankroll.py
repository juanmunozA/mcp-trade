"""
Cuánto capital hay realmente disponible.

EL AGENTE NO DECIDE CUÁNTO ARRIESGAR
────────────────────────────────────
Lee el saldo; `edge.py` calcula Kelly fraccional sobre él; el risk kernel
lo acota contra límites que viven en variables de entorno. Tres capas.

La diferencia importa: un agente que elige su propio tamaño de posición
puede racionalizar "esta vez estoy muy seguro" y duplicar. Kelly no
racionaliza — es una fórmula, y con la fracción a 0.20 sobrevive a que
la probabilidad esté mal estimada, que es el caso normal.

RED CORRECTA
────────────
Polymarket vive en **Polygon** (chain_id 137) y su colateral es **pUSD**.
Fondos enviados por BNB Smart Chain, Ethereum mainnet o cualquier otra red
NO llegan y recuperarlos requiere un puente. Al retirar de un exchange,
la red debe decir POLYGON.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger("bankroll")

# Polygon, no BNB. El error más caro que puedes cometer depositando.
POLYGON_CHAIN_ID = 137

# Fuente pública de posiciones y saldos de Polymarket.
DATA_API = "https://data-api.polymarket.com"


@dataclass(frozen=True, slots=True)
class Bankroll:
    """Foto del capital en un instante."""

    cash_usd: float
    """Colateral libre, listo para abrir posiciones."""

    positions_usd: float
    """Valor de mercado de lo que ya está abierto."""

    source: str
    """`onchain`, `paper` o `fallback`. Queda en el log: si el agente
    operó con un saldo estimado en vez de real, quieres saberlo."""

    at: datetime

    @property
    def total_usd(self) -> float:
        return self.cash_usd + self.positions_usd

    @property
    def deployable_usd(self) -> float:
        """
        Lo que Kelly puede dimensionar. Solo el efectivo libre: contar
        posiciones abiertas como capital disponible es apalancarse sin
        darse cuenta.
        """
        return max(0.0, self.cash_usd)

    def __str__(self) -> str:
        return (
            f"${self.total_usd:,.2f} total "
            f"(${self.cash_usd:,.2f} libre + ${self.positions_usd:,.2f} en posiciones) "
            f"[{self.source}]"
        )


class BankrollReader:
    """
    En paper devuelve un saldo simulado que sí evoluciona con el PnL, para
    que el dimensionamiento se comporte como en vivo. En live lee la cadena.
    """

    def __init__(
        self,
        *,
        funder_address: str | None = None,
        paper_starting_usd: float = 150.0,
        is_paper: bool = True,
    ) -> None:
        self.funder = funder_address
        self.is_paper = is_paper
        self._paper_cash = paper_starting_usd
        self._paper_positions = 0.0

    # ── paper ────────────────────────────────────────────────────────────

    def on_paper_fill(self, cost_usd: float) -> None:
        self._paper_cash -= cost_usd
        self._paper_positions += cost_usd

    def on_paper_resolution(self, payout_usd: float, cost_usd: float) -> None:
        self._paper_positions -= cost_usd
        self._paper_cash += payout_usd

    # ── lectura ──────────────────────────────────────────────────────────

    async def read(self) -> Bankroll:
        now = datetime.now(UTC)

        if self.is_paper:
            return Bankroll(
                cash_usd=round(self._paper_cash, 2),
                positions_usd=round(self._paper_positions, 2),
                source="paper",
                at=now,
            )

        if not self.funder:
            raise RuntimeError(
                "modo live sin PILOT_A_FUNDER: no hay dirección que consultar"
            )

        try:
            cash, positions = await self._read_onchain(self.funder)
            return Bankroll(cash, positions, "onchain", now)
        except Exception as exc:  # noqa: BLE001
            # Fail closed: sin saldo confirmado no se dimensiona nada. Devolver
            # un número inventado sería peor que no operar.
            log.error("no se pudo leer el saldo on-chain: %s", exc)
            return Bankroll(0.0, 0.0, "fallback", now)

    @staticmethod
    async def _read_onchain(address: str) -> tuple[float, float]:
        """Saldo de colateral y valor de las posiciones, vía Data API."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(f"{DATA_API}/value", params={"user": address})
            r.raise_for_status()
            payload = r.json()

            positions = 0.0
            if isinstance(payload, list):
                positions = sum(float(p.get("value", 0) or 0) for p in payload)
            elif isinstance(payload, dict):
                positions = float(payload.get("value", 0) or 0)

            rb = await http.get(f"{DATA_API}/balance", params={"user": address})
            cash = 0.0
            if rb.status_code == 200:
                b = rb.json()
                raw = b.get("balance", b) if isinstance(b, dict) else b
                try:
                    cash = float(raw)
                except (TypeError, ValueError):
                    cash = 0.0

        return cash, positions
