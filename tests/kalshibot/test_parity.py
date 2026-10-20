"""Parity of Strategy + BacktestBroker with scripts/her_trades.py (results/v8_mixed_f_66d_*.csv).

Default: a deterministic sample (every 10th market of the 66-day set, ~630 markets, ~370 of them traded) checked
market by market (entries, P&L within $0.05), lot by lot (buy price, exit kind/price, P&L) and for the filter skips.
Full 66-day run (all 6,321 markets, ~1 min): KB_FULL_PARITY=1.
"""
import os
from pathlib import Path

import pandas as pd
import pytest

from kalshibot.backtest.replay import ResearchData, Replayer, lot_pnl

ROOT = Path(__file__).resolve().parents[2]
DATA, RES = ROOT / "data", ROOT / "results"
PRE = RES / "v8_mixed_f_66d"

pytestmark = pytest.mark.skipif(
    not ((DATA / "composite_1s.npy").exists() and (DATA / "settled.json").exists()
         and Path(f"{PRE}_markets.csv").exists()),
    reason="research data / reference results not present")

EXIT = {"sold 99.7c": "tp", "stopped": "stop", "settled $1": "settle_win", "settled $0": "settle_loss"}
HOW = {"limit": "limit", "market late": "market"}


@pytest.fixture(scope="module")
def data():
    return ResearchData(DATA)


@pytest.fixture(scope="module")
def ref():
    M = pd.read_csv(f"{PRE}_markets.csv").set_index("ticker")
    E = pd.read_csv(f"{PRE}_entries.csv")
    S = pd.read_csv(f"{PRE}_skipped.csv").set_index("ticker")
    return M, E, S


def _check(results, tickers, ref, lots_too=True):
    M, E, S = ref
    Eg = {t: g.sort_values("entry_no") for t, g in E.groupby("ticker")} if lots_too else {}
    bad = []
    traded = 0
    for r in results:
        if not r.traded:
            if r.ticker in M.index:
                bad.append((r.ticker, "not traded", r.skipped))
            if r.skipped and "sweep" in r.skipped and (r.ticker not in S.index or S.loc[r.ticker, "why"] != r.skipped):
                bad.append((r.ticker, "skip reason", r.skipped))
            continue
        traded += 1
        if r.ticker not in M.index:
            bad.append((r.ticker, "extra market", r.entries, r.pnl))
            continue
        row = M.loc[r.ticker]
        if row.entries != r.entries or abs(row.pnl - r.pnl) > 0.05:
            bad.append((r.ticker, "market", row.entries, r.entries, row.pnl, r.pnl))
            continue
        if lots_too:
            g = Eg[r.ticker]
            for L, (_, e) in zip(r.lots, g.iterrows()):
                ok = (round(L.buy, 4) == e.buy and L.qty == e.n and HOW[e.how] == L.how and EXIT[e.exit] == L.exit
                      and abs(round(L.exit_px, 4) - e.exit_px) < 1e-9 and abs(lot_pnl(L) - e.pnl) < 0.005)
                if not ok:
                    bad.append((r.ticker, "lot", e.entry_no, L))
                    break
    missing = set(M.index[M.index.isin(tickers)]) - {r.ticker for r in results if r.traded}
    bad += [(t, "missing") for t in missing]
    sk = set(S.index[S.index.isin(tickers)]) - {r.ticker for r in results if r.skipped and "sweep" in r.skipped}
    bad += [(t, "skip missing") for t in sk]
    return bad, traded


def test_sample_parity(data, ref):
    M = ref[0]
    ws = data.windows[::10]
    tickers = [w.ticker for w in ws]
    res = Replayer(data).run(ws)
    bad, traded = _check(res, tickers, ref)
    assert traded >= 300
    assert not bad, bad[:10]
    mine = sum(r.pnl for r in res if r.traded)
    theirs = M.loc[M.index.isin(tickers), "pnl"].sum()
    assert mine == pytest.approx(theirs, abs=0.05)


@pytest.mark.skipif(os.environ.get("KB_FULL_PARITY") != "1", reason="slow: set KB_FULL_PARITY=1")
def test_full_66d_parity(data, ref):
    M = ref[0]
    res = Replayer(data).run()
    bad, traded = _check(res, [w.ticker for w in data.windows], ref)
    total = sum(r.pnl for r in res if r.traded)
    assert traded == len(M)
    assert abs(total - M.pnl.sum()) <= 0.001 * abs(M.pnl.sum())
    assert not bad, bad[:10]
