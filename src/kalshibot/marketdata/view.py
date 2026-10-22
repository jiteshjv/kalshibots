"""SecondView builder: live Book + that second's Kalshi prints + latest BtcTick -> SecondView (once per second).

Timing (mirrors scripts/her_trades.py, which decides at second u using the prints of second u and fills from u+1):
call ``second(market, now)`` right after the wall clock crosses a whole second S. The view describes the second that
just COMPLETED, [S-1, S): ``ts = S-1``, ``s = int(S-1 - open_ts)``, ``trades`` = prints with S-1 <= ts < S. The book
is the latest live book (as of `now`) and btc the latest BtcTick.

mark (her_trades.py `lastp`): with yes_q/yes_n = qty/notional of prints whose taker bought YES (at the YES price) and
no_q/no_n likewise for NO (at the NO price),

    mark_y = (yes_n + (no_q - no_n)) / (yes_q + no_q)        mark_n = 1 - mark_y

i.e. the qty-weighted YES-implied price of every print in the second. Seconds without prints carry the previous mark
forward (None until the market's first print).
"""
from __future__ import annotations

import math
from typing import Iterable, Protocol

from ..types import Book, BtcTick, KTrade, MarketInfo, SecondView


class BookSource(Protocol):
    def book(self, ticker: str) -> Book | None: ...
    def trades(self, ticker: str, t0: float, t1: float) -> list[KTrade]: ...


class BtcSource(Protocol):
    def latest(self) -> BtcTick | None: ...


def yes_mark(trades: Iterable[KTrade]) -> float | None:
    """Qty-weighted YES-implied price of a set of prints (None if there are none)."""
    yq = yn = nq = nn = 0.0
    for t in trades:
        if t.taker_side == "y":
            yq += t.qty
            yn += t.qty * t.price
        else:
            nq += t.qty
            nn += t.qty * t.price
    q = yq + nq
    if q <= 0:
        return None
    return (yn + (nq - nn)) / q


class ViewBuilder:
    def __init__(self, feed: BookSource, btc: BtcSource | None):
        self.feed, self.btc = feed, btc
        self._mark: dict[str, float] = {}       # ticker -> last YES mark

    def reset(self, ticker: str) -> None:
        self._mark.pop(ticker, None)

    def second(self, market: MarketInfo, now: float) -> SecondView:
        sec = math.floor(now) - 1
        trades = self.feed.trades(market.ticker, sec, sec + 1)
        book = self.feed.book(market.ticker)
        return self.build(market, sec, book, trades, self.btc.latest() if self.btc is not None else None)

    def partial(self, market: MarketInfo, now: float) -> SecondView:
        """The current, unfinished second [floor(now), now): its prints so far, the live book and latest BTC. Does not
        touch the carried mark (second() owns that), so a fast check between seconds changes nothing for step()."""
        sec = math.floor(now)
        trades = self.feed.trades(market.ticker, sec, now)
        book = self.feed.book(market.ticker)
        m = yes_mark(trades)
        mark = {"y": None, "n": None} if m is None else {"y": round(m, 6), "n": round(1.0 - m, 6)}
        ask = {"y": book.best_ask("y") if book else None, "n": book.best_ask("n") if book else None}
        bid = {"y": book.best_bid("y") if book else None, "n": book.best_bid("n") if book else None}
        return SecondView(ts=float(sec), s=int(sec - market.open_ts), btc=self.btc.latest() if self.btc is not None else None,
                          book=book, ask=ask, bid=bid, mark=mark, trades=list(trades))

    def build(self, market: MarketInfo, sec: float, book: Book | None, trades: list[KTrade],
              btc: BtcTick | None) -> SecondView:
        """Pure part of second(): usable by a replayer with recorded books/trades."""
        m = yes_mark(trades)
        if m is not None:
            self._mark[market.ticker] = m
        else:
            m = self._mark.get(market.ticker)
        mark = {"y": None, "n": None} if m is None else {"y": round(m, 6), "n": round(1.0 - m, 6)}
        ask = {"y": book.best_ask("y") if book else None, "n": book.best_ask("n") if book else None}
        bid = {"y": book.best_bid("y") if book else None, "n": book.best_bid("n") if book else None}
        return SecondView(ts=float(sec), s=int(sec - market.open_ts), btc=btc, book=book, ask=ask, bid=bid,
                          mark=mark, trades=list(trades))
