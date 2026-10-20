"""Opt-in live smoke test (KB_LIVE_SMOKE=1): public BTC sockets for ~20 s + a handful of Kalshi PUBLIC REST calls.

No keys, no authenticated calls, no orders. Kalshi usage: 1 markets list + 1 book + 1 trades page + 3 x 2 polls."""
import asyncio
import math
import os
import time

import pytest

from kalshibot.marketdata import BtcFeed, KalshiRest, PollingFeed, ViewBuilder

pytestmark = pytest.mark.skipif(os.environ.get("KB_LIVE_SMOKE") != "1", reason="set KB_LIVE_SMOKE=1 to run")


class StubHistory:
    def __init__(self):
        self.secs, self.bars = [], []

    def add(self, ts, price):
        self.secs.append((ts, price))

    def add_minute_bar(self, minute_ts, high, low, close):
        self.bars.append((minute_ts, high, low, close))


def test_live_smoke():
    rest = KalshiRest(reads_per_s=2)
    m = rest.current_market("KXBTC15M")
    assert m is not None, "no open KXBTC15M market right now"
    b = rest.orderbook(m.ticker)
    assert b.yes_bids or b.no_bids
    tr = rest.trades(m.ticker, min_ts=time.time() - 30)
    print(f"\n{m.ticker} strike {m.strike} yes ask {b.best_ask('y')} bid {b.best_bid('y')}, {len(tr)} trades in 30 s")

    h = StubHistory()
    ticks = []
    feed = BtcFeed(on_tick=ticks.append, history=h)
    n = feed.seed_history(h, days=0.2)                 # 1 Coinbase request (288 minutes)
    assert n > 200 and h.bars[-1][0] >= (time.time() // 60) * 60 - 300
    poll = PollingFeed(rest, [m.ticker])
    vb = ViewBuilder(poll, feed)

    async def go():
        task = asyncio.create_task(feed.run())
        await asyncio.sleep(16)
        views = []
        for _ in range(3):
            await poll.poll_once()
            views.append(vb.second(m, time.time()))
            await asyncio.sleep(1.0)
        feed.stop()
        await asyncio.wait_for(task, 5)
        return views

    views = asyncio.run(go())
    ages = feed.ages()
    print("source ages", {k: None if v is None else round(v, 1) for k, v in ages.items()},
          "basis", feed.basis, "last tick", ticks[-1])
    got = [s for s in ("cb", "kr", "bs", "gm") if ages[s] is not None]
    assert "cb" in got and len(got) >= 3, got
    live = [t for t in ticks if not t.stale]
    assert len(live) >= 15 and all(not math.isnan(t.price) for t in live)
    assert abs(live[-1].price - h.bars[-1][3]) < 0.02 * live[-1].price
    v = views[-1]
    print("view", v.s, v.ask, v.bid, v.mark, len(v.trades))
    assert v.book is not None and 0 <= v.s < 900
