import json
from dataclasses import replace

import numpy as np
import pytest

from kalshibot.config import StrategyCfg
from kalshibot.fees import maker_fee, taker_fee
from kalshibot.history import BtcHistory
from kalshibot.strategy import Strategy
from kalshibot.types import Book, BtcTick, Fill, KTrade, MarketInfo, SecondView

T0 = 1_786_003_200
OP = T0 + 3 * 86400
K = 60_000.0


def make(cfg=None, arr=None):
    a = np.full(4 * 86400, K) if arr is None else arr
    h = BtcHistory.from_array(T0, a)
    st = Strategy(cfg or StrategyCfg(), h)
    st.start_market(MarketInfo("KXBTC15M-TEST", float(OP), float(OP + 900), K))
    return st, a


def view(s, mny=0.002, ask_y=0.90, ask_n=0.12, bid_y=None, bid_n=None, mark_y=None, book=None, stale=False,
         btc=True):
    ts = float(OP + s)
    mark = {"y": mark_y, "n": None if mark_y is None else 1 - mark_y}
    trades = [KTrade(ts, "y", mark_y, 10)] if mark_y is not None else []
    return SecondView(ts=ts, s=s, btc=BtcTick(ts, K * (1 + mny), stale=stale) if btc else None, book=book,
                      ask={"y": ask_y, "n": ask_n},
                      bid={"y": bid_y if bid_y is not None else (ask_y - 0.01 if ask_y else None),
                           "n": bid_n if bid_n is not None else (ask_n - 0.01 if ask_n else None)},
                      mark=mark, trades=trades)


def kinds(intents):
    return [i.kind for i in intents]


def fill(st, it, price=None, s=None, maker=True):
    p = it.price if price is None else price
    st.on_fill(Fill(it.client_id, it.side, "buy" if "buy" in it.kind else "sell", p, it.qty,
                    (maker_fee if maker else taker_fee)(p, it.qty), float(OP + (s or 0)), maker))


# ------------------------------------------------------------------------------------------------ fees
def test_fees():
    assert taker_fee(0.5, 100) == 1.75
    assert maker_fee(0.5, 100) == 0.44                 # 0.4375 rounded up
    assert maker_fee(0.997, 100) == 0.01               # 0.0052 -> 1c
    assert taker_fee(0.9, 200) == 1.26                 # 1.26 exactly, the 1e-9 guard stops 1.27
    assert taker_fee(0.5, 0) == 0.0


# ------------------------------------------------------------------------------------------------ threshold / timing
def test_threshold_decay():
    st, _ = make()
    assert st.threshold(120) == pytest.approx(0.0015)
    assert st.threshold(510) == pytest.approx(0.0010)
    assert st.threshold(900) == pytest.approx(0.0005)
    assert st.threshold(2000) == pytest.approx(0.0005)
    assert st.threshold(720) == 0.0015 + (0.0005 - 0.0015) * 600 / 780


def test_entry_threshold_and_limit_price():
    st, _ = make()
    assert st.step(view(120, mny=0.0014)) == []
    it, = st.step(view(130, mny=0.0016, ask_y=0.912))
    assert (it.kind, it.side, it.qty, it.price, it.expires_s) == ("limit_buy", "y", 200, 0.907, 10)
    st2, _ = make()
    it, = st2.step(view(510, mny=-0.00101))
    assert (it.side, it.price) == ("n", 0.11)          # 11.5c is not a valid Kalshi price: whole cents 10-90c


def test_check_times_and_cutoff_min_12():
    st, _ = make()
    assert st.step(view(110)) == []                    # before minute 2
    assert st.step(view(125)) == []                    # not a 10 s check
    st, _ = make()
    assert kinds(st.step(view(720, mny=0.001))) == ["limit_buy"]
    st, _ = make()
    assert st.step(view(730, mny=0.01)) == []          # after minute 12


def test_no_entry_when_ask_too_high_or_btc_stale_or_missing():
    st, _ = make()
    assert st.step(view(120, ask_y=0.985)) == []
    assert st.step(view(130, ask_y=None)) == []
    assert st.step(view(140, stale=True)) == []
    assert st.step(view(150, btc=False)) == []
    assert kinds(st.step(view(160, ask_y=0.984))) == ["limit_buy"]


# ------------------------------------------------------------------------------------------------ sizing
def test_sizing_mixed_and_equal():
    st, _ = make()
    assert st.step(view(350))[0].qty == 200
    st, _ = make()
    assert st.step(view(360))[0].qty == 100
    st, _ = make(StrategyCfg(sizing="equal"))
    assert st.step(view(120))[0].qty == 100


def _fill_every_check(st, until=720):
    """Fill every entry bid the second after it is placed; returns all intents."""
    allx, pend = [], None
    for s in range(120, until + 1):
        if pend is not None:
            fill(st, pend, s=s)
            pend = None
        out = st.step(view(s))
        allx += out
        for i in out:
            if i.kind == "limit_buy":
                pend = i
    return allx


def test_cap_5000_contracts():
    st, _ = make()
    out = _fill_every_check(st)
    buys = [i for i in out if i.kind == "limit_buy"]
    assert sum(i.qty for i in buys) == 5000
    assert [i.qty for i in buys][-3:] == [200, 100, 100]
    assert sum(L.qty for L in st.open_lots()) == 5000
    assert max(i.price for i in out if i.kind == "limit_sell") == 0.997


def test_cap_partial_size():
    st, _ = make(StrategyCfg(max_held=450))
    out = _fill_every_check(st, until=200)
    assert [i.qty for i in out if i.kind == "limit_buy"] == [200, 200, 50]


# ------------------------------------------------------------------------------------------------ fills, TP, fallback
def test_limit_fill_then_tp_and_stop_price():
    st, _ = make()
    bid, = st.step(view(120, ask_y=0.905))
    fill(st, bid, s=121)
    L, = st.lots
    assert (L.buy, L.stop, L.how, L.qty) == (0.9, round(0.9 * 0.7, 4), "limit", 200)
    tp, = st.step(view(121))
    assert (tp.kind, tp.price, tp.qty, tp.lot_id) == ("limit_sell", 0.997, 200, L.lot_id)
    assert st.step(view(122)) == []                    # one TP per lot only
    fill(st, tp, s=123)
    assert L.exit == "tp" and L.pnl() == pytest.approx(200 * 0.997 - maker_fee(0.997, 200) - 200 * 0.9
                                                       - maker_fee(0.9, 200))


def test_expiry_cancel_and_market_fallback():
    st, _ = make()
    bid, = st.step(view(120))
    assert st.step(view(129)) == []
    out = st.step(view(130, ask_y=0.93))
    assert kinds(out) == ["cancel", "market_buy", "limit_buy"]   # same check places a new bid; the 99.7c sell waits
    #                                                               for the market buy's fill
    assert out[0].client_id == bid.client_id
    assert (out[1].qty, out[1].price, out[1].side) == (200, 0.985, "y")
    L, = st.lots
    assert (L.how, L.buy, L.stop) == ("market", 0.93, round(0.93 * 0.7, 4))
    st.on_order_done(bid.client_id, "cancelled")       # broker confirms the cancel: no effect
    assert st.pending == out[2].client_id and len(st.lots) == 1
    fill(st, out[1], price=0.931, s=131, maker=False)  # reconcile with the real fill
    assert L.buy == 0.931 and L.fee_in == taker_fee(0.931, 200)
    assert kinds(st.step(view(131))) == ["limit_sell"]  # now the 99.7c sell goes out


def test_fallback_needs_ask_below_cap():
    st, _ = make()
    st.step(view(120))
    assert kinds(st.step(view(130, ask_y=0.99))) == ["cancel"]


def test_fallback_unfilled_market_buy_is_undone():
    st, _ = make()
    st.step(view(120))
    out = st.step(view(130))
    st.on_order_done(out[1].client_id, "cancelled")
    assert st.lots == [] and st.side is None
    assert kinds(st.step(view(131))) == []             # no 99.7c sell was ever placed for the unfilled buy


def test_no_fallback_when_disabled():
    st, _ = make(StrategyCfg(limit_fallback=False))
    st.step(view(120))
    assert kinds(st.step(view(130, ask_y=0.93))) == ["cancel", "limit_buy"]


def test_market_entries_when_no_offset():
    st, _ = make(StrategyCfg(limit_offset=0.0))
    out = st.step(view(120))
    assert kinds(out) == ["market_buy"]
    fill(st, out[0], s=121, maker=False)
    assert kinds(st.step(view(121))) == ["limit_sell"]


# ------------------------------------------------------------------------------------------------ stops
def _one_lot(buy=0.9):
    st, a = make()
    bid, = st.step(view(120, ask_y=buy + 0.005))
    fill(st, bid, s=121)
    tp, = st.step(view(121, mark_y=buy))
    return st, a, tp


def test_stop_needs_both_conditions():
    st, _, tp = _one_lot()                             # stop at 0.63
    assert st.step(view(122, mark_y=0.60, mny=0.0005)) == []      # mark low but BTC lead >= 0.01%
    assert st.step(view(123, mark_y=0.70, mny=0.00005)) == []     # BTC near strike but mark above the stop
    assert st.step(view(124, mark_y=0.63, mny=0.0001)) == []      # lead == stop_lead is not < stop_lead
    out = st.step(view(125, mark_y=0.63, mny=0.00009, bid_y=0.55))
    assert kinds(out) == ["cancel", "market_sell"]
    assert out[0].client_id == tp.client_id and out[1].lot_id == st.lots[0].lot_id and out[1].qty == 200
    L = st.lots[0]
    assert (L.exit, L.exit_px) == ("stop", 0.55) and not st.open_lots()


def test_fast_stop_sells_mid_second_once():
    st, _, tp = _one_lot()                             # stop at 0.63
    assert st.fast_stops(view(122, mark_y=0.70, mny=0.00005)) == []   # prints so far above the stop
    assert st.fast_stops(view(122, mark_y=None, mny=0.00005)) == []   # no prints yet this second: nothing new
    out = st.fast_stops(view(122, mark_y=0.60, mny=0.00005, bid_y=0.58))
    assert kinds(out) == ["cancel", "market_sell"] and out[0].client_id == tp.client_id
    assert st.lots[0].exit == "stop" and st.fast_stop_n == 1 and st.lastp != 0.60   # the carried mark is untouched
    assert kinds(st.step(view(122, mark_y=0.60, mny=0.00005))) == []  # the whole-second step does not sell it again


def test_fast_stop_keeps_the_stop_rule():
    st, _, _ = _one_lot()
    assert st.fast_stops(view(122, mark_y=0.60, mny=0.0005)) == []    # BTC lead too large: no stop, as in step()
    assert kinds(st.fast_stops(view(122, mark_y=0.60, mny=0.00005, bid_y=0.70))) == ["cancel", "market_sell"]
    st2, _, _ = _one_lot()                             # stop_recheck on: the bid is back above the stop -> hold
    st2.cfg = replace(st2.cfg, stop_recheck=True)
    assert st2.fast_stops(view(122, mark_y=0.60, mny=0.00005, bid_y=0.70)) == []


def test_stop_mark_carries_from_last_print():
    st, _, _ = _one_lot()
    st.step(view(122, mark_y=0.60, mny=0.01))          # mark 0.60 printed, BTC far
    out = st.step(view(123, mark_y=None, mny=-0.0001))  # no prints this second: mark still 0.60
    assert kinds(out) == ["cancel", "market_sell"]


def test_stop_needs_a_bid_and_retries():
    st, _, _ = _one_lot()
    v = view(122, mark_y=0.5, mny=0.0)
    v.bid["y"] = None
    assert st.step(v) == []
    assert kinds(st.step(view(123, mark_y=0.5, mny=0.0))) == ["cancel", "market_sell"]


def test_failed_stop_reopens_lot_and_tp():
    st, _, _ = _one_lot()
    out = st.step(view(122, mark_y=0.5, mny=0.0))
    st.on_order_done(out[1].client_id, "cancelled")
    assert st.lots[0].open
    assert kinds(st.step(view(123, mark_y=0.7, mny=0.01))) == ["limit_sell"]


# ------------------------------------------------------------------------------------------------ roll
def test_roll_after_full_stop_out():
    st, _, _ = _one_lot()
    assert st.step(view(130, mny=-0.002)) == []        # other side while a lot is open: no
    out = st.step(view(131, mark_y=0.5, mny=0.0, bid_y=0.45))
    fill(st, out[1], price=0.45, s=132, maker=False)
    assert not st.open_lots()
    nb, = st.step(view(140, mny=-0.002, ask_n=0.6))
    assert (nb.kind, nb.side) == ("limit_buy", "n")
    assert st.lastp is None


def test_same_side_entries_continue_after_partial_stop():
    st, _, _ = _one_lot()
    assert kinds(st.step(view(130, mny=0.002))) == ["limit_buy"]


# ------------------------------------------------------------------------------------------------ filters
def test_range_filter_at_market_start():
    a = np.full(4 * 86400, K)
    a[OP - T0 - 1000] = K * 1.032
    st, _ = make(arr=a)
    assert st.skipped == "2h range"
    assert st.step(view(120)) == []
    a[OP - T0 - 1000] = K * 1.030
    st, _ = make(arr=a)
    assert st.skipped is None
    st, _ = make(arr=np.full(4 * 86400, np.nan))
    assert st.skipped == "no price history"


def test_sweep_filter_skips_market_at_first_entry():
    a = np.full(4 * 86400, K)
    a[OP - T0 + 100] = K * 1.0001                      # new 2-day high within the last 5 min
    st, _ = make(arr=a)
    assert st.step(view(120)) == [] and st.skipped == "2-day sweep"   # 3+ days not covered -> "2-day"
    assert st.step(view(130)) == []
    st, _ = make(arr=a)
    assert kinds(st.step(view(120, mny=-0.002))) == ["limit_buy"]   # a sweep up does not block a NO entry


def test_sweep_filter_reruns_while_bid_rests_and_cancels_it():
    a = np.full(4 * 86400, K)
    st, _ = make(arr=a)
    bid, = st.step(view(120))
    a[OP - T0 + 125] = K * 1.0001
    out = st.step(view(130, ask_y=0.99))             # bid expired, fallback impossible -> filter runs again
    assert kinds(out) == ["cancel"] and out[0].client_id == bid.client_id and st.skipped


def test_sweep_filter_not_checked_after_first_entry():
    a = np.full(4 * 86400, K)
    st, _ = make(arr=a)
    bid, = st.step(view(120))
    fill(st, bid, s=121)
    a[OP - T0 + 125] = K * 1.0001
    assert "limit_buy" in kinds(st.step(view(130)))


def test_4h_sweep_with_stretched_1h():
    i = OP - T0 + 120
    b = np.full(4 * 86400, K)
    b[i - 3 * 86400 + 1000] = K * 1.02                  # older than 2 days: ignored by the 2-day test
    b[i - 3 * 3600] = K * 1.003                         # 4h-window high (within 2 days, so 2-day high = this)
    b[i - 100] = K * 1.0035
    st, _ = make(arr=b)
    assert st.skip_reason(OP + 120, "y", K * 1.0035) == "2-day sweep"
    c = np.full(4 * 86400, K)
    c[i - 30 * 3600] = K * 1.01                         # 2-day high far above
    c[i - 3 * 3600] = K * 1.003                         # 4h high
    c[i - 100] = K * 1.0035                             # sweeps the 4h high only
    c[i - 3600] = K * 0.9995                            # 1h move = +0.40% >= 0.3879%
    st, _ = make(arr=c)
    assert st.skip_reason(OP + 120, "y", K * 1.0035) == "4h sweep + stretched 1h"
    c[i - 3600] = K * 0.9999                            # 1h move 0.36%: kept
    assert st.skip_reason(OP + 120, "y", K * 1.0035) is None


def test_filters_off():
    a = np.full(4 * 86400, K)
    a[OP - T0 + 100] = K * 1.0001
    st, _ = make(StrategyCfg(filters=False), arr=a)
    assert kinds(st.step(view(120))) == ["limit_buy"]


# ------------------------------------------------------------------------------------------------ spread
def test_spread_only_checked_when_book_exists():
    wide = Book(ts=0, yes_bids=[(0.86, 10)], no_bids=[(0.10, 10)])      # ask y 0.90, bid y 0.86 -> 4c
    tight = Book(ts=0, yes_bids=[(0.88, 10)], no_bids=[(0.10, 10)])     # 2c
    edge = Book(ts=0, yes_bids=[(0.87, 10)], no_bids=[(0.10, 10)])      # 3c: allowed
    st, _ = make()
    assert st.step(view(120, book=wide)) == []
    assert kinds(st.step(view(130, book=tight))) == ["limit_buy"]
    st, _ = make()
    assert kinds(st.step(view(120, book=edge))) == ["limit_buy"]
    st, _ = make()
    assert kinds(st.step(view(120, book=None))) == ["limit_buy"]


# ------------------------------------------------------------------------------------------------ settle / state
def test_end_market_settles():
    st, _, _ = _one_lot()
    lots = st.end_market(yes_won=True)
    assert lots[0].exit == "settle_win" and lots[0].exit_px == 1.0 and lots[0].fee_out == 0
    st, _, _ = _one_lot()
    assert st.end_market(yes_won=False)[0].pnl() == pytest.approx(-(200 * 0.9 + maker_fee(0.9, 200)))


def test_state_round_trip():
    st, _, tp = _one_lot()
    st.step(view(130))
    d = json.loads(json.dumps(st.state()))
    st2 = Strategy(StrategyCfg(), st.history)
    st2.load_state(d)
    a = st.step(view(140, ask_y=0.95))
    b = st2.step(view(140, ask_y=0.95))
    assert [(i.kind, i.qty, i.price, i.client_id) for i in a] == [(i.kind, i.qty, i.price, i.client_id) for i in b]
    assert st2.lots == st.lots


def test_deterministic_ids():
    st1, _ = make()
    st2, _ = make()
    assert st1.step(view(120))[0].client_id == st2.step(view(120))[0].client_id


# ------------------------------------------------------------------------------------------------ her risk check / entry wait
def _risk_ref(arr, t, u, k, samp=5, short=300, long_=900, adj=40):
    """scripts/her_trades.py risk_at, on the same array."""
    import math
    sds = []
    for w in (short, long_):
        d = np.diff(arr[t - w - T0:t - T0 + 1:samp])
        d = d[np.isfinite(d)]
        if len(d) >= 5:
            sds.append(float(np.std(d)) * math.sqrt(60 / samp))
    left = max(900 - u - adj, 1) / 60
    z = abs(arr[t - T0] - k) / (max(sds) * math.sqrt(left))
    return 0.5 * math.erfc(z / math.sqrt(2))


def test_risk_matches_backtest_formula():
    rng = np.random.default_rng(7)
    a = K + np.cumsum(rng.normal(0, 3.0, 4 * 86400))
    a += K - a[OP - T0]                                     # at the strike at the open
    st, _ = make(StrategyCfg(risk_max=0.15, mny_start=0, mny_floor=0), a)
    for u in (120, 300, 600, 720):
        t = OP + u
        assert st.risk(float(t), float(a[t - T0])) == pytest.approx(_risk_ref(a, t, u, K), rel=1e-9)


def test_risk_check_gates_entries():
    a = np.full(4 * 86400, K)
    a[OP - T0 - 900:] += np.tile([0.0, 30.0], (len(a) - (OP - T0 - 900)) // 2 + 1)[:len(a) - (OP - T0 - 900)]
    cfg = StrategyCfg(risk_max=0.15, mny_start=0, mny_floor=0, sizing="equal")
    st, _ = make(cfg, a)
    near = st.step(view(120, mny=0.0001))                   # $6 from the strike, BTC jumping $30 a second: no entry
    assert "limit_buy" not in kinds(near) and st.risk_blocks == 1
    far = st.step(view(130, mny=0.01))                      # $600 away: risk ~0, entry
    assert "limit_buy" in kinds(far)


def test_risk_off_uses_moneyness_line():
    st, _ = make(StrategyCfg(sizing="equal"))
    assert "limit_buy" not in kinds(st.step(view(120, mny=0.001)))   # under the 0.15% line
    assert st.risk_blocks == 0


def test_entry_wait_s_market_after_5s():
    st, _ = make(StrategyCfg(sizing="equal", entry_wait_s=5))
    bid = [i for i in st.step(view(120)) if i.kind == "limit_buy"][0]
    assert kinds(st.step(view(124))) == []
    out = st.step(view(125))
    assert kinds(out)[:2] == ["cancel", "market_buy"] and out[0].client_id == bid.client_id


def test_unfilled_stop_waits_before_retrying():
    st, _, _ = _one_lot()                              # stop at 0.63
    sell = lambda s: [i for i in st.fast_stops(view(s, mark_y=0.60, mny=0.00005, bid_y=0.58)) if i.kind == "market_sell"]
    out = sell(122)
    assert len(out) == 1
    st.on_order_done(out[0].client_id, "cancelled")    # nobody bought: no order storm in the same second
    assert sell(122) == [] and kinds(st.step(view(122, mark_y=0.60, mny=0.00005))) == ["limit_sell"]   # TP back, no sale
    out = sell(123)                                    # 1 s later: try again
    assert len(out) == 1
    st.on_order_done(out[0].client_id, "cancelled")
    assert sell(124) == []                             # second failure: wait 2 s
    out = sell(125)
    assert len(out) == 1
    st.on_order_done(out[0].client_id, "cancelled")
    assert sell(128) == [] and len(sell(129)) == 1     # then 4 s, the most it waits


def test_fallback_market_buy_rechecks_risk():
    rng = np.random.default_rng(3)
    a = K * np.exp(np.cumsum(rng.normal(0, 0.00002, 4 * 86400)))   # some volatility in the history
    for mny_at_expiry, want in ((0.0, ["cancel"]), (0.002, ["cancel", "market_buy", "limit_buy"])):
        st, _ = make(replace(StrategyCfg(), risk_max=0.15, mny_start=0.0, mny_floor=0.0, filters=False), a)
        st.market = replace(st.market, strike=float(a[OP - T0]))   # strike = BTC at the open
        K2 = st.market.strike
        v = lambda s_, m_, **kw: replace(view(s_, **kw), btc=BtcTick(float(OP + s_), K2 * (1 + m_)))
        bid, = st.step(v(120, 0.002))                  # far from the strike: risk low, bid placed
        assert bid.kind == "limit_buy"
        out = st.step(v(130, mny_at_expiry, ask_y=0.93))
        assert kinds(out) == want                      # at the strike (risk ~50%): no market buy, no new bid


def test_risk_unknown_with_too_little_history():
    rng = np.random.default_rng(5)
    full = K * np.exp(np.cumsum(rng.normal(0, 0.0001, 4 * 86400)))
    st, _ = make(replace(StrategyCfg(), risk_max=0.15), full)
    ts = OP + 300
    assert st.risk(ts, K * 1.0005) is not None              # full history: a number
    t_short = ts - 30                                        # just restarted: 30 s of per-second data
    st2 = Strategy(replace(StrategyCfg(), risk_max=0.15), BtcHistory.from_array(t_short, full[t_short - T0:ts - T0 + 1]))
    st2.start_market(st.market)
    assert st2.risk(ts, K * 1.0005) is None                  # unknown -> no entry, instead of a falsely calm number
    for m in range((t_short - 1200) // 60 * 60, t_short, 60):   # startup seeding: 1-minute bars before the restart
        c_ = float(full[m + 59 - T0])
        st2.history.add_minute_bar(m, c_, c_, c_)
    r_full, r_bars = st.risk(ts, K * 1.0005), st2.risk(ts, K * 1.0005)
    assert r_bars is not None and abs(r_bars - r_full) < 0.15     # minute bars give a comparable number at once


def test_fractional_stop_fills_leave_clean_quantities():
    st, _, _ = _one_lot()
    st.lots[0].qty = 1
    out = st.step(view(122, mark_y=0.5, mny=0.0))
    sell = [i for i in out if i.kind == "market_sell"][0]
    st.orders[sell.client_id].qty = 1
    o = st.orders[sell.client_id]
    o.filled, o.cost, o.first_ts = 0.57, 0.57 * 0.5, float(OP + 122)    # 0.57 of the 1 contract sold
    st.on_order_done(sell.client_id, "cancelled")            # 0.43 left unsold -> split off, reopened
    rest = [L for L in st.lots if L.open]
    assert len(rest) == 1 and rest[0].qty == 0.43             # not 0.43000000000000005


def test_fast_stop_waits_while_the_side_is_switching():
    st, _, _ = _one_lot()                              # YES lot, stop 0.63
    out = st.step(view(122, mark_y=0.5, mny=0.0))      # stopped out: no lots, side still "y"
    st.on_order_done([i for i in out if i.kind == "market_sell"][0].client_id, "filled")
    for L in st.lots:
        L.exit = L.exit or "stop"
    st._new_side = "n"                                  # a NO entry bid filled between seconds ...
    from kalshibot.types import Lot
    st.lots.append(Lot(lot_id="N1", ticker=st.market.ticker, side="n", qty=10, buy=0.83, fee_in=0.01, stop=0.6225,
                       entry_ts=float(OP + 123)))
    v = view(123, mark_y=0.10, mny=-0.00001, bid_y=0.09, bid_n=0.89)   # YES prices low = NO doing fine
    assert st.fast_stops(v) == []                      # no sale of the NO lot judged by YES prices


def test_size_by_risk_bands_ask_cap_and_max_bought():
    """size_by_risk / size_ask_cap / max_bought = the backtest's SZRISK / SZASK / MKTMAX (ez_ research 2026-10-23)."""
    cfg = StrategyCfg(risk_max=0.15, mny_start=0, mny_floor=0, sizing="equal", filters=False,
                      size_by_risk="5:150,10:100,15:50", size_ask_cap="97:50")
    for rk, ask, want in ((0.03, 0.90, 150), (0.05, 0.90, 150), (0.08, 0.90, 100), (0.12, 0.90, 50), (0.03, 0.975, 50)):
        st, _ = make(cfg)
        st.risk = lambda ts, p, _r=rk: _r
        bid, = [i for i in st.step(view(120, mny=0.01, ask_y=ask)) if i.kind == "limit_buy"]
        assert bid.qty == want, (rk, ask)
    st, _ = make(replace(cfg, size_by_risk="", size_ask_cap="", size_late=100, max_bought=150))
    st.risk = lambda ts, p: 0.03
    bid, = [i for i in st.step(view(120, mny=0.01)) if i.kind == "limit_buy"]
    fill(st, bid, s=120)
    nxt = [i for i in st.step(view(130, mny=0.01)) if i.kind == "limit_buy"]
    assert [i.qty for i in nxt] == [50]                 # 100 bought, 150 allowed in all
    st, _ = make(replace(cfg, size_by_risk="5:150"))     # risk above every band: the normal size
    st.risk = lambda ts, p: 0.10
    bid, = [i for i in st.step(view(120, mny=0.01)) if i.kind == "limit_buy"]
    assert bid.qty == 100


def test_size_bands_setting_is_checked():
    from kalshibot.settings_store import validate
    ok, errs = validate({"strategy.size_by_risk": "5:150, 10:100,15:50"})
    assert not errs and ok["strategy.size_by_risk"] == "5:150, 10:100,15:50"
    _, errs = validate({"strategy.size_by_risk": "5-150"})
    assert errs
