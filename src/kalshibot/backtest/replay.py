"""Replay historical KXBTC15M markets through Strategy + BacktestBroker (the research data in ``data/``).

Inputs (read-only):
  data/settled.json      official markets (ticker, close_time, floor_strike, result)
  data/composite_1s.npy  per-second BTC composite (BRTI proxy), index 0 = T0
  data/prices_1s.npz     only its ``T0`` member is read
  data/ktr/<TICKER>.json per-second trade tape, loaded lazily per market

Market set = her_trades.py's ``W`` (scripts/friend_v3.py): result yes/no, tape file exists, close date >= the cut
(2026-10-15 minus ``days``), and a BTC price for every second from open-60 to close-1. The 2h-range and strike
filters are applied by the strategy (``start_market``) / here (no floor_strike -> not traded).

SecondView built for second s (op = open):
  btc   = composite[op+s]
  ask   = her ``buy_px(op+s+1)`` per side (the price a market buy decided at s gets)
  bid   = her ``sell_fast(op+s+1)`` per side (the price a stop decided at s gets)
  mark  = her ``lastp`` formula from this second's prints (carried forward when none); trades = one KTrade per side
          that printed (price = that side's taker VWAP)
  book  = None, EXCEPT when her ``spread_est(op+s+1)`` exists: then a one-level ``TapeBook`` whose ``spread()``
          returns exactly that estimate, so the strategy's "spread check only when a book exists" reproduces her
          MAXSPREAD rule (she does apply it in the backtest).
P&L per lot uses her rounding: cost = round(n*buy + fee_in, 2); pnl = round(n*exit - fee_out - cost, 2).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from kalshibot.backtest.broker import BacktestBroker, Tape, buy_px, sell_fast, spread_est
from kalshibot.config import StrategyCfg
from kalshibot.history import BtcHistory
from kalshibot.strategy import Strategy
from kalshibot.types import Book, BtcTick, KTrade, Lot, MarketInfo, SecondView

DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data"


def iso_ts(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


class TapeBook(Book):
    """Stand-in book for the backtest: only ``spread()`` is meaningful (her trade-based spread estimate)."""

    def __init__(self, ts: float, sp: float, min_y: float, min_n: float):
        super().__init__(ts=ts, yes_bids=[(round(1 - min_n, 4), 0.0)], no_bids=[(round(1 - min_y, 4), 0.0)])
        self._sp = sp

    def spread(self, side):  # symmetric: min_y + min_n - 1
        return self._sp


@dataclass(frozen=True)
class Window:
    ticker: str
    open_ts: int
    yes: bool
    strike: float | None


@dataclass
class MarketResult:
    ticker: str
    open_ts: int
    yes: bool
    lots: list[Lot]
    skipped: str | None
    range2h: float | None
    traded: bool = field(init=False)
    pnl: float = field(init=False)

    def __post_init__(self):
        self.traded = bool(self.lots)
        self.pnl = round(sum(lot_pnl(L) for L in self.lots), 2)

    @property
    def entries(self) -> int:
        return len(self.lots)


def lot_cost(L: Lot) -> float:
    return round(L.qty * L.buy + L.fee_in, 2)


def lot_pnl(L: Lot) -> float:
    return round(L.qty * L.exit_px - L.fee_out - lot_cost(L), 2)


class ResearchData:
    def __init__(self, data_dir: str | os.PathLike = DEFAULT_DATA, days: int = 67, cut_ref: str = "2026-10-15"):
        self.dir = Path(data_dir)
        with np.load(self.dir / "prices_1s.npz") as z:
            self.t0 = int(z["T0"])
        self.arr = np.load(self.dir / "composite_1s.npy")
        meta = sorted(json.load(open(self.dir / "settled.json")), key=lambda x: x["close_time"])
        cut = (dt.date.fromisoformat(cut_ref) - dt.timedelta(days=days)).isoformat()
        fin = np.isfinite(self.arr)
        self.windows: list[Window] = []
        for x in meta:
            if x["result"] not in ("yes", "no") or x["close_time"][:10] < cut:
                continue
            if not (self.dir / "ktr" / f"{x['ticker']}.json").exists():
                continue
            op = iso_ts(x["close_time"]) - 900
            a, b = op - 60 - self.t0, op + 900 - self.t0
            if a < 0 or b > len(self.arr) or not fin[a:b].all():
                continue
            k = x.get("floor_strike")
            self.windows.append(Window(x["ticker"], op, x["result"] == "yes", float(k) if k else None))
        self.history = BtcHistory.from_array(self.t0, self.arr)

    def tape(self, ticker: str) -> Tape:
        with open(self.dir / "ktr" / f"{ticker}.json") as f:
            return {int(k): v for k, v in json.load(f).items()}


class Replayer:
    def __init__(self, data: ResearchData, cfg: StrategyCfg | None = None):
        self.data = data
        self.cfg = cfg or StrategyCfg()
        self.strategy = Strategy(self.cfg, data.history, id_prefix="bt")
        self.broker = BacktestBroker(self.strategy.on_fill, self.strategy.on_order_done)

    def views(self, w: Window, K: Tape):
        arr, t0, op = self.data.arr, self.data.t0, w.open_ts
        mark = {"y": None, "n": None}
        for s in range(900):
            t = op + s
            a = K.get(t)
            trades = []
            if a:
                yq, nq = a["y"][1], a["n"][1]
                q = yq + nq
                if q > 0:
                    mark = {"y": (a["y"][2] + (nq - a["n"][2])) / q, "n": (a["n"][2] + (yq - a["y"][2])) / q}
                    if yq > 0:
                        trades.append(KTrade(t, "y", a["y"][2] / yq, yq))
                    if nq > 0:
                        trades.append(KTrade(t, "n", a["n"][2] / nq, nq))
            sp = spread_est(K, t + 1, "y")
            book = None
            if sp is not None:
                book = TapeBook(t, sp, *_mins(K, t + 1))
            yield SecondView(
                ts=float(t), s=s, btc=BtcTick(float(t), float(arr[t - t0])), book=book,
                ask={"y": buy_px(K, t + 1, "y"), "n": buy_px(K, t + 1, "n")},
                bid={"y": sell_fast(K, t + 1, "y"), "n": sell_fast(K, t + 1, "n")},
                mark=mark, trades=trades)

    def run_market(self, w: Window) -> MarketResult | None:
        """None when the market is not tradable at all (no strike). Otherwise the lots (empty if no entry)."""
        if w.strike is None:
            return None
        st, br = self.strategy, self.broker
        m = MarketInfo(ticker=w.ticker, open_ts=float(w.open_ts), close_ts=float(w.open_ts + 900), strike=w.strike)
        st.start_market(m)
        if st.skipped:
            st.end_market(w.yes)
            return MarketResult(w.ticker, w.open_ts, w.yes, [], st.skipped, st.range2h)
        K = self.data.tape(w.ticker)
        br.start_market(m, K)
        for v in self.views(w, K):
            br.on_second(v.s)
            for it in st.step(v):
                br.submit(it, m)
            if st.skipped:
                break
        br.cancel_all()
        lots = st.end_market(w.yes)
        return MarketResult(w.ticker, w.open_ts, w.yes, lots, st.skipped, st.range2h)

    def run(self, windows=None) -> list[MarketResult]:
        out = []
        for w in (self.data.windows if windows is None else windows):
            r = self.run_market(w)
            if r is not None:
                out.append(r)
        return out


def _mins(K: Tape, t: int) -> tuple[float, float]:
    my = mn = 9
    for u in range(t - 3, t + 2):
        a = K.get(u)
        if a:
            if a["y"][1] > 0:
                my = min(my, a["y"][0])
            if a["n"][1] > 0:
                mn = min(mn, a["n"][0])
    return my, mn


def main(argv=None) -> None:  # python3 -P -m kalshibot.backtest.replay [data_dir]
    import sys
    argv = sys.argv[1:] if argv is None else argv
    data = ResearchData(argv[0] if argv else DEFAULT_DATA)
    res = Replayer(data).run()
    traded = [r for r in res if r.traded]
    print(f"{len(traded)} markets, {sum(r.entries for r in traded)} entries, P&L ${sum(r.pnl for r in traded):+,.2f}")


if __name__ == "__main__":
    main()
