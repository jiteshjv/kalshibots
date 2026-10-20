import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("momentum_audit", Path(__file__).parents[1]/"scripts"/"momentum_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_fee_quantum_and_order_rounding():
    assert audit.fee(.5, 5, .07) == .09
    assert audit.fee(.5, 5, .07, ".0001") == .0875


def test_touch_does_not_imply_through_fill():
    row = {"y": [.94, 10, 9.6], "n": [9, 0, 0]}
    assert audit.exit_fill(row, "y", .95, 5, "touch")
    assert not audit.exit_fill(row, "y", .95, 5, "through")
    assert not audit.exit_fill(row, "y", .95, 11, "touch")


def fixture():
    close = audit.timestamp("2026-10-15T12:15:00Z")
    opened = close-900
    market = dict(ticker="test", close_time="2026-10-15T12:15:00Z", floor_strike=100, result="yes")
    prices = {t:100 for t in range(opened-60, close)}
    prices.update({t:100.2 for t in range(opened, close)})
    return opened, market, prices


def test_future_only_tape_cannot_create_current_signal():
    opened, market, prices = fixture()
    row = {"y":[.80, 5, 4], "n":[9, 0, 0]}
    tape = {opened+121:row}
    result, _ = audit.replay(market, prices, tape, basis="raw", exit_mode="hold", slippage=0)
    assert result is None


def test_future_fill_over_limit_is_rejected():
    opened, market, prices = fixture()
    before = {"y":[.80, 5, 4], "n":[9, 0, 0]}
    after = {"y":[.86, 5, 4.3], "n":[9, 0, 0]}
    tape = {opened+119:before, opened+121:after}
    result, _ = audit.replay(market, prices, tape, basis="raw", exit_mode="hold", slippage=.01)
    assert result is None


def test_delayed_entry_and_settlement_fee_accounting():
    opened, market, prices = fixture()
    row = {"y":[.80, 5, 4], "n":[9, 0, 0]}
    tape = {opened+119:row, opened+121:row}
    result, _ = audit.replay(market, prices, tape, basis="raw", exit_mode="hold", slippage=0)
    assert result["decision_ts"] == opened+120
    assert result["entry_proxy_end_ts"] == opened+124
    assert abs(result["pnl"]-.94) < 1e-9
