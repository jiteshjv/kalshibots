import datetime as dt
import json
import threading

import pytest

from kalshibot.journal import Journal
from kalshibot.types import Fill, Lot, MarketInfo, OrderIntent

# 2026-10-19 20:00:00 UTC = 1:00 PM PDT
OPEN = dt.datetime(2026, 10, 19, 20, 0, tzinfo=dt.timezone.utc).timestamp()


def mk(open_ts=OPEN, t="KXBTC15M-26OCT191615-15"):
    return MarketInfo(ticker=t, open_ts=open_ts, close_ts=open_ts + 900, strike=67000.5)


@pytest.fixture
def j(tmp_path):
    jj = Journal(tmp_path)
    yield jj
    jj.close()


def test_wal_and_tables(j):
    mode = j._db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    names = {r[0] for r in j._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"markets", "lots", "orders", "fills", "events"} <= names
    assert j.path.name == "journal.sqlite"


def test_pnl_math_and_summary(j):
    m = mk()
    j.record_market_open(m, range_2h=0.012)
    # TP lot: 200 @ 0.95, fee_in 0.67, sold at 0.997 with fee_out 0.05
    a = Lot("a", m.ticker, "y", 200, 0.95, 0.67, 0.665, OPEN + 130, "limit")
    j.record_lot(a, mny=0.0016, th=0.0015)
    j.update_lot("a", exit="tp", exit_px=0.997, exit_ts=OPEN + 600, fee_out=0.05)
    # stopped lot: 100 @ 0.93, fee_in 0.46, sold at 0.42 fee 1.72
    b = Lot("b", m.ticker, "y", 100, 0.93, 0.46, 0.651, OPEN + 400, "market")
    j.record_lot(b)
    j.update_lot("b", exit="stop", exit_px=0.42, exit_ts=OPEN + 700, fee_out=1.72)
    # open lot (no P&L yet)
    j.record_lot(Lot("c", m.ticker, "y", 100, 0.96, 0.27, 0.672, OPEN + 710))

    pa = 200 * 0.997 - 0.05 - (200 * 0.95 + 0.67)
    pb = 100 * 0.42 - 1.72 - (100 * 0.93 + 0.46)
    assert j.realized_pnl("2026-10-19") == pytest.approx(pa + pb)
    assert j.realized_pnl(dt.date(2026, 10, 18)) == 0.0
    s = j.daily_summary("2026-10-19")
    assert s["entries"] == 2 and s["stopped"] == 1 and s["take_profit"] == 1
    assert s["pnl"] == pytest.approx(round(pa + pb, 2))
    assert s["fees"] == pytest.approx(0.67 + 0.05 + 0.46 + 1.72)
    assert s["open_lots"] == 1 and s["markets"] == 1


def test_settlement_and_utc_day_boundary(j):
    late = dt.datetime(2026, 10, 19, 23, 50, tzinfo=dt.timezone.utc).timestamp()
    m = mk(late, "KXBTC15M-26OCT191905-05")
    j.record_market_open(m)
    lot = Lot("x", m.ticker, "n", 100, 0.9, 0.63, 0.63, late + 200)
    j.record_lot(lot)
    lot.exit, lot.exit_px, lot.exit_ts = "settle_win", 1.0, m.close_ts  # closes 00:05 UTC on the 20th
    j.record_market_close(m.ticker, yes_won=False, lots=[lot])
    assert j.realized_pnl("2026-10-19") == 0.0
    assert j.realized_pnl("2026-10-20") == pytest.approx(100 - 90 - 0.63)
    r = j._rows("SELECT result FROM markets")[0]
    assert r["result"] == "NO"


def test_orders_and_fills(j):
    j.record_order(OrderIntent("limit_buy", "y", 200, 0.95, lot_id=None, client_id="kb-1", reason="entry"), "T1")
    j.record_order(OrderIntent("limit_sell", "y", 200, 0.997, lot_id="L1", client_id="kb-2", reason="tp"), "T1",
                   status="resting")
    j.record_order(OrderIntent("market_buy", "y", 100, 0.985, client_id="kb-3", reason="entry_market_fallback"), "T1")
    assert {o["client_id"] for o in j.open_orders()} == {"kb-1", "kb-2", "kb-3"}
    j.record_fill(Fill("kb-1", "y", "buy", 0.95, 120, 0.4, OPEN, True))
    assert {o["client_id"]: o["status"] for o in j.open_orders()}["kb-1"] == "partial"
    j.record_fill(Fill("kb-1", "y", "buy", 0.95, 80, 0.27, OPEN + 1, True))
    j.update_order("kb-3", status="cancelled", reason="expired at s=140")
    assert [o["client_id"] for o in j.open_orders()] == ["kb-2"]
    assert len(j.fills()) == 2 and j.fills()[0]["ticker"] == "T1"


def test_events(j):
    j.event("info", "start", "paper mode")
    j.event("error", "ws", "disconnected")
    assert [e["kind"] for e in j.events()] == ["ws", "start"]
    assert [e["kind"] for e in j.events(min_level="warn")] == ["ws"]


def test_export_shape_matches_sheet(j, tmp_path):
    m = mk()
    j.record_market_open(m, range_2h=0.0123)
    j.record_lot(Lot("a", m.ticker, "y", 200, 0.95, 0.67, 0.665, OPEN + 131), mny=0.00161, th=0.0015)
    j.update_lot("a", exit="tp", exit_px=0.997, exit_ts=OPEN + 600, fee_out=0.0)
    j.record_lot(Lot("b", m.ticker, "y", 100, 0.96, 0.27, 0.672, OPEN + 300))
    j.update_lot("b", exit="settle_win", exit_px=1.0, exit_ts=OPEN + 900)
    j.record_market_close(m.ticker, True)
    sk = mk(OPEN + 900, "KXBTC15M-26OCT191630-30")
    j.record_market_open(sk, skipped="2-day sweep")
    j.record_market_open(mk(OPEN + 1800, "KXBTC15M-26OCT191645-45"))  # no trades -> not listed

    p = j.export_dashboard_json(tmp_path / "dashboard.json", meta={"mode": "paper"})
    d = json.loads(p.read_text())
    assert set(d) >= {"m", "e", "meta", "ev"} and d["meta"]["mode"] == "paper"
    assert len(d["m"]) == 2
    sheet_keys = {"day", "window_pt", "ticker", "range2h", "side", "strike", "result", "entries", "contracts",
                  "avg_buy", "first_entry", "stopped", "sold995", "settled", "pnl", "skip", "d", "cap", "held_s"}
    r, s = d["m"]
    assert sheet_keys <= set(r) and sheet_keys <= set(s)
    assert r["day"] == "Mon Oct 19" and r["window_pt"] == "1:00 PM" and r["d"] == "2026-10-19"
    assert r["range2h"] == 1.23 and r["side"] == "YES (up)" and r["result"] == "YES"
    assert r["entries"] == 2 and r["contracts"] == 300 and r["sold995"] == 1 and r["settled"] == 1
    assert r["avg_buy"] == pytest.approx((200 * 0.95 + 100 * 0.96) / 300, abs=1e-4)
    assert r["first_entry"] == "2:11"
    assert r["pnl"] == pytest.approx(round(200 * 0.997 - 190.67 + 100 - 96.27, 2))
    assert r["cap"] == pytest.approx(round(190.67 + 96.27, 2))  # both open at once
    assert r["held_s"] == pytest.approx(900 - 131)
    assert s["skip"] == 1 and s["side"] == "skipped: 2-day sweep" and s["entries"] == 0
    e = d["e"][m.ticker]
    assert len(e) == 2 and len(e[0]) == 12
    assert e[0][:4] == [1, "2:11", "Y", 200] and e[0][4] == 0.161 and e[0][5] == 0.15
    assert e[0][8] == "sold 99.7c" and e[0][9] == "10:00" and e[1][8] == "settled $1"


def test_export_open_lot(j, tmp_path):
    m = mk()
    j.record_market_open(m)
    j.record_lot(Lot("a", m.ticker, "n", 100, 0.9, 0.63, 0.63, OPEN + 200))
    d = j.dashboard_data(now=OPEN + 300)
    r = d["m"][0]
    assert r["open"] == 1 and r["pnl"] == 0.0 and r["result"] is None and r["side"] == "NO (down)"
    assert d["e"][m.ticker][0][8] == "open" and d["e"][m.ticker][0][11] is None
    assert d["meta"]["open_lots"] == 1


def test_thread_safety(j):
    def work(k):
        for i in range(50):
            j.event("info", "t", f"{k}-{i}")
            j.record_lot(Lot(f"{k}-{i}", "T", "y", 1, 0.5, 0.0, 0.35, OPEN))
    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(j.events(1000)) == 200 and len(j.lots()) == 200


def test_update_lot_rejects_unknown(j):
    with pytest.raises(ValueError):
        j.update_lot("a", bogus=1)
