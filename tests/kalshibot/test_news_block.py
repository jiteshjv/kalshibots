import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from kalshibot.config import RiskCfg
from kalshibot.risk import RiskGuard

ET = ZoneInfo("America/New_York")
ts = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=ET).timestamp()


@pytest.fixture
def cal(tmp_path):
    (tmp_path / "news_calendar.csv").write_text("date,event,time_et\n2026-10-02,jobs,08:30\n2026-10-28,fomc,14:00\n2026-10-15,ppi,08:30\n")
    return tmp_path


def g(cal, **kw):
    return RiskGuard(RiskCfg(**kw), data_dir=cal)


def test_off_by_default(cal):
    assert g(cal).news_block(ts("2026-10-02T08:30")) == ""


def test_window(cal):
    r = g(cal, news_mode="window", news_before_min=15, news_after_min=60)
    assert r.news_block(ts("2026-10-02T08:14")) == ""
    assert r.news_block(ts("2026-10-02T08:16")) == "news_window:jobs"
    assert r.news_block(ts("2026-10-02T09:30")) == "news_window:jobs"
    assert r.news_block(ts("2026-10-02T09:31")) == ""
    assert r.news_block(ts("2026-10-28T14:20")) == "news_window:fomc"
    assert r.news_block(ts("2026-10-15T08:30")) == ""            # ppi not in the default event list


def test_day_mode_uses_pacific_date(cal):
    r = g(cal, news_mode="day")
    assert r.news_block(ts("2026-10-02T02:00")) == ""            # Oct 1, 11 PM PT
    assert r.news_block(ts("2026-10-02T20:00")) == "news_day:jobs"  # Oct 2, 5 PM PT
    assert r.entry_block_reason(None, ts("2026-10-02T20:00")) == "news_day:jobs"


def test_bad_mode_rejected(cal):
    with pytest.raises(ValueError):
        g(cal, news_mode="sometimes")
