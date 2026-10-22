"""Broker protocol + shared order-state tracking used by PaperBroker and LiveBroker.

Order lifecycle (OrderState.status):
    pending  -> accepted by the broker, not yet working (paper: waits for the next second; live: awaiting the
                exchange's reply)
    resting  -> working on the book (limit orders)
    filled   -> remaining == 0                                      (terminal)
    cancelled / expired / rejected                                  (terminal; reported via on_done)

Callbacks:
    on_fill(Fill)                 -- once per (partial) execution. Fill.client_id is the strategy's client_id.
    on_done(client_id, status)    -- once per order when it ends WITHOUT being completely filled:
                                     "cancelled" (incl. the unfilled remainder of an IOC/market order),
                                     "expired" or "rejected". A complete fill does not call on_done unless the broker
                                     was built with done_on_fill=True (then status "filled").
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from kalshibot.types import Fill, MarketInfo, OrderIntent, SecondView, Side

try:  # prefer the strategy agent's fees module; identical private fallback otherwise
    from kalshibot import fees as _fees  # type: ignore
except Exception:  # pragma: no cover - depends on other agents' progress
    _fees = None

TAKER_RATE = 0.07
MAKER_RATE = 0.0175
EPS = 1e-9

ACTIVE = ("pending", "resting")
TERMINAL = ("filled", "cancelled", "expired", "rejected")

FillCb = Callable[[Fill], Any]
DoneCb = Callable[[str, str], Any]


# ----------------------------------------------------------------------------------------------------------- fees
def _fee(rate: float, levels: Iterable[tuple[float, float]]) -> float:
    """Kalshi fee for ONE order executed at the given (price, qty) levels: ceil(rate * sum(n p (1-p)) * 100) / 100."""
    x = sum(q * p * (1.0 - p) for p, q in levels)
    if x <= 0:
        return 0.0
    return math.ceil(rate * x * 100.0 - EPS) / 100.0


def taker_fee(price: float, n: float) -> float:
    if _fees is not None and hasattr(_fees, "taker_fee"):
        return float(_fees.taker_fee(price, n))
    return _fee(TAKER_RATE, [(price, n)])


def maker_fee(price: float, n: float) -> float:
    if _fees is not None and hasattr(_fees, "maker_fee"):
        return float(_fees.maker_fee(price, n))
    return _fee(MAKER_RATE, [(price, n)])


def order_fee(levels: list[tuple[float, float]], maker: bool) -> float:
    """Fee for one fill. A single price level goes through fees.py; a book walk uses the exact per-order formula
    over all levels (one ceil per order, as Kalshi charges)."""
    levels = [(p, q) for p, q in levels if q > 0]
    if not levels:
        return 0.0
    if len(levels) == 1:
        p, q = levels[0]
        return maker_fee(p, q) if maker else taker_fee(p, q)
    return _fee(MAKER_RATE if maker else TAKER_RATE, levels)


# ----------------------------------------------------------------------------------------------------- order state
def action_of(kind: str) -> str:
    return "buy" if kind in ("limit_buy", "market_buy") else "sell"


@dataclass
class OrderState:
    client_id: str
    ticker: str
    kind: str                       # limit_buy | market_buy | limit_sell | market_sell
    side: Side
    qty: float                      # contracts (Kalshi trades fractions: 2 decimals)
    price: float | None             # limit price, or cap (market_buy) / floor (market_sell)
    lot_id: str | None = None
    reason: str = ""
    status: str = "pending"
    filled: float = 0
    notional: float = 0.0           # sum(price * qty) over fills
    fees: float = 0.0
    placed_ts: float | None = None
    expires_ts: float | None = None
    exchange_id: str | None = None  # live only
    tif: str = "gtc"                # "gtc" | "ioc"
    post_only: bool = False
    reduce_only: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def action(self) -> str:
        return action_of(self.kind)

    @property
    def remaining(self) -> float:
        return max(round(self.qty - self.filled, 2), 0)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    @property
    def avg_price(self) -> float | None:
        return self.notional / self.filled if self.filled else None

    def to_dict(self) -> dict:
        return dict(client_id=self.client_id, ticker=self.ticker, kind=self.kind, side=self.side, qty=self.qty,
                    price=self.price, lot_id=self.lot_id, reason=self.reason, status=self.status, filled=self.filled,
                    remaining=self.remaining, avg_price=self.avg_price, fees=round(self.fees, 4),
                    placed_ts=self.placed_ts, expires_ts=self.expires_ts, exchange_id=self.exchange_id, tif=self.tif,
                    post_only=self.post_only, reduce_only=self.reduce_only)


# --------------------------------------------------------------------------------------------------------- protocol
@runtime_checkable
class Broker(Protocol):
    async def submit(self, intent: OrderIntent, market: MarketInfo | str) -> None: ...
    async def cancel(self, client_id: str) -> bool: ...
    async def cancel_all(self, ticker: str | None = None) -> int: ...
    def on_second(self, view: SecondView, ticker: str | None = None) -> None: ...
    async def reconcile(self, market: MarketInfo | str) -> list[dict]: ...
    def subscribe(self, on_fill: FillCb | None = None, on_done: DoneCb | None = None) -> None: ...
    def open_orders(self, ticker: str | None = None) -> list[OrderState]: ...


def ticker_of(market: MarketInfo | str | None) -> str | None:
    if market is None or isinstance(market, str):
        return market
    return market.ticker


class BrokerBase:
    """Order tracking (client_id -> OrderState), positions (ticker, side) -> contracts, fill log, callbacks."""

    def __init__(self, on_fill: FillCb | None = None, on_done: DoneCb | None = None, *, done_on_fill: bool = False,
                 id_prefix: str = "kbx"):
        self._fill_cbs: list[FillCb] = []
        self._done_cbs: list[DoneCb] = []
        self.subscribe(on_fill, on_done)
        self.done_on_fill = done_on_fill
        self.orders: dict[str, OrderState] = {}       # insertion order = placement order
        self.positions: dict[tuple[str, str], int] = {}
        self.fills: list[Fill] = []
        self.cash: float = 0.0                         # -buys - fees + sells (settlement not included)
        self._ids = itertools.count(1)
        self.id_prefix = id_prefix

    # callbacks ------------------------------------------------------------------------------------------------
    def subscribe(self, on_fill: FillCb | None = None, on_done: DoneCb | None = None) -> None:
        if on_fill is not None:
            self._fill_cbs.append(on_fill)
        if on_done is not None:
            self._done_cbs.append(on_done)

    # queries --------------------------------------------------------------------------------------------------
    def get(self, client_id: str) -> OrderState | None:
        return self.orders.get(client_id)

    def open_orders(self, ticker: str | None = None) -> list[OrderState]:
        return [o for o in self.orders.values() if o.active and (ticker is None or o.ticker == ticker)]

    def open_order_count(self, ticker: str | None = None) -> int:
        return len(self.open_orders(ticker))

    def position(self, ticker: str, side: Side) -> int:
        return self.positions.get((ticker, side), 0)

    async def cancel_entries(self) -> int:
        """Shutdown: cancel resting BUY orders only (they could add to a position nobody watches). Take-profit sells
        stay on the exchange: they can only reduce a position, at the take-profit price."""
        n = 0
        for o in self.open_orders():
            if o.action == "buy" and o.tif != "ioc" and await self.cancel(o.client_id):   # an IOC ends by itself
                n += 1
        return n

    def restore_position(self, ticker: str, side: Side, qty: int) -> None:
        """Restart: the contracts already held (so sells are allowed)."""
        self.positions[(ticker, side)] = round(qty, 2)

    # helpers --------------------------------------------------------------------------------------------------
    def _new_id(self) -> str:
        return f"{self.id_prefix}-{next(self._ids)}"

    def _make_state(self, intent: OrderIntent, ticker: str, ts: float | None) -> OrderState:
        cid = intent.client_id or self._new_id()
        st = OrderState(client_id=cid, ticker=ticker, kind=intent.kind, side=intent.side, qty=round(intent.qty, 2),
                        price=intent.price, lot_id=intent.lot_id, reason=intent.reason, placed_ts=ts)
        st.tif = "ioc" if intent.kind in ("market_buy", "market_sell") else "gtc"
        st.post_only = intent.kind == "limit_buy"
        st.reduce_only = intent.kind in ("limit_sell", "market_sell")
        if intent.kind == "limit_buy" and intent.expires_s is not None and ts is not None:
            st.expires_ts = ts + intent.expires_s
        return st

    def _apply_fill(self, st: OrderState, price: float, qty: int, fee: float, ts: float, maker: bool,
                    *, force: bool = False) -> Fill | None:
        """Record an execution on st and notify. force=True applies it even if st is terminal (live reality)."""
        if qty <= 0 or (not force and not st.active):
            return None
        qty = round(qty, 2)
        st.filled = round(st.filled + qty, 2)
        st.notional += price * qty
        st.fees += fee
        key = (st.ticker, st.side)
        if st.action == "buy":
            self.positions[key] = round(self.positions.get(key, 0) + qty, 2)
            self.cash -= price * qty + fee
        else:
            self.positions[key] = round(self.positions.get(key, 0) - qty, 2)
            self.cash += price * qty - fee
        f = Fill(client_id=st.client_id, side=st.side, action=st.action, price=round(price, 6), qty=qty,
                 fee=round(fee, 4), ts=ts, maker=maker)
        completed = st.active and st.remaining == 0
        if completed:
            st.status = "filled"
        self.fills.append(f)
        for cb in self._fill_cbs:
            cb(f)
        if completed and self.done_on_fill:
            for cb in self._done_cbs:
                cb(st.client_id, "filled")
        return f

    def _finish(self, st: OrderState, status: str) -> bool:
        """Terminate an active order with a non-fill status and notify once. Returns False if already terminal."""
        if not st.active:
            return False
        if status == "cancelled" and st.remaining == 0:
            st.status = "filled"
            return False
        st.status = status
        for cb in self._done_cbs:
            cb(st.client_id, status)
        return True

    def _targets_for_cancel(self, intent: OrderIntent) -> list[OrderState]:
        """A `cancel` intent names the order to cancel by client_id (the strategy's convention). If that order is
        known it is the only target (no-op when it already finished — e.g. filled the same second). Only when the
        client_id is missing or unknown to this broker does lot_id select every active order of that lot."""
        st = self.orders.get(intent.client_id) if intent.client_id else None
        if st is not None:
            return [st] if st.active else []
        if intent.lot_id:
            return [o for o in self.orders.values() if o.active and o.lot_id == intent.lot_id]
        return []
