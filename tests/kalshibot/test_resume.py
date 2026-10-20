"""Restart in the middle of a market: open lots are taken over (stops + 99.7c sells keep working), live positions are
checked against Kalshi, shutdown cancels only entry orders, and the price history cache makes restarts fast."""
import asyncio

import numpy as np

from kalshibot.app import App, MarketRun
from kalshibot.config import Settings
from kalshibot.execution.base import OrderState
from kalshibot.execution.paper import PaperBroker
from kalshibot.history import BtcHistory
from kalshibot.journal import Journal
from kalshibot.types import Lot, MarketInfo, OrderIntent

from test_strategy import K, OP, make, view

TK = "KXBTC15M-TEST"


def lot(i, side="y", qty=100, buy=0.90, exit=None):
    return Lot(lot_id=f"{TK}-L{i:02d}", ticker=TK, side=side, qty=qty, buy=buy, fee_in=0.5, stop=round(buy * 0.75, 4),
               entry_ts=OP + 120 + 10 * i, how="limit", exit=exit, exit_px=0.997 if exit else None,
               exit_ts=OP + 400 if exit else None)


def app_for(tmp_path, mode="paper", broker=None):
    app = App.__new__(App)
    app.s = Settings()
    app.s.risk.mode = mode
    app.journal, app.cid_ticker, app.alerts = Journal(tmp_path), {}, type("A", (), {"error": lambda *a: None})()
    app.broker = broker or PaperBroker()
    return app


def test_paper_resume_takes_over_open_lots_and_keeps_stops_and_tps(tmp_path):
    app = app_for(tmp_path)
    for L in (lot(1), lot(2), lot(3, exit="tp")):
        app.journal.record_lot(L)
    st, _ = make()
    run = MarketRun(st.market, st)
    asyncio.run(app._resume(run))
    assert [L.lot_id for L in st.open_lots()] == [f"{TK}-L01", f"{TK}-L02"] and len(st.lots) == 3
    assert app.broker.position(TK, "y") == 200 and st.side == "y"
    out = st.step(view(300, mny=0.00005, mark_y=0.60, bid_y=0.60))          # BTC at the strike, price below both stops
    sells = [i for i in out if i.kind == "market_sell"]
    assert {i.lot_id for i in sells} == {f"{TK}-L01", f"{TK}-L02"}
    st2, _ = make()
    run2 = MarketRun(st2.market, st2)
    app2 = app_for(tmp_path / "b"); app2.journal.record_lot(lot(1))
    asyncio.run(app2._resume(run2))
    tps = [i for i in st2.step(view(200, mny=0.002)) if i.kind == "limit_sell"]
    assert [i.lot_id for i in tps] == [f"{TK}-L01"]                           # paper: the 99.7c sell is re-placed
    assert st2._new_lot_id() == f"{TK}-L02"                                  # new lot ids never collide


class FakeLive(PaperBroker):
    def __init__(self, held, resting=()):
        super().__init__()
        self._held, self._resting = held, list(resting)

    async def reconcile(self, market):
        for st in self._resting:
            self.orders[st.client_id] = st
        return [dict(lot_id=f"recon-{TK}-{s}", ticker=TK, side=s, qty=q, buy=0.9, fee_in=1.0, stop=None,
                     entry_ts=None, how="reconciled", tp_client_id=None) for s, q in self._held.items()]


def test_live_resume_adopts_resting_tp_and_cancels_stray_buys(tmp_path):
    tp = OrderState(client_id="kb1-tp", ticker=TK, kind="limit_sell", side="y", qty=100, price=0.997, status="resting")
    buy = OrderState(client_id="kb1-bid", ticker=TK, kind="limit_buy", side="y", qty=100, price=0.9, status="resting")
    app = app_for(tmp_path, "live", FakeLive({"y": 200}, [tp, buy]))
    for L in (lot(1), lot(2)):
        app.journal.record_lot(L)
    app.journal.record_order(OrderIntent(kind="limit_sell", side="y", qty=100, price=0.997, lot_id=f"{TK}-L01",
                                         client_id="kb1-tp", reason="tp"), TK)
    st, _ = make()
    asyncio.run(app._resume(MarketRun(st.market, st)))
    assert st.tp_of == {f"{TK}-L01": "kb1-tp"} and not st.no_entries
    assert buy.status == "cancelled" and tp.status == "resting"
    placed = [i for i in st.step(view(200, mny=0.002)) if i.kind == "limit_sell"]
    assert [i.lot_id for i in placed] == [f"{TK}-L02"]                         # only the lot without a resting sell


def test_live_resume_mismatch_manages_kalshis_position_and_opens_nothing(tmp_path):
    app = app_for(tmp_path, "live", FakeLive({"y": 150}))
    for L in (lot(1), lot(2)):
        app.journal.record_lot(L)
    st, _ = make()
    asyncio.run(app._resume(MarketRun(st.market, st)))
    assert st.no_entries
    op = st.open_lots()
    assert len(op) == 1 and op[0].qty == 150 and op[0].how == "reconciled" and op[0].stop == round(0.9 * (1 - app.s.strategy.stop_pct), 4)
    assert sum(L.exit == "unknown" for L in st.lots) == 2
    assert not [i for i in st.step(view(300, mny=0.003)) if i.kind in ("limit_buy", "market_buy")]


def test_shutdown_cancels_entries_only():
    b = PaperBroker()
    m = MarketInfo(TK, float(OP), float(OP + 900), K)
    b.restore_position(TK, "y", 100)
    b.submit_now(OrderIntent(kind="limit_buy", side="y", qty=100, price=0.5, client_id="bid"), m)
    b.submit_now(OrderIntent(kind="limit_sell", side="y", qty=100, price=0.997, lot_id="L1", client_id="tp"), m)
    assert asyncio.run(b.cancel_entries()) == 1
    assert b.get("bid").status == "cancelled" and b.get("tp").active


def test_history_cache_roundtrip(tmp_path):
    h = BtcHistory(max_days=4)
    t0 = 1_790_000_040
    for i in range(600):
        h.add(t0 + i, 60_000 + i)
    h.add_minute_bar(t0 - 3600, 60_100, 59_900, 60_000)
    p = tmp_path / "bars.npz"
    assert h.save(p) == 11
    g = BtcHistory(max_days=4)
    last = g.load(p, now=t0 + 600)
    assert last == t0 + 540 and g.extreme(t0, t0 + 600, "y") == 60_599 and g.extreme(t0 - 3600, t0, "y") == 60_100
