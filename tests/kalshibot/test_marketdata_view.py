"""ViewBuilder: mark = her_trades.py `lastp` formula, carry-forward, timing, ask/bid from the book."""
import json
from pathlib import Path

import pytest

from kalshibot.marketdata.view import ViewBuilder, yes_mark
from kalshibot.types import Book, BtcTick, KTrade, MarketInfo

ROOT = Path(__file__).resolve().parents[2]
M = MarketInfo("KXBTC15M-TEST", open_ts=1000.0, close_ts=1900.0, strike=100000.0)


def her_lastp(a, side):
    """Verbatim her_trades.py: a = {"y": [minpx, qty, notional], "n": [...]} for one second."""
    o = "n" if side == "y" else "y"
    q = a[side][1] + a[o][1]
    return (a[side][2] + (a[o][1] - a[o][2])) / q if q > 0 else None


def agg(trades):
    a = {"y": [9, 0, 0], "n": [9, 0, 0]}
    for t in trades:
        x = a[t.taker_side]
        x[0] = min(x[0], t.price)
        x[1] += t.qty
        x[2] += t.qty * t.price
    return a


class Feed:
    def __init__(self, book=None, trades=()):
        self.b, self.t = book, list(trades)

    def book(self, ticker):
        return self.b

    def trades(self, ticker, t0, t1):
        return [t for t in self.t if t0 <= t.ts < t1]


class Btc:
    def __init__(self, tick):
        self.tick = tick

    def latest(self):
        return self.tick


def test_yes_mark_matches_her_formula():
    tr = [KTrade(1.1, "y", 0.93, 100), KTrade(1.2, "n", 0.08, 50), KTrade(1.3, "y", 0.94, 10)]
    a = agg(tr)
    m = yes_mark(tr)
    assert m == pytest.approx(her_lastp(a, "y"))
    assert 1 - m == pytest.approx(her_lastp(a, "n"))
    assert m == pytest.approx((93 + 9.4 + (50 - 4)) / 160)
    assert yes_mark([]) is None


def test_yes_mark_on_research_tape():
    """Same numbers as her_trades.py on a real recorded market (skipped if the research data is absent)."""
    files = [f for f in sorted((ROOT / "data" / "ktr").glob("*.json"))[-400:] if f.stat().st_size > 1000][:20] \
        if (ROOT / "data" / "ktr").exists() else []
    if not files:
        pytest.skip("no research tape")
    n = 0
    for f in files:
        for sec, a in json.loads(f.read_text()).items():
            # rebuild one synthetic print per side with the second's VWAP: identical qty/notional sums
            tr = [KTrade(float(sec), s, a[s][2] / a[s][1], a[s][1]) for s in ("y", "n") if a[s][1] > 0]
            if not tr:
                continue
            assert yes_mark(tr) == pytest.approx(her_lastp(a, "y"), abs=1e-9)
            assert 1 - yes_mark(tr) == pytest.approx(her_lastp(a, "n"), abs=1e-9)
            n += 1
    assert n > 0


def test_view_timing_book_and_carry_forward():
    book = Book(ts=1130.2, yes_bids=[(0.92, 100), (0.91, 50)], no_bids=[(0.06, 30)])
    tr = [KTrade(1129.0, "y", 0.93, 10), KTrade(1129.9, "n", 0.07, 10), KTrade(1130.1, "y", 0.99, 5)]
    tick = BtcTick(ts=1130.0, price=100150.0)
    vb = ViewBuilder(Feed(book, tr), Btc(tick))
    v = vb.second(M, 1130.05)                        # just after the clock hit 1130 -> second [1129, 1130)
    assert v.ts == 1129.0 and v.s == 129
    assert [t.ts for t in v.trades] == [1129.0, 1129.9]
    assert v.mark["y"] == pytest.approx((9.3 + (10 - 0.7)) / 20) and v.mark["n"] == pytest.approx(1 - v.mark["y"])
    assert v.ask == {"y": 0.94, "n": 0.08} and v.bid == {"y": 0.92, "n": 0.06}
    assert v.btc is tick and v.book is book
    v2 = vb.second(M, 1131.0)                        # [1130, 1131): one YES print at 99c
    assert v2.mark["y"] == pytest.approx(0.99)
    v3 = vb.second(M, 1135.0)                        # no prints -> carried forward
    assert v3.trades == [] and v3.mark["y"] == pytest.approx(0.99) and v3.s == 134


def test_view_without_book_or_prints():
    vb = ViewBuilder(Feed(None, []), None)
    v = vb.second(M, 1001.5)
    assert v.s == 0 and v.book is None and v.btc is None
    assert v.ask == {"y": None, "n": None} and v.mark == {"y": None, "n": None}
