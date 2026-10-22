"""PaperBroker: simulated execution against the LIVE Kalshi book and that second's trades. Deterministic, no I/O.

Call order per market second (the app does this):  broker.on_second(view)  ->  strategy.step(view)  ->
risk.allow / broker.submit(...) for each intent.  So an order submitted after view s is first worked in on_second(s+1)
(~1 s latency, like her_trades.py's "s+1" rule).

Fill model (all prices are for OUR side, dollars):
  market_buy   (IOC, cap = intent.price)   next second: walk the asks (= other side's bids at 1-p) up to the cap;
               partial fills ok; the unfilled remainder is cancelled (on_done "cancelled"). Taker fee.
  market_sell  (IOC, floor = intent.price, default 0.01; reduce-only) next second: walk our side's bids down to the
               floor; partial ok; remainder cancelled. Taker fee.
  limit_buy    (post-only, rests at L): rejected at submit if the last seen ask is already <= L. Fills ONLY when a
               trade prints THROUGH L (back of queue): a taker of our side paying < L, or a taker of the other side
               paying > 1-L. Filled at L, maker fee. Broker-side expiry backstop at placed_ts + expires_s +
               expiry_grace_s (the strategy is expected to cancel at expiry itself and then go to market).
  limit_sell   (TP, reduce-only, rests at P): fills when a taker of our side pays >= P, at P, maker fee. If it is
               already marketable when it first works (our best bid >= P) it executes as a taker against the bids.
Quantity for resting fills: resting_fill="full" (default, = backtest: the whole remainder fills when the condition
is met) or "volume" (at most the qualifying trades' contracts, each trade consumed once across our orders).
Book liquidity consumed by our market orders within one second is not reused by another of our orders that second.
Sells are reduce-only against the paper position (never go short).
"""
from __future__ import annotations

from kalshibot.types import Book, KTrade, MarketInfo, OrderIntent, SecondView, Side, other

from .base import EPS, BrokerBase, DoneCb, FillCb, OrderState, order_fee, ticker_of

DEFAULT_FLOOR = 0.01
DEFAULT_CAP = 0.99


class PaperBroker(BrokerBase):
    def __init__(self, on_fill: FillCb | None = None, on_done: DoneCb | None = None, *,
                 resting_fill: str = "full", expiry_grace_s: float = 1.0, market_delay: bool = True,
                 post_only_check: bool = True, done_on_fill: bool = False, id_prefix: str = "pp"):
        super().__init__(on_fill, on_done, done_on_fill=done_on_fill, id_prefix=id_prefix)
        if resting_fill not in ("full", "volume"):
            raise ValueError("resting_fill must be 'full' or 'volume'")
        self.resting_fill = resting_fill
        self.expiry_grace_s = expiry_grace_s
        self.market_delay = market_delay
        self.post_only_check = post_only_check
        self._view: dict[str | None, SecondView] = {}
        self._used: dict[tuple, float] = {}           # book liquidity consumed this second
        self._used_ts: float | None = None

    # ------------------------------------------------------------------------------------------------ Broker API
    async def submit(self, intent: OrderIntent, market: MarketInfo | str) -> None:
        self.submit_now(intent, market)

    async def cancel(self, client_id: str) -> bool:
        st = self.orders.get(client_id)
        return bool(st) and self._finish(st, "cancelled")

    async def cancel_all(self, ticker: str | None = None) -> int:
        return self.cancel_all_now(ticker)

    async def reconcile(self, market: MarketInfo | str) -> list[dict]:
        return []                                       # paper state lives in memory / the strategy's saved state

    # sync variants (handy for tests and the backtest-style loop)
    def cancel_all_now(self, ticker: str | None = None) -> int:
        return sum(self._finish(o, "cancelled") for o in self.open_orders(ticker))

    def submit_now(self, intent: OrderIntent, market: MarketInfo | str) -> OrderState | None:
        ticker = ticker_of(market) or ""
        if intent.kind == "cancel":
            for st in self._targets_for_cancel(intent):
                self._finish(st, "cancelled")
            return None
        if intent.client_id and intent.client_id in self.orders:
            return self.orders[intent.client_id]        # idempotent: same client_id is never placed twice
        view = self._last_view(ticker)
        st = self._make_state(intent, ticker, view.ts if view else None)
        self.orders[st.client_id] = st
        if st.qty <= 0 or (st.kind in ("limit_buy", "limit_sell") and st.price is None):
            self._finish(st, "rejected")
            return st
        if st.kind == "limit_buy" and self.post_only_check and view is not None:
            ask = _best_ask(view, st.side)
            if ask is not None and ask <= st.price + EPS:
                st.meta["reject_reason"] = "post_only_cross"
                self._finish(st, "rejected")
                return st
        if st.kind in ("market_buy", "market_sell") and not self.market_delay and view is not None:
            self._execute_market(st, view)
        return st

    # ------------------------------------------------------------------------------------------------ simulation
    def on_second(self, view: SecondView, ticker: str | None = None) -> None:
        """Work every active order of `ticker` (all orders when None) against this second's book and trades."""
        self._view[ticker] = view
        if ticker is not None:
            self._view[None] = view
        self._reset_used(view.ts)
        orders = [o for o in self.open_orders(ticker)]
        # 1. market orders submitted since the last second
        for st in orders:
            if st.kind in ("market_buy", "market_sell") and st.active:
                self._execute_market(st, view)
        # 2. newly placed limit orders start working; a TP that is already marketable executes as a taker
        for st in orders:
            if st.kind in ("limit_buy", "limit_sell") and st.status == "pending":
                st.status = "resting"
                if st.placed_ts is None:
                    st.placed_ts = view.ts
                if st.kind == "limit_sell" and view.book is not None:
                    self._take(st, view, limit=st.price)
        # 3. resting orders vs this second's prints (back of queue)
        trade_left = [t.qty for t in view.trades]
        for st in orders:
            if st.status == "resting":
                self._fill_from_trades(st, view, trade_left)
        # 4. expiry backstop for entry bids
        for st in orders:
            if (st.status == "resting" and st.kind == "limit_buy" and st.expires_ts is not None
                    and view.ts >= st.expires_ts + self.expiry_grace_s - EPS):
                self._finish(st, "expired")

    def work_market_now(self, view: SecondView, ticker: str | None = None) -> None:
        """Fast stops between seconds: execute pending market orders of `ticker` against this (partial-second) view's
        live book now instead of at the next on_second."""
        for st in self.open_orders(ticker):
            if st.kind in ("market_buy", "market_sell") and st.active:
                self._execute_market(st, view)

    # ------------------------------------------------------------------------------------------------ internals
    def _last_view(self, ticker: str | None) -> SecondView | None:
        return self._view.get(ticker) or self._view.get(None)

    def _reset_used(self, ts: float) -> None:
        if self._used_ts != ts:
            self._used = {}
            self._used_ts = ts

    def _execute_market(self, st: OrderState, view: SecondView) -> None:
        self._reset_used(view.ts)
        if st.kind == "market_buy":
            limit = st.price if st.price is not None else DEFAULT_CAP
        else:
            limit = st.price if st.price is not None else DEFAULT_FLOOR
        self._take(st, view, limit=limit)
        if st.active:
            self._finish(st, "cancelled")               # IOC: unfilled remainder dies

    def _sell_capacity(self, st: OrderState) -> int:
        return max(min(st.remaining, self.position(st.ticker, st.side)), 0)

    def _take(self, st: OrderState, view: SecondView, limit: float) -> None:
        """Execute st as a taker against the book (or the view's quote when there is no book)."""
        want = st.remaining if st.action == "buy" else self._sell_capacity(st)
        if want <= 0:
            return
        levels: list[tuple[float, float]] = []
        if view.book is None:
            px = (view.ask if st.action == "buy" else view.bid).get(st.side)
            if px is not None and (px <= limit + EPS if st.action == "buy" else px >= limit - EPS):
                levels = [(px, want)]
        else:
            levels = self._walk(view.book, st.side, st.action, want, limit)
        qty = int(sum(q for _, q in levels))
        if qty <= 0:
            return
        vwap = sum(p * q for p, q in levels) / qty
        self._apply_fill(st, vwap, qty, order_fee(levels, maker=False), view.ts, maker=False)

    def _walk(self, book: Book, side: Side, action: str, want: int, limit: float) -> list[tuple[float, float]]:
        # Every level is ultimately a bid on the YES-or-NO book: buying `side` at a lifts other(side)'s bid at 1-a;
        # selling `side` at b hits side's bid at b. Consumption is keyed by that underlying bid.
        out: list[tuple[float, float]] = []
        left = want
        if action == "buy":
            for a, q in book.asks(side):
                if a > limit + EPS or left <= 0:
                    break
                key = (other(side), round(1.0 - a, 4))
                avail = int(q - self._used.get(key, 0))
                take = min(left, avail)
                if take > 0:
                    out.append((a, take))
                    self._used[key] = self._used.get(key, 0) + take
                    left -= take
        else:
            for b, q in book.bids(side):
                if b < limit - EPS or left <= 0:
                    break
                key = (side, round(b, 4))
                avail = int(q - self._used.get(key, 0))
                take = min(left, avail)
                if take > 0:
                    out.append((b, take))
                    self._used[key] = self._used.get(key, 0) + take
                    left -= take
        return out

    @staticmethod
    def _qualifies(st: OrderState, t: KTrade) -> bool:
        p = st.price
        if st.kind == "limit_buy":
            return ((t.taker_side == st.side and t.price < p - EPS)
                    or (t.taker_side == other(st.side) and t.price > 1.0 - p + EPS))
        return t.taker_side == st.side and t.price >= p - EPS     # limit_sell

    def _fill_from_trades(self, st: OrderState, view: SecondView, trade_left: list[float]) -> None:
        idx = [i for i, t in enumerate(view.trades) if self._qualifies(st, t)]
        if not idx:
            return
        want = st.remaining if st.action == "buy" else self._sell_capacity(st)
        if want <= 0:
            return
        if self.resting_fill == "full":
            qty = want
        else:
            qty = 0
            for i in idx:
                take = int(min(want - qty, trade_left[i]))
                if take > 0:
                    trade_left[i] -= take
                    qty += take
                if qty >= want:
                    break
        if qty > 0:
            self._apply_fill(st, st.price, qty, order_fee([(st.price, qty)], maker=True), view.ts, maker=True)


def _best_ask(view: SecondView, side: Side) -> float | None:
    if view.book is not None:
        return view.book.best_ask(side)
    return view.ask.get(side) if view.ask else None
