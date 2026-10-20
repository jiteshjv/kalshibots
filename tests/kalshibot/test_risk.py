import pytest

from kalshibot.config import LIVE_CONFIRM_PHRASE, RiskCfg, Settings
from kalshibot.risk import LiveModeRefused, RiskGuard, live_mode_ok, utc_day
from kalshibot.types import Book, BtcTick, OrderIntent, SecondView

NOW = 1_760_875_200.0          # 2025-10-19 12:00 UTC


def view(btc_age=0.5, book_age=0.5, stale=False, brti=None, comp=None, book=True, btc=True):
    t = BtcTick(ts=NOW - btc_age, price=100000.0, brti=brti, composite=comp, stale=stale) if btc else None
    b = Book(ts=NOW - book_age, yes_bids=[(0.5, 10)], no_bids=[(0.4, 10)]) if book else None
    return SecondView(ts=NOW, s=200, btc=t, book=b, ask={}, bid={}, mark={})


ENTRY = OrderIntent(kind="limit_buy", side="y", qty=100, price=0.95, reason="entry")
FALLBACK = OrderIntent(kind="market_buy", side="y", qty=100, price=0.985, reason="entry_market_fallback")
EXITS = [OrderIntent(kind="market_sell", side="y", qty=100, price=0.01, reason="stop"),
         OrderIntent(kind="limit_sell", side="y", qty=100, price=0.997, reason="tp"),
         OrderIntent(kind="cancel", side="y", qty=0, client_id="x", reason="close_cancel")]


def guard(tmp_path, pnl=0.0, open_n=0, **cfg):
    days = []

    def realized(day):
        days.append(day)
        return pnl

    g = RiskGuard(RiskCfg(**cfg), data_dir=tmp_path, realized_pnl=realized, open_orders=lambda: open_n,
                  clock=lambda: NOW)
    g.days = days
    return g


def test_all_clear(tmp_path):
    g = guard(tmp_path)
    assert g.allow(ENTRY, view()) == (True, "")
    assert g.allow(FALLBACK, view()) == (True, "")
    assert g.days[0] == utc_day(NOW) == "2025-10-19"


@pytest.mark.parametrize("kw,v,reason", [
    (dict(pnl=-1500.0), {}, "daily_loss"),
    (dict(pnl=-2000.0), {}, "daily_loss"),
    (dict(btc_marker=1), dict(btc=False), "stale_btc"),
    (dict(), dict(stale=True), "stale_btc"),
    (dict(), dict(btc_age=3.5), "stale_btc"),
    (dict(), dict(book=False), "no_book"),
    (dict(), dict(book_age=3.01), "stale_book"),
    (dict(), dict(brti=100000.0, comp=100025.5), "brti_divergence"),
    (dict(open_n=60), {}, "max_open_orders"),
])
def test_entry_blocks(tmp_path, kw, v, reason):
    kw.pop("btc_marker", None)
    g = guard(tmp_path, **kw)
    assert g.allow(ENTRY, view(**v)) == (False, reason)
    assert g.allow(FALLBACK, view(**v)) == (False, reason)
    for x in EXITS:                                           # never blocks stops / TP / cancels
        assert g.allow(x, view(**v)) == (True, "")
    assert g.stats()["blocked"] == {reason: 2} and g.last_reason == reason and g.stats()["passed_exits"] == 3


def test_near_limits_pass(tmp_path):
    g = guard(tmp_path, pnl=-1499.99, open_n=59)
    assert g.allow(ENTRY, view(btc_age=3.0, book_age=3.0, brti=100000.0, comp=100025.0)) == (True, "")


def test_kill_file(tmp_path):
    g = guard(tmp_path)
    assert not g.must_flatten()
    (tmp_path / "KILL").write_text("")
    assert g.must_flatten()
    assert g.allow(ENTRY, view()) == (False, "kill_file")
    assert all(g.allow(x, view()) == (True, "") for x in EXITS)
    assert g.stats()["kill_file"] is True


def test_kill_file_absolute_path(tmp_path):
    k = tmp_path / "elsewhere" / "STOP"
    g = guard(tmp_path / "data", kill_file=str(k))
    k.parent.mkdir()
    assert not g.must_flatten()
    k.write_text("")
    assert g.must_flatten()


def test_journal_fallbacks(tmp_path):
    class J:
        def realized_pnl(self, day):
            return -5000.0

        def open_orders(self):
            return [1, 2]

    g = RiskGuard(RiskCfg(), J(), data_dir=tmp_path, clock=lambda: NOW)
    assert g.open_order_count() == 2
    assert g.allow(ENTRY, view()) == (False, "daily_loss")
    g2 = RiskGuard(RiskCfg(), None, data_dir=tmp_path, clock=lambda: NOW)
    assert g2.allow(ENTRY, view()) == (True, "")


def test_live_mode_confirm():
    assert live_mode_ok(RiskCfg())[0] is False
    assert live_mode_ok(RiskCfg(mode="live", live_confirm="yes"))[0] is False
    assert live_mode_ok(RiskCfg(mode="live", live_confirm=LIVE_CONFIRM_PHRASE)) == (True, "")
    assert live_mode_ok(Settings(risk=RiskCfg(mode="live", live_confirm=LIVE_CONFIRM_PHRASE)))[0] is True
    with pytest.raises(LiveModeRefused):
        RiskGuard(RiskCfg(mode="live", live_confirm="nope"))
    with pytest.raises(ValueError):
        RiskGuard(RiskCfg(mode="yolo"))
    RiskGuard(RiskCfg(mode="live", live_confirm=LIVE_CONFIRM_PHRASE))
