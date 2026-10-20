"""Gold bot pieces: per-event news windows, skip hours, and the gold feed without a key (stale -> no entries)."""
import asyncio
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


def test_gold_feed_without_key_is_stale(monkeypatch):
    monkeypatch.delenv("PYTH_API_KEY", raising=False)
    f = GoldFeed(None)
    async def go():
        t = asyncio.create_task(f.run()); await asyncio.sleep(0.05); f.stop(); await asyncio.sleep(0); t.cancel()
    asyncio.run(go())
    assert f.latest().stale and "PYTH_API_KEY" in f.last_error
