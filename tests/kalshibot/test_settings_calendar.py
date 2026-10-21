"""Dashboard settings (validate / apply / save), news calendar download parsing + merge, upcoming no-trade windows."""
import json
import time

from kalshibot import newscal, settings_store as S
from kalshibot.config import RiskCfg, Settings
from kalshibot.risk import RiskGuard


def test_settings_validate_apply_save(tmp_path):
    clean, errs = S.validate({"strategy.size_late": "10", "strategy.stop_pct": 25, "strategy.max_price": 98.5,
                              "strategy.check_every_s": 5, "risk.news_mode": "day"})
    assert not errs and clean == {"strategy.size_late": 10, "strategy.stop_pct": 0.25, "strategy.max_price": 0.985,
                                  "strategy.check_every_s": 5, "risk.news_mode": "day"}
    bad, errs = S.validate({"risk.mode": "live", "risk.live_confirm": "x", "strategy.series": "KXGOLD15M",
                            "strategy.size_late": 0, "risk.news_mode": "sometimes", "strategy.check_every_s": 2.5})
    assert bad == {} and len(errs) == 6                     # mode / confirm / series can never be set from the page
    s = Settings()
    S.apply(s, clean)
    assert s.strategy.size_late == 10 and s.strategy.stop_pct == 0.25 and s.risk.news_mode == "day"
    S.save(tmp_path, clean)
    assert S.load(tmp_path) == clean and S.to_display(s)["strategy.stop_pct"] == 25.0


FED = """<h4>2026 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>October</strong></div><div class="fomc-meeting__date">27-28</div>
<div class="fomc-meeting__month"><strong>December</strong></div><div class="fomc-meeting__date">8-9*</div>
<h4>2025 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>August</strong></div><div class="fomc-meeting__date">22 (notation vote)</div>
<div class="fomc-meeting__month"><strong>Apr/May</strong></div><div class="fomc-meeting__date">30-1</div>"""
FF = json.dumps([
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
    {"title": "CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High"},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-11-04T10:30:00-05:00", "impact": "Medium"},
    {"title": "GDP q/q", "country": "EUR", "date": "2026-11-04T05:00:00-05:00", "impact": "High"}])


def test_calendar_parsers_and_merge(tmp_path):
    assert newscal.parse_fomc(FED) == [("2025-05-01", "fomc", "14:00"), ("2026-10-28", "fomc", "14:00"),
                                       ("2026-12-09", "fomc", "14:00")]
    assert newscal.parse_ff(FF) == [("2026-11-06", "jobs", "08:30"), ("2026-11-10", "cpi", "08:30")]
    p = tmp_path / "news_calendar.csv"
    p.write_text("date,event,time_et\n2026-11-06,jobs,09:00\n2026-10-29,gdp_pce,08:30\n")
    info = newscal.update(p, get=lambda url: FED if "federalreserve" in url else FF)
    rows = newscal.read(p)
    assert ("2026-11-06", "jobs", "08:30") in rows and ("2026-10-29", "gdp_pce", "08:30") in rows   # time corrected, kept
    assert info["sources"] == {"fed": 3, "ff": 2}
    def broken(url):
        raise OSError("offline")
    before = p.read_text()
    assert newscal.update(p, get=broken)["sources"]["fed"].startswith("failed") and p.read_text() == before


def test_upcoming_news_windows(tmp_path):
    (tmp_path / "news_calendar.csv").write_text("date,event,time_et\n2026-10-28,fomc,14:00\n2026-11-06,jobs,08:30\n"
                                                 "2026-10-01,cpi,08:30\n2026-10-29,claims,08:30\n")
    g = RiskGuard(RiskCfg(news_mode="window", news_events="jobs,cpi,fomc", news_before_min=0, news_after_min=60),
                  None, data_dir=tmp_path)
    now = 1792540000.0                                                  # Oct 20 2026
    up = g.upcoming_news(now, days=21)
    assert [e for _, _, e in up] == ["fomc", "jobs"]                    # past CPI and unlisted claims left out
    a, b, _ = up[0]
    assert b - a == 3600 and time.strftime("%H:%M", time.gmtime(a)) == "18:00"   # 2 PM ET = 18:00 UTC


def test_schedule_and_dashboard_export_use_portable_time_formats(tmp_path, monkeypatch):
    """Windows strftime has no %-d / %-I: guard every formatting path the dashboard uses."""
    import datetime as _dt
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "kalshibot")
    bad = [f"{p.name}:{i}" for p in src.rglob("*.py") for i, line in enumerate(p.read_text().splitlines(), 1)
           if re.search(r'strftime\([^)]*%[-#]', line)]
    assert not bad, bad
    from kalshibot.app import App, _clock, _day_clock
    d = _dt.datetime(2026, 10, 28, 9, 5)
    assert _clock(d) == "9:05 AM" and _day_clock(d) == "Wed Oct 28 9:05 AM"
    (tmp_path / "news_calendar.csv").write_text("date,event,time_et\n2099-01-02,fomc,14:00\n")
    app = App.__new__(App)
    app.s = Settings()
    app.s.risk.news_mode, app.s.risk.news_before_min = "window", 0
    app.risk = RiskGuard(app.s.risk, None, data_dir=tmp_path)
    sc = app.schedule(days=30000)
    assert sc["upcoming"][0]["what"] == "FOMC decision" and sc["upcoming"][0]["start"].startswith("Fri Jan 2 11:00 AM")
    from kalshibot.journal import Journal
    j = Journal(tmp_path)
    j.export_dashboard_json(tmp_path / "dashboard.json", meta={"schedule": sc})
    assert json.loads((tmp_path / "dashboard.json").read_text())["meta"]["schedule"]["upcoming"]
