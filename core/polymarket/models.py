"""Tipos del dominio. Deliberadamente pobres en comportamiento."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class Book:
    token_id: str
    ts: datetime
    bids: tuple[Level, ...]   # descendente por precio
    asks: tuple[Level, ...]   # ascendente por precio

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        return (b + a) / 2 if b is not None and a is not None else None

    @property
    def spread(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        return a - b if b is not None and a is not None else None

    def depth(self, side: str, within: float = 0.05) -> float:
        """Liquidez agregada dentro de `within` del mid. Mide si puedes salir."""
        mid = self.mid
        if mid is None:
            return 0.0
        levels = self.bids if side == "bid" else self.asks
        return sum(lv.size for lv in levels if abs(lv.price - mid) <= within)

    def cost_to_fill(self, side: str, shares: float) -> float | None:
        """
        Precio medio real de barrer `shares` del book. Esto —no el mid— es
        tu costo. Un backtest que use el mid está mintiendo.
        """
        levels = self.asks if side == "BUY" else self.bids
        remaining, cost = shares, 0.0
        for lv in levels:
            take = min(remaining, lv.size)
            cost += take * lv.price
            remaining -= take
            if remaining <= 1e-9:
                return cost / shares
        return None  # no hay profundidad suficiente


@dataclass(frozen=True, slots=True)
class Market:
    """Un mercado binario: dos tokens complementarios que suman $1 al resolver."""

    condition_id: str
    slug: str
    question: str
    token_up: str
    token_down: str
    end_date: datetime | None

    def other_side(self, token_id: str) -> str:
        if token_id == self.token_up:
            return self.token_down
        if token_id == self.token_down:
            return self.token_up
        raise ValueError(f"{token_id} no pertenece a {self.slug}")


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    status: str
    is_paper: bool
    latency_ms: int | None = None
    raw: dict | None = None
