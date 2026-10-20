"""BacktestBroker: fills strategy intents against the per-second Kalshi trade tape with her_trades.py's exact rules.

Tape format (``data/ktr/<TICKER>.json``, keys converted to int): ``{epoch_second: {"y": [min_px, qty, notional],
"n": [...]}}`` where "y" aggregates prints whose TAKER bought YES (price = what the taker paid) and "n" likewise for
NO; ``[9, 0, 0]`` = no prints of that side in that second.

Fill rules (s = the market second of the step that submitted the order, op = market open):
  market_buy   taker VWAP of our side over seconds op+s+1 .. op+s+3 (``buy_px``); taker fee; ts = op+s+1.
               Not filled (on_done "cancelled") when there are no prints or VWAP >= the intent's price cap.
  market_sell  first second in op+s+1 .. op+s+4 with prints on the OTHER side, at 1 - their VWAP (``sell_fast``);
               taker fee; ts = op+s+1 (her exit time u+1, whichever second actually printed).
  limit_buy    resting from the next second on; fills completely at its price (maker fee, ts = that second) when the
               tape trades THROUGH it: our side printed below the bid (min_px < L - 1e-9) or the other side's taker
               VWAP > 1 - L + 1e-9.
  limit_sell   (TP) resting from the next second on; fills completely at its price (maker fee) when our side's taker
               VWAP that second >= the price.
Per second, ``on_second(s)`` checks resting sells (TP) before resting bids, as her_trades.py does.

Orders never expire on their own here: the strategy cancels its entry bid at the expiry second (after that second's
fill check), which is the ordering her_trades.py uses. The broker is synchronous (backtest only): ``submit`` of a
market order reports the fill (or on_done) before it returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from kalshibot.fees import maker_fee, taker_fee
from kalshibot.types import Fill, MarketInfo, OrderIntent

Tape = dict[int, dict[str, list[float]]]


def other(side: str) -> str:
    return "n" if side == "y" else "y"


def buy_px(K: Tape, t: int, side: str) -> float | None:
    """her `buy_px`: VWAP paid by takers buying `side` in [t, t+2]."""
    q = n = 0
    for u in range(t, t + 3):
        a = K.get(u)
        if a and a[side][1] > 0:
            q += a[side][1]
            n += a[side][2]
    return n / q if q else None


def sell_fast(K: Tape, t: int, side: str) -> float | None:
    """her `sell_fast`: first second in [t, t+3] with opposite-side prints -> 1 - their VWAP."""
    o = other(side)
    for u in range(t, t + 4):
        a = K.get(u)
        if a and a[o][1] > 0:
            return 1 - a[o][2] / a[o][1]
    return None


def spread_est(K: Tape, t: int, side: str) -> float | None:
    """her `spread_est`: our side's lowest taker price + the other side's lowest taker price - 1, over t-3..t+1."""
    o = other(side)
    a1 = a2 = 9
    for u in range(t - 3, t + 2):
        a = K.get(u)
        if a:
            if a[side][1] > 0:
                a1 = min(a1, a[side][0])
            if a[o][1] > 0:
                a2 = min(a2, a[o][0])
    return None if a1 >= 9 or a2 >= 9 else a1 + a2 - 1


@dataclass
class _Resting:
    intent: OrderIntent
    placed_s: int


class BacktestBroker:
    def __init__(self, on_fill: Callable[[Fill], Any] | None = None,
                 on_done: Callable[[str, str], Any] | None = None):
        self._fill_cbs = [on_fill] if on_fill else []
        self._done_cbs = [on_done] if on_done else []
        self.market: MarketInfo | None = None
        self.tape: Tape = {}
        self.op = 0
        self.s = -1
        self.resting: dict[str, _Resting] = {}
        self.fills: list[Fill] = []

    def subscribe(self, on_fill=None, on_done=None) -> None:
        if on_fill:
            self._fill_cbs.append(on_fill)
        if on_done:
            self._done_cbs.append(on_done)

    def start_market(self, market: MarketInfo, tape: Tape) -> None:
        self.market, self.tape = market, tape
        self.op = int(market.open_ts)
        self.s = -1
        self.resting = {}
        self.fills = []

    # ------------------------------------------------------------------ callbacks
    def _fill(self, it: OrderIntent, price: float, ts: float, maker: bool) -> None:
        action = "buy" if it.kind in ("limit_buy", "market_buy") else "sell"
        f = Fill(client_id=it.client_id, side=it.side, action=action, price=price, qty=it.qty,
                 fee=(maker_fee if maker else taker_fee)(price, it.qty), ts=ts, maker=maker)
        self.fills.append(f)
        for cb in self._fill_cbs:
            cb(f)

    def _done(self, cid: str, status: str) -> None:
        for cb in self._done_cbs:
            cb(cid, status)

    # ------------------------------------------------------------------ per second
    def on_second(self, s: int) -> None:
        """Fill resting orders against the tape of market second s (TP sells first, then entry bids)."""
        self.s = s
        if not self.resting:
            return
        a = self.tape.get(self.op + s)
        if not a:
            return
        ts = self.op + s
        for kind in ("limit_sell", "limit_buy"):
            for cid, r in list(self.resting.items()):
                it = r.intent
                if it.kind != kind or r.placed_s >= s or cid not in self.resting:
                    continue
                sd, o = it.side, other(it.side)
                if kind == "limit_sell":
                    hit = a[sd][1] > 0 and a[sd][2] / a[sd][1] >= it.price
                else:
                    L = it.price
                    hit = (a[sd][1] > 0 and a[sd][0] < L - 1e-9) or (a[o][1] > 0 and a[o][2] / a[o][1] > 1 - L + 1e-9)
                if hit:
                    del self.resting[cid]
                    self._fill(it, it.price, ts, True)

    # ------------------------------------------------------------------ orders
    def submit(self, intent: OrderIntent, market: MarketInfo | None = None) -> None:
        it, s, K = intent, self.s, self.tape
        if it.kind == "cancel":
            self.cancel(it.client_id)
        elif it.kind == "market_buy":
            e = buy_px(K, self.op + s + 1, it.side)
            if e is None or (it.price is not None and e >= it.price):
                self._done(it.client_id, "cancelled")
            else:
                self._fill(it, e, self.op + s + 1, False)
        elif it.kind == "market_sell":
            b = sell_fast(K, self.op + s + 1, it.side)
            if b is None or (it.price is not None and b < it.price - 1e-9):
                self._done(it.client_id, "cancelled")
            else:
                self._fill(it, b, self.op + s + 1, False)
        elif it.kind in ("limit_buy", "limit_sell"):
            self.resting[it.client_id] = _Resting(it, s)
        else:
            raise ValueError(f"unknown intent kind {it.kind}")

    def cancel(self, client_id: str | None) -> bool:
        r = self.resting.pop(client_id, None) if client_id else None
        if r is None:
            return False
        self._done(client_id, "cancelled")
        return True

    def cancel_all(self) -> int:
        ids = list(self.resting)
        for cid in ids:
            self.cancel(cid)
        return len(ids)
