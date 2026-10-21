"""Gold bot pieces: per-event news windows, skip hours, and the gold feed without a key (stale -> no entries)."""
import asyncio
import time
import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

from kalshibot.config import RiskCfg, StrategyCfg
from kalshibot.marketdata.gold import GoldFeed
from kalshibot.risk import RiskGuard
from kalshibot.types import MarketInfo

from test_strategy import K, OP, make

ET, PT = ZoneInfo("America/New_York"), ZoneInfo("America/Los_Angeles")
ts = lambda s, z=ET: dt.datetime.fromisoformat(s).replace(tzinfo=z).timestamp()


def test_per_event_news_windows(tmp_path):
    (tmp_path / "news_calendar.csv").write_text("date,event,time_et\n2026-10-15,claims,08:30\n2026-10-28,fomc,14:00\n")
    r = RiskGuard(RiskCfg(news_mode="window", news_windows="claims:15:60,fomc:0:60"), data_dir=tmp_path)
    assert r.news_block(ts("2026-10-15T08:15")) == "news_window:claims"          # 5:15 AM PT
    assert r.news_block(ts("2026-10-15T08:14")) == ""
    assert r.news_block(ts("2026-10-15T09:29")) == "news_window:claims"
    assert r.news_block(ts("2026-10-15T09:30:01")) == ""                        # the 6:30 AM PT window trades
    assert r.news_block(ts("2026-10-28T13:59")) == "" and r.news_block(ts("2026-10-28T14:00")) == "news_window:fomc"
    assert r.news_block(ts("2026-10-28T15:00:01")) == ""


def test_skip_hours_pt():
    st, _ = make(replace(StrategyCfg(), skip_hours_pt="7-9"))
    seen = set()
    for k in range(0, 20 * 3600, 900):                     # 20 hours of windows inside the test's price history
        o = float(OP + k)
        h = dt.datetime.fromtimestamp(o, PT).hour
        st.start_market(MarketInfo("KXGOLD15M-T", o, o + 900, K))
        assert (st.skipped == "7-9 PT") == (7 <= h < 9), h
        seen.add(st.skipped == "7-9 PT")
    assert seen == {True, False}


def test_gold_feed_without_key_uses_hyperliquid_then_swissquote(monkeypatch):
    monkeypatch.delenv("PYTH_API_KEY", raising=False)
    f = GoldFeed(None)
    hl = ({"universe": [{"name": "xyz:TSLA"}, {"name": "xyz:GOLD"}]}, [{"oraclePx": "1"}, {"oraclePx": "4123.4"}])
    sq = [{"spreadProfilePrices": [{"spreadProfile": "elite", "bid": 4123.0, "ask": 4124.0}]}]
    monkeypatch.setattr(f, "_get", lambda url, body=None: hl if "hyperliquid" in url else sq)
    assert f._fetch()[0] == 4123.4 and f.source == "hyperliquid"
    def hl_down(url, body=None):
        if "hyperliquid" in url:
            raise OSError("down")
        return sq
    monkeypatch.setattr(f, "_get", hl_down)
    assert f._fetch()[0] == 4123.5 and f.source == "swissquote"
    monkeypatch.setattr(f, "_get", lambda url, body=None: (_ for _ in ()).throw(OSError("down")))
    async def go():
        t = asyncio.create_task(f.run()); await asyncio.sleep(0.05); f.stop(); await asyncio.sleep(0); t.cancel()
    asyncio.run(go())
    assert f.latest().stale and "hyperliquid" in f.last_error


def test_seed_uses_hyperliquid_trading_minutes_and_falls_back_to_dukascopy(monkeypatch, tmp_path):
    from kalshibot.history import BtcHistory
    f = GoldFeed(None, data_dir=tmp_path)
    mon = int(dt.datetime(2026, 10, 19, 15, 0, tzinfo=dt.timezone.utc).timestamp())   # Monday 8 AM PT
    monkeypatch.setattr(time, "time", lambda: mon)
    calls = []
    def hl(a, b):
        calls.append((a, b))
        return [(m, 4100.5, 4099.5, 4100.0) for m in range(max(a, mon - 5000 * 60) // 60 * 60, b, 60)]
    monkeypatch.setattr(f, "_hl_candles", hl)
    monkeypatch.setattr(f, "_hour", lambda t: (_ for _ in ()).throw(AssertionError("Dukascopy not needed")))
    h = BtcHistory()
    n = f.seed_history(h, days=3)
    sat = int(dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc).timestamp())
    assert n > 0 and h.range_2h(mon) is not None
    assert h.extreme(sat, sat + 3600, "y") is None                     # weekend candles dropped
    monkeypatch.setattr(f, "_hl_candles", lambda a, b: [])            # Hyperliquid down -> Dukascopy
    monkeypatch.setattr(f, "_hour", lambda t: [(s, 4100.0) for s in range(t, t + 3600, 30)])
    g = BtcHistory()
    assert f.seed_history(g, days=1) > 0 and g.range_2h(mon) is not None


def test_seed_counts_trading_hours_not_calendar_days(monkeypatch, tmp_path):
    from kalshibot.history import BtcHistory
    f = GoldFeed(None, data_dir=tmp_path)
    mon = int(dt.datetime(2026, 10, 19, 15, 0, tzinfo=dt.timezone.utc).timestamp())   # Monday 8 AM PT
    monkeypatch.setattr(time, "time", lambda: mon)
    asked = []
    monkeypatch.setattr(f, "_hour", lambda t: asked.append(t) or [])
    monkeypatch.setattr(f, "_hl_candles", lambda a, b: [])
    f.seed_history(BtcHistory(), days=3)
    trading = [t for t in asked if GoldFeed._trading_hour(t)]
    assert len(trading) >= 72 and min(asked) < mon - 4 * 86400                           # reaches back past the weekend
