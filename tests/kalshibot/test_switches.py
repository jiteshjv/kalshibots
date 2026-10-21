"""Kill / Disarm / Rearm: strategy side (flatten, stop_entries) and the dashboard endpoints."""
import json
import urllib.request

from kalshibot.dashboard.server import DashboardServer
from kalshibot.fees import taker_fee
from kalshibot.types import Fill

from test_strategy import OP, _fill_every_check, fill, kinds, make, view


def _held(st):
    return sum(L.qty for L in st.open_lots())


def test_flatten_cancels_everything_and_sells_every_lot():
    st, _ = make()
    _fill_every_check(st, until=300)
    assert _held(st) > 0
    out = st.flatten(view(301))
    cancels = [i for i in out if i.kind == "cancel"]
    sells = [i for i in out if i.kind == "market_sell"]
    assert cancels and all(i.reason == "kill" for i in cancels)
    assert sum(i.qty for i in sells) == sum(L.qty for L in st.lots if L.exit == "kill")
    assert not st.open_lots() and st.no_entries
    # nothing more to send while the sells are working, and no entries afterwards
    assert st.flatten(view(302)) == []
    assert "limit_buy" not in kinds(st.step(view(310))) and "market_buy" not in kinds(st.step(view(320)))


def test_unfilled_kill_sell_is_resent_until_flat():
    st, _ = make()
    _fill_every_check(st, until=200)
    sells = [i for i in st.flatten(view(201)) if i.kind == "market_sell"]
    first = sells[0]
    st.on_order_done(first.client_id, "canceled")          # IOC found no bids: lot is open again
    assert st.open_lots()
    again = [i for i in st.flatten(view(202)) if i.kind == "market_sell"]
    assert [i.lot_id for i in again] == [first.lot_id]
    for it in sells[1:] + again:                            # all fill: flat, exit priced at the fill
        st.on_fill(Fill(it.client_id, it.side, "sell", 0.60, it.qty, taker_fee(0.60, it.qty), float(OP + 203), False))
        st.on_order_done(it.client_id, "executed")
    assert not st.open_lots()
    assert all(L.exit == "kill" and L.exit_px == 0.60 for L in st.lots)


def test_disarm_stops_entries_but_keeps_stops_and_tp():
    st, _ = make()
    _fill_every_check(st, until=200)
    out = st.stop_entries()
    assert all(i.kind == "cancel" for i in out)
    later = []
    for s in range(201, 700):
        later += st.step(view(s))
    assert not {"limit_buy", "market_buy"} & set(kinds(later))
    assert st.open_lots()                                   # positions are still held and managed
    stop = st.step(view(701, mny=0.0, mark_y=0.10, bid_y=0.09))
    assert "market_sell" in kinds(stop)                     # stops still fire while disarmed
    st2, _ = make(); st2.load_state(json.loads(json.dumps(st.state())))
    assert st2.no_entries


def _post(url, tok="s3cret"):
    req = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer " + tok}, data=b"")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_disarm_and_rearm_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHIBOT_DASH_TOKEN", "s3cret")
    srv = DashboardServer(tmp_path, 0)
    srv.start()
    try:
        base = f"http://127.0.0.1:{srv.port}"
        assert _post(base + "/disarm", "wrong")[0] == 401
        code, body = _post(base + "/disarm")
        assert code == 200 and (tmp_path / "DISARM").exists()
        assert _post(base + "/rearm")[0] == 409               # no app attached
        calls = []
        srv.on_rearm = lambda: (calls.append(1) or (False, "still disarming"))
        code, body = _post(base + "/rearm")
        assert code == 409 and body["message"] == "still disarming"
        srv.on_rearm = lambda: (True, "re-armed")
        assert _post(base + "/rearm") == (200, {"ok": True, "error": None, "message": "re-armed"})
    finally:
        srv.stop()


def test_jump_pause_blocks_entries_after_a_sharp_minute():
    from dataclasses import replace
    import numpy as np
    from kalshibot.config import StrategyCfg
    from test_strategy import K, OP, T0
    arr = np.full(4 * 86400, K)
    i = OP + 60 - T0                                       # minute starting at OP+60 closes 0.6% higher ...
    arr[i:] = K * 1.006
    st, _ = make(replace(StrategyCfg(), jump_pause=0.005), arr)
    assert st.jumped(OP + 130) and st.jumped(OP + 60 * 15 + 50)
    assert not st.jumped(OP + 60 * 17 + 5)                 # ... 15 completed minutes later it no longer counts
    st2, _ = make(replace(StrategyCfg(), jump_pause=0.0), arr)
    assert not st2.jumped(OP + 130)
    out = st.step(view(130, mny=0.006))
    assert "limit_buy" not in kinds(out)


def test_stop_recheck_skips_lots_whose_bid_recovered():
    from dataclasses import replace
    from kalshibot.config import StrategyCfg
    st, _ = make(replace(StrategyCfg(), stop_recheck=True))
    _fill_every_check(st, until=200)
    held = len(st.open_lots())
    st.step(view(201, mny=0.002, mark_y=0.10, bid_y=0.95))   # mark says stop, BTC lead fine -> no stop anyway
    out = st.step(view(202, mny=0.0, mark_y=0.10, bid_y=0.95)) # BTC at strike, but the bid is back at 95c
    assert "market_sell" not in kinds(out) and len(st.open_lots()) == held
    out = st.step(view(203, mny=0.0, mark_y=0.10, bid_y=0.09))
    assert "market_sell" in kinds(out)


def test_sweep_3_to_14_day_rule():
    import numpy as np
    from kalshibot.config import StrategyCfg
    from kalshibot.history import BtcHistory
    from kalshibot.strategy import Strategy
    t0 = 1_790_000_000; n = 32 * 86400
    base = np.full(n, 100.0)
    def strat(arr, **kw):
        cfg = StrategyCfg(sweep_days=3, sweep_max_days=30, sweep_4h_m60=0, **kw)
        return Strategy(cfg, BtcHistory.from_array(t0, arr))
    t = t0 + n - 1
    a = base.copy(); a[n - 6 * 86400] = 101.0; a[n - 20 * 86400] = 102.0; a[-200:] = 101.5   # breaks the 14-day high, not the 30-day -> skip
    assert strat(a).skip_reason(t, "y", 101.5) == "14-day sweep"
    b = base.copy(); b[t0 - t0 + 1000] = 102.0; b[-200:] = 102.5     # also breaks the 30-day high -> not skipped
    assert strat(b).skip_reason(t, "y", 102.5) is None
    c = base.copy(); c[n - 2 * 86400] = 101.0; c[-200:] = 101.5      # only a 2-day high -> not skipped (sweep_days 3)
    assert strat(c).skip_reason(t, "y", 101.5) is None


def test_fair_value_stop_triggers_before_the_bid_and_skips_the_recheck():
    import math
    import numpy as np
    from kalshibot.config import StrategyCfg
    from kalshibot.history import BtcHistory
    from kalshibot.strategy import Strategy
    from kalshibot.types import Lot
    from test_strategy import K, OP, T0, view
    rng = np.random.default_rng(1)
    a = K * np.exp(np.cumsum(rng.normal(0, 0.0001, 4 * 86400)))      # ~0.01%/s vol history
    a[OP - T0:] = K
    cfg = StrategyCfg(stop_pct=0.25, stop_lead=0.0003, stop_recheck=True, stop_fair=True)
    st = Strategy(cfg, BtcHistory.from_array(T0, a))
    st.start_market(__import__("kalshibot.types", fromlist=["MarketInfo"]).MarketInfo("KXBTC15M-TEST", float(OP), float(OP + 900), K))
    assert st.sigma and abs(st.sigma - 0.0001) / 0.0001 < 0.2
    # backtest formula (her_trades.fair_yes) at 5 minutes left, BTC 0.01% above the strike
    p = K * 1.0001; left = 899 - 600; w60 = sum((k / 60) ** 2 for k in range(1, 61))
    want = 0.5 * (1 + math.erf((p - K) / (st.sigma * p * math.sqrt(left - 59 + w60) * math.sqrt(2))))
    assert abs(st.fair_yes(OP + 600, p) - want) < 1e-9
    st.lots = [Lot(lot_id="L1", ticker="KXBTC15M-TEST", side="n", qty=100, buy=0.96, fee_in=0.1, stop=0.72, entry_ts=OP + 300)]
    st.side, st.lastp = "n", 0.90                                 # NO bid still 90c (above the 72c stop) ...
    out = st.step(view(600, mny=0.0001, mark_y=0.10, bid_n=0.90, ask_n=0.91))   # ... but fair NO ~ 0.5 < 0.72
    assert [i.kind for i in out if i.lot_id == "L1"].count("market_sell") == 1
    st2 = Strategy(StrategyCfg(stop_pct=0.25, stop_lead=0.0003, stop_recheck=True, stop_fair=False), BtcHistory.from_array(T0, a))
    st2.start_market(st.market); st2.lots = [Lot(lot_id="L1", ticker="KXBTC15M-TEST", side="n", qty=100, buy=0.96, fee_in=0.1, stop=0.72, entry_ts=OP + 300)]
    st2.side, st2.lastp = "n", 0.90
    assert not [i for i in st2.step(view(600, mny=0.0001, mark_y=0.10, bid_n=0.90, ask_n=0.91)) if i.kind == "market_sell"]
