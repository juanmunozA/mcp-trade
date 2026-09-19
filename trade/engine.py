"""
Motor de complete sets — la mecánica descrita en el tweet de @RetroValix
sobre el usuario `norm1e69`.

LA IDEA
───────
En un mercado binario, UP y DOWN son complementarios: al resolver, uno paga
$1 y el otro $0. Si logras comprar 1 share de cada uno por un total menor a
$1, tienes un *complete set* con ganancia garantizada, independiente del
resultado.

Costo medio observado en `norm1e69`: 98.69¢ → edge ≈ 1.31¢ por set.

DOS MANERAS DE ARMARLO
──────────────────────
  ATÓMICA   Compras ambos lados en el mismo instante porque el book ya
            ofrece ask_up + ask_down < $1. Sin riesgo direccional.
            **Este es el juego que ya perdiste.** Reportes de 2026 ponen la
            ventana media de una oportunidad en ~2.7s, con la mayoría del
            PnL capturado por bots sub-100ms. Desde un VPS normal vas a ver
            la oportunidad y llegar tarde. Lo dejamos implementado para
            medir cuánto te pierdes: tu *fill rate* aquí es el termómetro
            de si tienes infraestructura para competir.

  LEGGING   Acumulas un lado, esperas a que el subyacente se mueva, y
            compras el lado opuesto ahora más barato. `norm1e69` hizo esto
            en 88.76% de sus mercados. **Aquí sí hay espacio**, pero ojo:
            mientras estés desbalanceado tienes riesgo direccional puro.
            Ese es el "residual direccional" del tweet, y es lo que puede
            comerse los 1.31¢ de edge treinta veces seguidas.

El motor solo PROPONE intenciones. El risk kernel decide.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.polymarket.models import Book, Market
from core.risk import OrderIntent, Side

log = logging.getLogger("pilot_b.engine")


@dataclass
class Inventory:
    """Inventario por mercado. `paired` es edge cerrado; el resto es apuesta."""

    condition_id: str
    shares_up: float = 0.0
    shares_down: float = 0.0
    avg_cost_up: float = 0.0
    avg_cost_down: float = 0.0

    @property
    def paired(self) -> float:
        return min(self.shares_up, self.shares_down)

    @property
    def imbalance(self) -> float:
        """Positivo = sobras de UP. Negativo = sobras de DOWN."""
        return self.shares_up - self.shares_down

    @property
    def combined_cost(self) -> float:
        return self.avg_cost_up + self.avg_cost_down

    @property
    def locked_edge(self) -> float:
        """Ganancia ya asegurada por los sets completos, antes de fees."""
        return self.paired * max(0.0, 1.0 - self.combined_cost)

    def add(self, side_is_up: bool, price: float, shares: float) -> None:
        if side_is_up:
            total = self.shares_up + shares
            self.avg_cost_up = (self.avg_cost_up * self.shares_up + price * shares) / total
            self.shares_up = total
        else:
            total = self.shares_down + shares
            self.avg_cost_down = (self.avg_cost_down * self.shares_down + price * shares) / total
            self.shares_down = total


@dataclass
class EngineConfig:
    max_combined_cost: float = 0.988   # solo arma set si Up+Down <= esto
    max_imbalance_shares: float = 50.0
    min_order_shares: float = 5.0
    max_order_shares: float = 20.0
    # fracción de la profundidad disponible que estamos dispuestos a tomar
    depth_fraction: float = 0.25


@dataclass
class Proposal:
    intents: list[OrderIntent] = field(default_factory=list)
    kind: str = ""          # atomic | leg | rebalance | none
    note: str = ""


class SetEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.cfg = config
        self._inv: dict[str, Inventory] = {}

    def inventory(self, condition_id: str) -> Inventory:
        return self._inv.setdefault(condition_id, Inventory(condition_id))

    def on_fill(self, market: Market, token_id: str, price: float, shares: float) -> None:
        self.inventory(market.condition_id).add(token_id == market.token_up, price, shares)

    # ── el cerebro (sin IA, a propósito) ─────────────────────────────────

    def evaluate(self, market: Market, book_up: Book, book_down: Book) -> Proposal:
        inv = self.inventory(market.condition_id)

        # 1. Guardarraíl primero: si estamos muy desbalanceados, lo único
        #    permitido es volver al centro. El residual direccional es lo
        #    que mata este negocio.
        if abs(inv.imbalance) > self.cfg.max_imbalance_shares:
            return self._rebalance(market, book_up, book_down, inv)

        # 2. ¿Hay set atómico disponible ahora mismo?
        atomic = self._try_atomic(market, book_up, book_down)
        if atomic.intents:
            return atomic

        # 3. Si no, ¿vale la pena poner una pata?
        return self._try_leg(market, book_up, book_down, inv)

    # ── atómico ──────────────────────────────────────────────────────────

    def _try_atomic(self, market: Market, book_up: Book, book_down: Book) -> Proposal:
        size = self._sizeable(book_up, book_down)
        if size < self.cfg.min_order_shares:
            return Proposal(kind="none", note="sin profundidad")

        cost_up = book_up.cost_to_fill("BUY", size)
        cost_down = book_down.cost_to_fill("BUY", size)
        if cost_up is None or cost_down is None:
            return Proposal(kind="none", note="book insuficiente para barrer")

        combined = cost_up + cost_down
        if combined > self.cfg.max_combined_cost:
            return Proposal(kind="none", note=f"combinado {combined:.4f} sin edge")

        note = f"set atómico @ {combined:.4f} (edge {1 - combined:.4f}/share)"
        log.info("%s | %s", market.slug, note)
        return Proposal(
            kind="atomic",
            note=note,
            intents=[
                OrderIntent("b", market.token_up, Side.BUY, cost_up, size, market.slug, note),
                OrderIntent("b", market.token_down, Side.BUY, cost_down, size, market.slug, note),
            ],
        )

    # ── legging ──────────────────────────────────────────────────────────

    def _try_leg(
        self, market: Market, book_up: Book, book_down: Book, inv: Inventory
    ) -> Proposal:
        """
        Compra el lado que nos falta, PERO solo si el precio de hoy mantiene
        el costo combinado proyectado por debajo del umbral. Sin esta
        condición esto degenera en comprar lo que esté barato, que es
        simplemente apostar.
        """
        # ¿Qué lado nos falta? Si estamos parejos, el que esté más barato.
        if inv.imbalance > 0:
            need_up, book, token, held_cost = False, book_down, market.token_down, inv.avg_cost_up
        elif inv.imbalance < 0:
            need_up, book, token, held_cost = True, book_up, market.token_up, inv.avg_cost_down
        else:
            if book_up.best_ask is None or book_down.best_ask is None:
                return Proposal(kind="none", note="sin ask")
            need_up = book_up.best_ask <= book_down.best_ask
            book = book_up if need_up else book_down
            token = market.token_up if need_up else market.token_down
            held_cost = 0.0

        size = min(
            self._sizeable(book, book),
            abs(inv.imbalance) or self.cfg.max_order_shares,
        )
        if size < self.cfg.min_order_shares:
            return Proposal(kind="none", note="pata muy chica")

        price = book.cost_to_fill("BUY", size)
        if price is None:
            return Proposal(kind="none", note="sin profundidad para la pata")

        projected = held_cost + price
        if held_cost > 0 and projected > self.cfg.max_combined_cost:
            return Proposal(
                kind="none",
                note=f"la pata dejaría el combinado en {projected:.4f}, por encima del umbral",
            )

        # Con inventario en cero exigimos que la pata sea barata por sí sola:
        # si pagas 0.60 por el primer lado, necesitas el otro a 0.38 y eso
        # rara vez llega. Media del umbral es una heurística conservadora.
        if held_cost == 0 and price > self.cfg.max_combined_cost / 2:
            return Proposal(
                kind="none",
                note=f"primera pata a {price:.4f}: muy cara para arrancar",
            )

        note = (
            f"pata {'UP' if need_up else 'DOWN'} @ {price:.4f} "
            f"(combinado proyectado {projected:.4f})"
        )
        return Proposal(
            kind="leg",
            note=note,
            intents=[OrderIntent("b", token, Side.BUY, price, size, market.slug, note)],
        )

    # ── rebalanceo ───────────────────────────────────────────────────────

    def _rebalance(
        self, market: Market, book_up: Book, book_down: Book, inv: Inventory
    ) -> Proposal:
        """
        Estamos pasados de un lado. Compramos el lado corto **a cualquier
        precio razonable** — aquí la prioridad es cerrar el riesgo
        direccional, no el edge. Perder 0.5¢ de edge es infinitamente mejor
        que quedar largo 200 shares de UP cuando el BTC se cae.
        """
        long_up = inv.imbalance > 0
        book = book_down if long_up else book_up
        token = market.token_down if long_up else market.token_up

        size = min(abs(inv.imbalance), self.cfg.max_order_shares)
        price = book.cost_to_fill("BUY", size) or book.best_ask
        if price is None:
            return Proposal(kind="none", note="rebalanceo bloqueado: sin ask")

        note = f"REBALANCE: desbalance {inv.imbalance:+.1f} shares, cerrando el lado corto"
        log.warning("%s | %s", market.slug, note)
        return Proposal(
            kind="rebalance",
            note=note,
            intents=[OrderIntent("b", token, Side.BUY, price, size, market.slug, note)],
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _sizeable(self, book_a: Book, book_b: Book) -> float:
        """Nunca tomamos más que una fracción de lo que hay. Barrer el book te delata."""
        avail = min(book_a.depth("ask"), book_b.depth("ask"))
        return min(avail * self.cfg.depth_fraction, self.cfg.max_order_shares)

    # ── métricas ─────────────────────────────────────────────────────────

    def summary(self) -> dict:
        total_paired = sum(i.paired for i in self._inv.values())
        total_edge = sum(i.locked_edge for i in self._inv.values())
        worst = max(
            (i for i in self._inv.values()),
            key=lambda i: abs(i.imbalance),
            default=None,
        )
        return {
            "markets": len(self._inv),
            "paired_shares": round(total_paired, 2),
            "locked_edge_usd": round(total_edge, 4),
            "worst_imbalance": round(worst.imbalance, 2) if worst else 0.0,
            "avg_combined_cost": round(
                sum(i.combined_cost for i in self._inv.values() if i.paired)
                / max(1, sum(1 for i in self._inv.values() if i.paired)),
                4,
            ),
        }
