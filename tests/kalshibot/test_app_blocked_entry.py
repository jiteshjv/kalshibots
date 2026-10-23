"""Tonight's paper bug (Oct 19, 9:38 PM): an unfilled entry bid falls back to a market buy that the risk guard blocks.
The strategy drops the provisional lot; the journal must drop it too, and its 99.7c sell must never be sent."""
import asyncio

from kalshibot.app import App, MarketRun
from kalshibot.execution.paper import PaperBroker
from kalshibot.journal import Journal

from test_strategy import make, view


class BlockMarketBuys:
    def allow(self, it, v):
        return (it.kind != "market_buy", "stale_book")


def test_blocked_market_fallback_leaves_no_phantom_lot_and_no_sell(tmp_path):
    app = App.__new__(App)
    app.journal, app.risk, app.cid_ticker = Journal(tmp_path), BlockMarketBuys(), {}
    sent = []
    app.broker = PaperBroker()
    orig = app.broker.submit
    async def spy(it, m):
        sent.append(it.kind); await orig(it, m)
    app.broker.submit = spy
    st, _ = make()
    run = MarketRun(st.market, st)
    async def go():
        for s in range(120, 200):                          # bids are never filled -> fallback market buys get blocked
            await app._submit_all(run, st.step(view(s)), view(s))
    asyncio.run(go())
    assert "market_buy" not in sent and "limit_sell" not in sent
    assert st.open_lots() == [] and app.journal.lots() == []
    app.journal.close()


def test_no_trade_triggered_stop_check_once_shutdown_starts():
    from dataclasses import replace
    from kalshibot.config import Settings
    from kalshibot.types import KTrade
    from test_strategy import _one_lot
    st, _, _ = _one_lot()                                  # one open lot
    app = App.__new__(App)
    s = Settings()
    app.s = replace(s, strategy=replace(s.strategy, fast_stop_ms=100))
    app.recorder, app._fast_busy, app._fast_task = None, False, None
    app.current = MarketRun(st.market, st)
    app._step_lock = asyncio.Lock()
    calls = []
    async def fake_fast(now):
        calls.append(now)
    app._fast = fake_fast
    async def go(stopping):
        app.stopping, app._fast_busy = stopping, False
        app._rec_trade(st.market.ticker, KTrade(0.0, "y", 0.5, 1))
        if app._fast_task:
            await app._fast_task
    asyncio.run(go(False))
    assert len(calls) == 1                                 # normal: a trade starts a stop check
    app._fast_task = None
    asyncio.run(go(True))
    assert len(calls) == 1 and app._fast_task is None      # shutting down: no new check


def test_recorder_failure_never_blocks_a_stop_sale(tmp_path):
    from test_strategy import _one_lot

    class BrokenRecorder:
        def write(self, kind, payload):
            raise OSError("disk full")

    st, _, _ = _one_lot()                                  # one open lot (stop 0.63)
    app = App.__new__(App)
    app.journal, app.risk, app.cid_ticker = Journal(tmp_path), BlockMarketBuys(), {}
    app.recorder = BrokenRecorder()
    from kalshibot.config import Settings
    app.s, app.history = Settings(), st.history

    class NoAlerts:
        def __getattr__(self, name):
            return lambda *a, **k: None
    app.alerts = NoAlerts()
    sent = []
    app.broker = PaperBroker()
    async def spy(it, m):
        sent.append(it.kind)
    app.broker.submit = spy
    run = MarketRun(st.market, st)
    v = view(125, mark_y=0.5, mny=0.0)
    asyncio.run(app._submit_all(run, st.step(v), v))
    assert "market_sell" in sent                           # the stop reached the broker despite the recorder failing
    app.journal.close()


def test_rec_forwards_the_timestamp():
    got = []

    class Rec:
        def write(self, kind, payload, ts=None):
            got.append((kind, ts))
    app = App.__new__(App)
    app.recorder = Rec()
    app._rec("px", {"c": 1.0}, ts=123.4)
    app._rec("trade", {"t": "X"})
    assert got == [("px", 123.4), ("trade", None)]


def test_no_new_entries_while_a_cancel_is_unconfirmed(tmp_path):
    from kalshibot.config import Settings
    class AllowAll:
        def allow(self, it, v):
            return True, ""
    st, _ = make()
    app = App.__new__(App)
    app.journal, app.risk, app.cid_ticker = Journal(tmp_path), AllowAll(), {}
    app.s, app.recorder = Settings(), None
    sent = []
    app.broker = PaperBroker()
    app.broker.pending_cancels = {"X1": "e1"}
    async def spy(it, m):
        sent.append(it.kind)
    app.broker.submit = spy
    run = MarketRun(st.market, st)
    asyncio.run(app._submit_all(run, st.step(view(120)), view(120)))
    assert sent == [] and run.blocks.get("unconfirmed_cancel") == 1
    app.broker.pending_cancels = {}
    asyncio.run(app._submit_all(run, st.step(view(130)), view(130)))
    assert "limit_buy" in sent or "market_buy" in sent
    app.journal.close()
