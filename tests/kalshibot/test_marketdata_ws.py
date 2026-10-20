"""KalshiWs book maintenance (snapshot/delta/seq gap -> resync), trades, fills, BRTI; PollingFeed. Offline."""
import asyncio
import json
from pathlib import Path

import pytest

from kalshibot.marketdata.kalshi_rest import from_yes_book
from kalshibot.marketdata.kalshi_ws import KalshiWs, LiveBook, PollingFeed, TradeTape, parse_fill
from kalshibot.types import Book, KTrade

FIX = Path(__file__).parent / "fixtures" / "marketdata"
MSG = json.loads((FIX / "ws_messages.json").read_text())
TK = "KXBTC15M-26OCT191345-45"


class FakeRest:
    has_key = True
    key_id = "kid"
    key_path = None

    def __init__(self, book=None, trades=()):
        self.book_calls = []
        self.trade_calls = []
        self._book = book or Book(ts=0, yes_bids=[(0.91, 10.0)], no_bids=[(0.05, 20.0)])
        self._trades = list(trades)

    def orderbook(self, ticker):
        self.book_calls.append(ticker)
        return Book(ts=0, yes_bids=list(self._book.yes_bids), no_bids=list(self._book.no_bids))

    def trades_with_ids(self, ticker, min_ts=None):
        self.trade_calls.append(min_ts)
        return [x for x in self._trades if min_ts is None or x[1].ts >= int(min_ts)]


class FakeWs:
    def __init__(self, incoming=()):
        self.sent = []
        self.incoming = list(incoming)

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        return json.dumps(self.incoming.pop(0))


def mk(**kw):
    w = KalshiWs(FakeRest(), [TK], clock=lambda: 1000.0, **kw)
    w.ws = FakeWs()
    w.connected = True
    return w


def run(coro):
    return asyncio.run(coro)


def test_livebook_apply():
    b = LiveBook()
    b.load([(0.9, 10)], [(0.05, 5)], 1.0)
    b.apply("y", 0.9, -4, 2.0)
    b.apply("y", 0.91, 3, 2.0)
    b.apply("n", 0.05, -5, 2.0)          # level removed
    s = b.snapshot(2.0)
    assert s.yes_bids == [(0.91, 3), (0.9, 6)] and s.no_bids == []


def test_snapshot_and_deltas_with_yes_price_conversion():
    w = mk()
    books = []
    w.on_book = lambda tk, b: books.append(b)

    async def go():
        for k in ("subscribed", "snapshot", "delta_yes", "delta_no"):
            await w.handle(MSG[k])
    run(go())
    assert w.sids["orderbook_delta"] == 7
    b = w.book(TK)
    assert b.yes_bids == [(0.90, 100.0), (0.88, 50.0)]
    # NO levels arrived in YES-leg prices (0.95/0.94/+0.93) -> NO bids 0.05/0.06/0.07
    assert b.no_bids == [(0.07, 10.0), (0.06, 75.0), (0.05, 300.0)]
    assert b.best_ask("y") == 0.93 and b.best_ask("n") == 0.10 and b.spread("y") == 0.03
    assert b.ts == 1000.0 and len(books) == 3


def test_legacy_no_leg_pricing_when_flag_off():
    w = mk(use_yes_price=False)
    run(w.handle(MSG["snapshot"]))
    assert w.book(TK).no_bids[0] == (0.95, 300.0)


def test_seq_gap_triggers_resync_via_ws_and_rest():
    w = mk()

    async def go():
        for k in ("subscribed", "snapshot", "delta_yes", "delta_gap"):
            await w.handle(MSG[k])
        assert w.books[TK].dirty                       # gap delta NOT applied, book invalid
        assert w.book(TK).ts < 1000.0 or w.books[TK].dirty
        await asyncio.sleep(0.05)                      # let the REST resync task run
    run(go())
    assert w.gaps == 1 and w.resyncs == 1
    cmd = w.ws.sent[-1]
    assert cmd["cmd"] == "update_subscription" and cmd["params"] == {"sids": [7], "market_tickers": [TK],
                                                                       "action": "get_snapshot"}
    assert w.rest.book_calls == [TK]
    b = w.book(TK)
    assert not w.books[TK].dirty and b.yes_bids == [(0.91, 10.0)] and b.no_bids == [(0.05, 20.0)]
    # the stream continues from the new seq; the WS snapshot then wins over the REST copy
    nxt = dict(MSG["snapshot"], seq=10)
    run(w.handle(nxt))
    assert w.book(TK).yes_bids == [(0.90, 120.0), (0.88, 50.0)] and w.gaps == 1


def test_deltas_ignored_until_snapshot():
    w = mk()
    run(w.handle(MSG["delta_yes"]))
    assert w.book(TK) is None


def test_disconnected_book_ts_freezes():
    w = mk()
    run(w.handle(MSG["snapshot"]))
    run(w._set_connected(False))
    assert w.books[TK].dirty and w.book(TK).ts == 1000.0
    w.clock = lambda: 1005.0
    assert w.book(TK).ts == 1000.0        # stale: ts does not advance while disconnected


def test_trade_fill_brti_messages():
    w = mk()
    got = {}
    w.on_trade = lambda tk, t: got.setdefault("trade", (tk, t))
    w.on_fill = lambda f, raw: got.setdefault("fill", f)
    w.on_brti = lambda ts, p: got.setdefault("brti", (ts, p))

    async def go():
        await w.handle(MSG["trade"])
        await w.handle(MSG["trade"])            # duplicate trade id ignored
        await w.handle(MSG["fill"])
        await w.handle(MSG["brti"])
    run(go())
    tk, t = got["trade"]
    assert tk == TK and t == KTrade(ts=1792432202.25, taker_side="n", price=0.09, qty=136.0)
    assert len(w.trades(TK, 1792432202, 1792432203)) == 1
    f = got["fill"]
    assert f["client_order_id"] == "kb-1-e1" and f["book_side"] == "ask" and f["qty"] == 200.0 and f["fee"] == 0.03
    assert from_yes_book(f["book_side"], f["yes_price"], "n") == ("buy", 0.91)
    assert got["brti"] == (1792432202.0, 108150.12)


def test_parse_fill_legacy_fields():
    f = parse_fill({"side": "no", "action": "sell", "no_price_dollars": "0.95", "count": 3, "ts": 5})
    assert f["book_side"] == "bid" and f["yes_price"] == 0.05 and f["qty"] == 3.0


def test_full_session_subscribes_and_reconnects():
    incoming = [MSG["subscribed"], MSG["snapshot"], MSG["delta_yes"]]
    conns = []

    class Conn:
        def __init__(self, url, additional_headers):
            self.ws = FakeWs(incoming if not conns else [])
            self.headers = additional_headers
            conns.append(self)

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, *a):
            return False

    import kalshibot.marketdata.kalshi_ws as kw
    orig = kw.auth_headers
    kw.auth_headers = lambda kid, kp, m, p: {"KALSHI-ACCESS-KEY": kid, "SIGNED": f"{m} {p}"}
    status = []
    try:
        w = KalshiWs(FakeRest(), [TK], connect=Conn, brti=True, clock=lambda: 1.0, max_backoff=0.01,
                     on_status=status.append)

        async def go():
            task = asyncio.create_task(w.run())
            while len(conns) < 2:
                await asyncio.sleep(0.01)
            w.stop()
            await asyncio.wait_for(task, 3)
        run(go())
    finally:
        kw.auth_headers = orig
    assert conns[0].headers["SIGNED"] == "GET /trade-api/ws/v2"
    subs = [m for m in conns[0].ws.sent if m["cmd"] == "subscribe"]
    chans = [m["params"]["channels"][0] for m in subs]
    assert chans == ["orderbook_delta", "trade", "ticker", "fill", "cfbenchmarks_value"]
    assert subs[0]["params"] == {"channels": ["orderbook_delta"], "market_tickers": [TK], "use_yes_price": True}
    assert subs[3]["params"] == {"channels": ["fill"]}
    assert subs[4]["params"]["index_ids"] == ["BRTI"]
    assert status[:2] == [True, False]
    assert w.books[TK].yes == {0.9: 100.0, 0.88: 50.0}


def test_set_tickers_updates_subscriptions():
    w = mk()
    run(w.handle(MSG["subscribed"]))
    w.sids["trade"] = 8
    w.sids["ticker"] = 9
    run(w.set_tickers(["NEW"]))
    sent = [(m["params"]["sids"], m["params"]["action"], m["params"]["market_tickers"]) for m in w.ws.sent]
    assert ([7], "add_markets", ["NEW"]) in sent and ([7], "delete_markets", [TK]) in sent
    assert ([8], "add_markets", ["NEW"]) in sent and TK not in w.books


def test_trade_tape_order_and_between():
    tp = TradeTape()
    tp.add("T", "a", KTrade(10.5, "y", 0.9, 1))
    tp.add("T", "c", KTrade(12.1, "y", 0.9, 1))
    tp.add("T", "b", KTrade(11.2, "n", 0.1, 1))           # late arrival, inserted in order
    assert not tp.add("T", "b", KTrade(11.2, "n", 0.1, 1))
    assert [t.ts for t in tp.between("T", 10, 13)] == [10.5, 11.2, 12.1]
    assert [t.ts for t in tp.between("T", 11, 12)] == [11.2]


def test_polling_feed_dedups_and_books():
    trades = [("t1", KTrade(100.2, "y", 0.9, 5)), ("t2", KTrade(101.4, "n", 0.1, 3))]
    rest = FakeRest(trades=trades)
    now = [102.0]
    seen = []
    pf = PollingFeed(rest, [TK], clock=lambda: now[0], on_trade=lambda tk, t: seen.append(t))

    async def go():
        await pf.poll_once()
        rest._trades.append(("t3", KTrade(102.5, "y", 0.92, 1)))
        now[0] = 103.0
        await pf.poll_once()
    run(go())
    assert [t.ts for t in seen] == [100.2, 101.4, 102.5]          # no duplicates across polls
    assert rest.trade_calls[0] == pytest.approx(102.0 - 30) and rest.trade_calls[1] == pytest.approx(101.4 - 2)
    b = pf.book(TK)
    assert b.ts == 103.0 and b.best_bid("y") == 0.91
    assert len(pf.trades(TK, 101, 102)) == 1


def test_polling_feed_polls_book_faster_than_trades():
    rest = FakeRest(trades=[])
    books = []
    pf = PollingFeed(rest, [TK], interval=0.2, book_interval=0.04, on_book=lambda tk, b: books.append(b))

    async def go():
        t = asyncio.create_task(pf.run())
        await asyncio.sleep(0.39)
        pf.stop()
        await asyncio.sleep(0.25)
        t.cancel()
    run(go())
    assert len(books) >= 7 and 1 <= len(rest.trade_calls) <= 3      # ~10 books vs ~2 trade polls
