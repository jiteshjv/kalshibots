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
