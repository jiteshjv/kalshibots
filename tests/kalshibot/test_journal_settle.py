from kalshibot.journal import Journal
from kalshibot.types import Lot, MarketInfo


def test_unsettled_and_orphan_settlement(tmp_path):
    j = Journal(tmp_path)
    j.record_market_open(MarketInfo("KXBTC15M-A", 1000.0, 1900.0, 100.0))
    j.record_market_open(MarketInfo("KXBTC15M-B", 1900.0, 2800.0, 100.0))
    j.record_lot(Lot("A-L01", "KXBTC15M-A", "n", 100, 0.95, 0.1, 0.665, 1500.0))
    j.record_lot(Lot("A-L02", "KXBTC15M-A", "y", 100, 0.60, 0.1, 0.42, 1600.0, exit="stop", exit_px=0.40, exit_ts=1650.0))
    assert [m["ticker"] for m in j.unsettled_markets(2000.0)] == ["KXBTC15M-A"]
    assert j.settle_orphans("KXBTC15M-A", yes_won=False) == 1          # only the open lot; NO won -> $1
    j.record_market_close("KXBTC15M-A", False)
    lots = {r["lot_id"]: r for r in j.lots("KXBTC15M-A")}
    assert lots["A-L01"]["exit"] == "settle_win" and lots["A-L01"]["exit_px"] == 1.0
    assert lots["A-L02"]["exit"] == "stop"
    assert j.unsettled_markets(3000.0)[0]["ticker"] == "KXBTC15M-B"
    j.close()


def test_market_note_excludes_it_from_dashboard_totals(tmp_path):
    import sqlite3
    from kalshibot.journal import Journal
    from kalshibot.types import Lot, MarketInfo
    db = sqlite3.connect(tmp_path / "journal.sqlite")                  # an old journal without the note column
    db.execute("CREATE TABLE markets(ticker TEXT PRIMARY KEY, open_ts REAL, close_ts REAL, strike REAL, result TEXT,"
               " skipped TEXT, range_2h REAL, closed_ts REAL)"); db.commit(); db.close()
    j = Journal(tmp_path)
    m = MarketInfo("KXBTC15M-T1", 1792540000.0, 1792540900.0, 80000.0)
    j.record_market_open(m)
    j.record_lot(Lot(lot_id="KXBTC15M-T1-L01", ticker=m.ticker, side="y", qty=100, buy=0.9, fee_in=0.5, stop=0.67,
                     entry_ts=m.open_ts + 200, how="limit", exit="tp", exit_px=0.997, exit_ts=m.open_ts + 700))
    assert j.set_note([m.ticker], "BRTI bug, ignore") == 1
    row = j.dashboard_data()["m"][0]
    assert row["note"] == "BRTI bug, ignore" and row["excluded"] == 1 and row["entries"] == 1
