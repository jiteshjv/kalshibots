"""BacktestBroker fill rules (her_trades.py) on a hand-made tape."""
from kalshibot.backtest.broker import BacktestBroker, buy_px, sell_fast, spread_est
from kalshibot.fees import maker_fee, taker_fee
from kalshibot.types import MarketInfo, OrderIntent

OP = 1_786_400_000
NONE = [9, 0, 0]


def tape(d):
    return {OP + s: {"y": v.get("y", NONE), "n": v.get("n", NONE)} for s, v in d.items()}


def setup(K):
    fills, done = [], []
    b = BacktestBroker(fills.append, lambda c, s: done.append((c, s)))
    b.start_market(MarketInfo("T", OP, OP + 900, 60000.0), K)
    return b, fills, done


def test_helpers():
    K = tape({11: {"y": [0.90, 10, 9.0]}, 12: {"y": [0.92, 30, 27.9], "n": [0.05, 5, 0.3]}, 14: {"n": [0.06, 1, 0.06]}})
    assert buy_px(K, OP + 11, "y") == (9.0 + 27.9) / 40
    assert buy_px(K, OP + 13, "y") is None
    assert sell_fast(K, OP + 11, "y") == 1 - 0.3 / 5
    assert sell_fast(K, OP + 13, "y") == 1 - 0.06
    assert sell_fast(K, OP + 15, "y") is None
    assert spread_est(K, OP + 13, "y") == 0.90 + 0.05 - 1


def test_market_orders():
    K = tape({11: {"y": [0.90, 10, 9.0], "n": [0.08, 10, 0.9]}})
    b, fills, done = setup(K)
    b.on_second(10)
    b.submit(OrderIntent("market_buy", "y", 100, price=0.985, client_id="a"))
    assert (fills[-1].price, fills[-1].ts, fills[-1].fee) == (0.9, OP + 11, taker_fee(0.9, 100))
    b.submit(OrderIntent("market_buy", "y", 100, price=0.90, client_id="b"))       # VWAP not below the cap
    assert done[-1] == ("b", "cancelled")
    b.submit(OrderIntent("market_sell", "y", 100, client_id="c"))
    assert fills[-1].price == 1 - 0.09 and fills[-1].action == "sell" and not fills[-1].maker
    b.on_second(20)
    b.submit(OrderIntent("market_sell", "y", 100, client_id="d"))
    assert done[-1] == ("d", "cancelled")


def test_resting_bid_fills_only_through_and_from_next_second():
    K = tape({10: {"y": [0.80, 5, 4.0]}, 11: {"y": [0.90, 5, 4.5]}, 12: {"n": [0.10, 5, 0.5]},
              13: {"n": [0.11, 5, 0.55]}, 14: {"y": [0.899, 1, 0.899]}})
    b, fills, done = setup(K)
    b.on_second(10)
    b.submit(OrderIntent("limit_buy", "y", 200, price=0.90, client_id="a"))
    for s in (10, 11, 12):          # placement second ignored; equal price / exactly 1-L do not fill
        b.on_second(s)
    assert not fills
    b.on_second(13)                 # NO takers paid 0.11 > 1 - 0.90
    assert (fills[-1].client_id, fills[-1].price, fills[-1].ts, fills[-1].fee) == ("a", 0.9, OP + 13,
                                                                                 maker_fee(0.9, 200))
    b.submit(OrderIntent("limit_buy", "y", 100, price=0.90, client_id="b"))
    b.on_second(14)                 # YES printed below the bid
    assert fills[-1].client_id == "b"


def test_tp_and_cancel():
    K = tape({11: {"y": [0.99, 10, 9.96]}, 12: {"y": [0.995, 10, 9.97]}})
    b, fills, done = setup(K)
    b.on_second(10)
    b.submit(OrderIntent("limit_sell", "y", 100, price=0.997, client_id="tp"))
    b.submit(OrderIntent("limit_sell", "y", 100, price=0.997, client_id="tp2"))
    b.submit(OrderIntent("cancel", "y", 0, client_id="tp2"))
    assert done == [("tp2", "cancelled")]
    b.on_second(11)
    assert not fills
    b.on_second(12)                 # VWAP 0.997 >= 0.997
    assert (fills[-1].client_id, fills[-1].price, fills[-1].maker) == ("tp", 0.997, True)
    assert b.cancel_all() == 0
