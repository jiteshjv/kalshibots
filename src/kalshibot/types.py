"""Shared data types for kalshibot. Every module imports these; keep them small and stable.

Conventions (used everywhere):
  * side: "y" (YES / Up) or "n" (NO / Down). Prices are in dollars (0.0-1.0), floats, 0.001 ticks.
  * time: UTC epoch seconds (float). A market's second index s = int(ts - market.open_ts), 0..899.
  * Kalshi books hold BIDS only: the ask to BUY side S at price p is a bid on the other side at 1-p.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["y", "n"]


def other(side: Side) -> Side:
    return "n" if side == "y" else "y"


@dataclass(frozen=True)
class MarketInfo:
    ticker: str              # e.g. KXBTC15M-26OCT191345-45
    open_ts: float
    close_ts: float          # open_ts + 900
    strike: float            # Kalshi floor_strike (official start value)
    exchange_index: int = 2  # Kalshi crypto shard


@dataclass
class Book:
    """Full Kalshi order book for one market at one moment. Levels are (price, qty), best first."""
    ts: float
    yes_bids: list[tuple[float, float]] = field(default_factory=list)   # descending price
    no_bids: list[tuple[float, float]] = field(default_factory=list)    # descending price

    def bids(self, side: Side) -> list[tuple[float, float]]:
        """Bids to SELL `side` into (descending)."""
        return self.yes_bids if side == "y" else self.no_bids

    def asks(self, side: Side) -> list[tuple[float, float]]:
        """Offers to BUY `side` from (ascending): the other side's bids at 1-p."""
        return [(round(1.0 - p, 4), q) for p, q in self.bids(other(side))]

    def best_bid(self, side: Side) -> float | None:
        b = self.bids(side)
        return b[0][0] if b else None

    def best_ask(self, side: Side) -> float | None:
        a = self.asks(side)
        return a[0][0] if a else None

    def spread(self, side: Side) -> float | None:
        a, b = self.best_ask(side), self.best_bid(side)
        return None if a is None or b is None else round(a - b, 4)


@dataclass(frozen=True)
class KTrade:
    """One Kalshi trade. price = what the TAKER paid for taker_side (so the other side traded at 1-price)."""
    ts: float
    taker_side: Side
    price: float
    qty: float


@dataclass(frozen=True)
class BtcTick:
    """Best BTC estimate at ts. price = BRTI when fresh, else the exchange composite (see marketdata)."""
    ts: float
    price: float
    brti: float | None = None
    composite: float | None = None
    source: str = "composite"            # "brti" | "composite"
    stale: bool = False                  # True -> strategy must not open new entries


@dataclass
class SecondView:
    """Everything the strategy sees at one second of a market. Built by marketdata (live) or the backtest replayer.

    ask/bid are per side and *executable* prices for small size; mark is the price of our side implied by the
    most recent trades (used for stop triggers, as in the backtest)."""
    ts: float
    s: int                                   # seconds since market open
    btc: BtcTick | None
    book: Book | None                        # live: full book; backtest: None
    ask: dict[str, float | None]             # {"y": .., "n": ..} price to buy now
    bid: dict[str, float | None]             # {"y": .., "n": ..} price to sell now
    mark: dict[str, float | None]            # {"y": .., "n": ..} last-trade-implied price
    trades: list[KTrade] = field(default_factory=list)   # trades printed in this second


OrderKind = Literal["limit_buy", "market_buy", "limit_sell", "market_sell", "cancel"]


@dataclass
class OrderIntent:
    """What the strategy wants. Brokers turn intents into orders and report Fills back."""
    kind: OrderKind
    side: Side
    qty: int
    price: float | None = None               # limit price; for market_* the worst acceptable price (cap/floor)
    lot_id: str | None = None                # entry lot this belongs to (stops/exits reference lots)
    client_id: str | None = None             # set by the strategy; unique per intent
    expires_s: int | None = None             # for limit_buy: cancel after this many seconds (strategy may then go market)
    reason: str = ""                         # "entry", "entry_market_fallback", "stop", "tp", "close_cancel"


@dataclass(frozen=True)
class Fill:
    client_id: str
    side: Side
    action: Literal["buy", "sell"]
    price: float
    qty: int
    fee: float                               # dollars for this fill
    ts: float
    maker: bool


@dataclass
class Lot:
    """One entry (e.g. 100 or 200 contracts) with its own stop."""
    lot_id: str
    ticker: str
    side: Side
    qty: int
    buy: float
    fee_in: float
    stop: float                              # price trigger = buy * (1 - stop_pct)
    entry_ts: float
    how: str = "limit"                       # "limit" | "market"
    exit: str | None = None                  # "tp" | "stop" | "settle_win" | "settle_loss"
    exit_px: float | None = None
    exit_ts: float | None = None
    fee_out: float = 0.0

    @property
    def open(self) -> bool:
        return self.exit is None

    def pnl(self) -> float | None:
        if self.exit_px is None:
            return None
        return self.qty * self.exit_px - self.fee_out - (self.qty * self.buy + self.fee_in)
