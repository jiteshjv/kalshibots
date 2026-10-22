import asyncio

import pytest

from kalshibot.execution import PaperBroker, maker_fee, order_fee, taker_fee
from kalshibot.execution.base import Broker
from kalshibot.types import Book, BtcTick, KTrade, MarketInfo, OrderIntent, SecondView

M = MarketInfo(ticker="KXBTC15M-TEST", open_ts=1000.0, close_ts=1900.0, strike=100000.0)


def view(s, yes_bids=(), no_bids=(), trades=(), book=True, ask=None, bid=None):
    ts = M.open_ts + s
    b = Book(ts=ts, yes_bids=list(yes_bids), no_bids=list(no_bids)) if book else None
    return SecondView(ts=ts, s=s, btc=BtcTick(ts=ts, price=100100.0), book=b,
                      ask=ask or {"y": b.best_ask("y") if b else None, "n": b.best_ask("n") if b else None},
                      bid=bid or {"y": b.best_bid("y") if b else None, "n": b.best_bid("n") if b else None},
                      mark={"y": None, "n": None}, trades=list(trades))


def T(s, side, price, qty=10):
    return KTrade(ts=M.open_ts + s, taker_side=side, price=price, qty=qty)


class Rec:
    def __init__(self):
        self.fills, self.done = [], []

    def broker(self, **kw):
        return PaperBroker(on_fill=self.fills.append, on_done=lambda c, s: self.done.append((c, s)), **kw)


def run(coro):
    return asyncio.run(coro)


def sub(b, **kw):
    return b.submit_now(OrderIntent(**kw), M)


THIN = dict(no_bids=[(0.04, 50), (0.03, 100), (0.01, 500)], yes_bids=[(0.95, 100), (0.94, 100)])


# ------------------------------------------------------------------------------------------------------------ fees
def test_fee_math():
    assert taker_fee(0.5, 100) == 1.75
    assert maker_fee(0.5, 100) == 0.44
    assert taker_fee(0.97, 200) == 0.41
    assert maker_fee(0.997, 100) == 0.01
    assert taker_fee(0.0, 100) == 0.0
    # multi-level order: one ceil over the whole order
    assert order_fee([(0.96, 50), (0.97, 100)], maker=False) == 0.34
    assert order_fee([(0.95, 200)], maker=True) == maker_fee(0.95, 200)


def test_protocol():
    assert isinstance(PaperBroker(), Broker)


# ----------------------------------------------------------------------------------------------------- market buy
def test_market_buy_walks_asks_to_cap_partial_then_cancel():
    r = Rec(); b = r.broker()
    b.on_second(view(130, **THIN))
    st = sub(b, kind="market_buy", side="y", qty=200, price=0.975, client_id="m1", lot_id="L1")
    assert r.fills == []                                  # 1 s latency: works on the next second
    b.on_second(view(131, **THIN))
    assert len(r.fills) == 1
    f = r.fills[0]
    assert f.qty == 150 and f.action == "buy" and not f.maker
    assert f.price == pytest.approx((0.96 * 50 + 0.97 * 100) / 150, abs=1e-6)
    assert f.fee == 0.34
    assert r.done == [("m1", "cancelled")]
    assert st.status == "cancelled" and st.filled == 150 and st.remaining == 50
    assert b.position(M.ticker, "y") == 150


def test_market_buy_full_fill_no_done_and_uses_next_second_book():
    r = Rec(); b = r.broker()
    b.on_second(view(130, **THIN))
    sub(b, kind="market_buy", side="n", qty=100, price=0.985, client_id="m1")
    b.on_second(view(131, yes_bids=[(0.03, 500)], no_bids=[(0.9, 10)]))   # NO asks = 0.97 x 500
    assert [(f.qty, f.price) for f in r.fills] == [(100, 0.97)]
    assert r.done == []
    assert b.get("m1").status == "filled"


def test_market_orders_share_book_liquidity_within_a_second():
    r = Rec(); b = r.broker()
    b.on_second(view(130))
    sub(b, kind="market_buy", side="y", qty=40, price=0.99, client_id="a")
    sub(b, kind="market_buy", side="y", qty=40, price=0.99, client_id="b")
    b.on_second(view(131, no_bids=[(0.04, 50)]))
    assert [(f.client_id, f.qty) for f in r.fills] == [("a", 40), ("b", 10)]
    assert r.done == [("b", "cancelled")]


def test_market_buy_without_book_uses_view_quote():
    r = Rec(); b = r.broker()
    sub(b, kind="market_buy", side="y", qty=100, price=0.985, client_id="m")
    b.on_second(view(131, book=False, ask={"y": 0.98, "n": 0.03}, bid={"y": 0.97, "n": 0.02}))
    assert [(f.qty, f.price) for f in r.fills] == [(100, 0.98)]


def test_market_delay_off_fills_on_submit():
    r = Rec(); b = r.broker(market_delay=False)
    b.on_second(view(130, **THIN))
    sub(b, kind="market_buy", side="y", qty=10, price=0.99, client_id="m")
    assert [(f.qty, f.price) for f in r.fills] == [(10, 0.96)]


# ---------------------------------------------------------------------------------------------- resting entry bid
@pytest.mark.parametrize("trade,fills", [
    (("y", 0.95), False),     # at our price: not through (back of queue)
    (("n", 0.05), False),     # NO taker at exactly 1-L: not through
    (("y", 0.96), False),
    (("n", 0.04), False),
    (("n", 0.06), True),      # NO taker paid > 1-L -> hit YES bids below L -> through us
    (("y", 0.94), True),      # YES taker bought below L
])
def test_limit_buy_queue_rule(trade, fills):
    r = Rec(); b = r.broker()
    b.on_second(view(120, no_bids=[(0.04, 100)]))          # YES ask 0.96
    st = sub(b, kind="limit_buy", side="y", qty=200, price=0.955 - 0.005, client_id="e1", expires_s=10)
    assert st.status == "pending"
    b.on_second(view(121, no_bids=[(0.04, 100)], trades=[T(121, *trade)]))
    if fills:
        assert [(f.qty, f.price, f.maker) for f in r.fills] == [(200, 0.95, True)]
        assert r.fills[0].fee == maker_fee(0.95, 200)
        assert st.status == "filled"
    else:
        assert r.fills == [] and st.status == "resting"


def test_limit_buy_no_side_queue_rule():
    r = Rec(); b = r.broker()
    b.on_second(view(120))
    sub(b, kind="limit_buy", side="n", qty=100, price=0.95, client_id="e1")
    b.on_second(view(121, trades=[T(121, "y", 0.05), T(121, "n", 0.95)]))
    assert r.fills == []
    b.on_second(view(122, trades=[T(122, "y", 0.051)]))
    assert [(f.side, f.qty, f.price) for f in r.fills] == [("n", 100, 0.95)]


def test_limit_buy_volume_mode_partial():
    r = Rec(); b = r.broker(resting_fill="volume")
    b.on_second(view(120))
    st = sub(b, kind="limit_buy", side="y", qty=200, price=0.95, client_id="e1")
    b.on_second(view(121, trades=[T(121, "n", 0.06, 30), T(121, "y", 0.95, 500), T(121, "n", 0.07, 20)]))
    assert sum(f.qty for f in r.fills) == 50 and st.status == "resting" and st.remaining == 150


def test_limit_buy_post_only_reject():
    r = Rec(); b = r.broker()
    b.on_second(view(120, no_bids=[(0.05, 100)]))          # YES ask 0.95
    st = sub(b, kind="limit_buy", side="y", qty=100, price=0.95, client_id="e1")
    assert st.status == "rejected" and r.done == [("e1", "rejected")]


def test_entry_expiry_then_strategy_cancel_and_market_fallback():
    r = Rec(); b = r.broker()
    b.on_second(view(120, **THIN))
    sub(b, kind="limit_buy", side="y", qty=200, price=0.955, client_id="e1", lot_id="L1", expires_s=10)
    for s in range(121, 130):
        b.on_second(view(s, **THIN, trades=[T(s, "y", 0.96)]))
    assert r.fills == [] and r.done == []
    b.on_second(view(130, **THIN))                         # s == expiry: broker leaves it to the strategy
    assert b.get("e1").status == "resting"
    sub(b, kind="cancel", side="y", qty=0, client_id="e1")  # strategy: cancel ...
    sub(b, kind="market_buy", side="y", qty=200, price=0.985, client_id="e1-mkt", lot_id="L1",
        reason="entry_market_fallback")                    # ... then market
    assert r.done == [("e1", "cancelled")]
    b.on_second(view(131, **THIN))
    assert [(f.client_id, f.qty) for f in r.fills] == [("e1-mkt", 150)]
    assert r.done[-1] == ("e1-mkt", "cancelled")


def test_fill_at_expiry_second_wins_and_late_cancel_is_noop():
    r = Rec(); b = r.broker()
    b.on_second(view(120))
    sub(b, kind="limit_buy", side="y", qty=100, price=0.95, client_id="e1", expires_s=10)
    b.on_second(view(130, trades=[T(130, "n", 0.08)]))
    assert len(r.fills) == 1
    assert run(b.cancel("e1")) is False
    sub(b, kind="cancel", side="y", qty=0, client_id="e1")
    assert r.done == []


def test_expiry_backstop():
    r = Rec(); b = r.broker(expiry_grace_s=1.0)
    b.on_second(view(120))
    sub(b, kind="limit_buy", side="y", qty=100, price=0.95, client_id="e1", expires_s=10)
    b.on_second(view(130))
    assert r.done == []
    b.on_second(view(131))
    assert r.done == [("e1", "expired")]


# ------------------------------------------------------------------------------------------------ stops / TP
def _hold(b, side="y", qty=300):
    b.on_second(view(100))
    sub(b, kind="market_buy", side=side, qty=qty, price=0.99, client_id=f"buy-{side}")
    b.on_second(view(101, no_bids=[(0.04, 10_000)], yes_bids=[(0.04, 10_000)]))
    assert b.position(M.ticker, side) == qty


def test_stop_sale_walks_thin_book_to_floor():
    r = Rec(); b = r.broker()
    _hold(b)
    r.fills.clear()
    sub(b, kind="market_sell", side="y", qty=300, price=0.5, client_id="stop1", reason="stop")
    b.on_second(view(200, yes_bids=[(0.60, 100), (0.55, 50), (0.30, 1000)]))
    f = r.fills[0]
    assert f.qty == 150 and f.action == "sell" and not f.maker
    assert f.price == pytest.approx((0.6 * 100 + 0.55 * 50) / 150, abs=1e-6)
    assert f.fee == order_fee([(0.6, 100), (0.55, 50)], maker=False)
    assert r.done == [("stop1", "cancelled")]
    assert b.position(M.ticker, "y") == 150
    # the strategy re-issues with a 1c floor: the rest goes into the deep 30c level
    sub(b, kind="market_sell", side="y", qty=150, price=0.01, client_id="stop2", reason="stop")
    b.on_second(view(201, yes_bids=[(0.30, 1000)]))
    assert (r.fills[-1].qty, r.fills[-1].price) == (150, 0.30)
    assert b.position(M.ticker, "y") == 0


def test_sells_are_reduce_only():
    r = Rec(); b = r.broker()
    _hold(b, qty=100)
    r.fills.clear()
    sub(b, kind="market_sell", side="y", qty=300, price=0.01, client_id="s")
    b.on_second(view(200, yes_bids=[(0.5, 1000)]))
    assert r.fills[0].qty == 100 and b.position(M.ticker, "y") == 0
    sub(b, kind="market_sell", side="n", qty=10, price=0.01, client_id="s2")
    b.on_second(view(201, no_bids=[(0.5, 1000)]))
    assert len(r.fills) == 1 and r.done[-1] == ("s2", "cancelled")


def test_take_profit_resting_sell():
    r = Rec(); b = r.broker()
    _hold(b, side="n", qty=200)
    r.fills.clear()
    sub(b, kind="limit_sell", side="n", qty=200, price=0.997, client_id="tp1", lot_id="L1", reason="tp")
    b.on_second(view(300, yes_bids=[(0.002, 50)], no_bids=[(0.99, 10)],
                     trades=[T(300, "n", 0.996), T(300, "y", 0.5)]))
    assert r.fills == []
    b.on_second(view(301, trades=[T(301, "n", 0.997, 5)]))
    assert [(f.qty, f.price, f.maker, f.action) for f in r.fills] == [(200, 0.997, True, "sell")]
    assert r.fills[0].fee == maker_fee(0.997, 200)
    assert b.position(M.ticker, "n") == 0


def test_take_profit_volume_mode_partial_and_multiple_lots():
    r = Rec(); b = r.broker(resting_fill="volume")
    _hold(b, qty=300)
    r.fills.clear()
    sub(b, kind="limit_sell", side="y", qty=200, price=0.997, client_id="tpA")
    sub(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tpB")
    b.on_second(view(300, trades=[T(300, "y", 0.998, 150)]))
    assert [(f.client_id, f.qty) for f in r.fills] == [("tpA", 150)]
    b.on_second(view(301, trades=[T(301, "y", 0.997, 500)]))
    assert [(f.client_id, f.qty) for f in r.fills[1:]] == [("tpA", 50), ("tpB", 100)]


def test_take_profit_marketable_on_placement_is_taker():
    r = Rec(); b = r.broker()
    _hold(b, qty=100)
    r.fills.clear()
    sub(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp")
    b.on_second(view(300, yes_bids=[(0.998, 60), (0.997, 100)]))
    f = r.fills[0]
    assert f.qty == 100 and not f.maker and f.price == pytest.approx((0.998 * 60 + 0.997 * 40) / 100)


def test_cancel_tp_by_lot_then_stop():
    r = Rec(); b = r.broker()
    _hold(b, qty=100)
    sub(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp", lot_id="L1")
    b.on_second(view(300))
    sub(b, kind="cancel", side="y", qty=0, client_id="cx-1", lot_id="L1")
    assert r.done == [("tp", "cancelled")]
    sub(b, kind="market_sell", side="y", qty=100, price=0.01, client_id="stop", lot_id="L1")
    b.on_second(view(301, yes_bids=[(0.4, 1000)], trades=[T(301, "y", 0.999)]))   # TP gone: no double sale
    assert [(f.client_id, f.qty) for f in r.fills[-1:]] == [("stop", 100)]


# ----------------------------------------------------------------------------------------- cancel_all / misc
def test_cancel_all_and_ticker_filter():
    r = Rec(); b = r.broker()
    b.on_second(view(120))
    sub(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="a")
    b.submit_now(OrderIntent(kind="limit_buy", side="y", qty=100, price=0.9, client_id="other"), "OTHER-TICKER")
    sub(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="b")
    assert b.open_order_count() == 3
    assert run(b.cancel_all(M.ticker)) == 2
    assert sorted(r.done) == [("a", "cancelled"), ("b", "cancelled")]
    assert run(b.cancel_all()) == 1 and b.open_order_count() == 0
    assert run(b.cancel_all()) == 0


def test_idempotent_client_id_and_rejects():
    r = Rec(); b = r.broker()
    b.on_second(view(120))
    s1 = sub(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="a")
    s2 = sub(b, kind="limit_buy", side="y", qty=999, price=0.5, client_id="a")
    assert s1 is s2 and len(b.orders) == 1
    assert sub(b, kind="limit_buy", side="y", qty=0, price=0.9, client_id="z").status == "rejected"
    assert sub(b, kind="limit_sell", side="y", qty=5, price=None, client_id="z2").status == "rejected"
    st = sub(b, kind="market_buy", side="y", qty=5, price=0.9)
    assert st.client_id.startswith("pp-")


def test_async_api():
    r = Rec(); b = r.broker()
    run(b.submit(OrderIntent(kind="limit_buy", side="y", qty=10, price=0.5, client_id="a"), M))
    assert run(b.cancel("a")) is True and run(b.cancel("a")) is False
    assert run(b.reconcile(M)) == []


def _scenario():
    r = Rec(); b = r.broker()
    b.on_second(view(120, **THIN))
    sub(b, kind="limit_buy", side="y", qty=200, price=0.955, client_id="e1", expires_s=10)
    b.on_second(view(121, **THIN, trades=[T(121, "n", 0.045)]))
    sub(b, kind="market_buy", side="y", qty=100, price=0.98, client_id="m1")
    b.on_second(view(122, **THIN, trades=[T(122, "n", 0.06)]))
    sub(b, kind="limit_sell", side="y", qty=300, price=0.997, client_id="tp")
    b.on_second(view(123, trades=[T(123, "y", 0.997)]))
    return [(f.client_id, f.qty, f.price, f.fee, f.ts, f.maker) for f in r.fills], r.done, round(b.cash, 6)


def test_deterministic():
    a = _scenario()
    assert a == _scenario()
    fills, done, cash = a
    assert [x[0] for x in fills] == ["m1", "e1", "tp"]
    assert cash == pytest.approx(-(0.96 * 50 + 0.97 * 50) - order_fee([(0.96, 50), (0.97, 50)], False) - 200 * 0.955 - maker_fee(0.955, 200)
                                 + 300 * 0.997 - maker_fee(0.997, 300))


def test_cancel_of_already_filled_order_does_not_touch_other_lot_orders():
    r = Rec(); b = r.broker()
    b.on_second(view(120))
    sub(b, kind="limit_buy", side="y", qty=100, price=0.95, client_id="e1", lot_id="L1", expires_s=10)
    b.on_second(view(130, trades=[T(130, "n", 0.08)]))           # entry fills at the expiry second
    sub(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp", lot_id="L1")
    sub(b, kind="cancel", side="y", qty=0, client_id="e1", lot_id="L1", reason="entry_expired")
    assert b.get("tp").active and r.done == []


def test_work_market_now_sells_into_the_live_book_mid_second():
    r = Rec(); b = r.broker()
    _hold(b)
    r.fills.clear()
    sub(b, kind="market_sell", side="y", qty=100, price=0.01, client_id="fs", reason="stop")
    assert r.fills == []                                  # normal path: waits for the next second
    b.work_market_now(view(199, yes_bids=[(0.62, 500)]))  # fast stop: executes against this book at once
    assert [(f.qty, f.price) for f in r.fills] == [(100, 0.62)]
    assert b.get("fs").status == "filled"
    b.on_second(view(200, yes_bids=[(0.40, 500)]))        # not sold again at the next second
    assert len(r.fills) == 1
