"""Paper bot vs backtest, market by market, for one Pacific-time day (BTC).

    python3 -P scripts/paper_vs_backtest.py 2026-10-20                     # data folder: data_daily/2026-10-20
    python3 -P scripts/paper_vs_backtest.py 2026-10-20 --data data_oct20   # use an existing data folder
    options: --config config.paper.yaml  --journal ~/kalshibot-data/journal.sqlite  --download (fetch the day's data first)

1. Reads the paper bot's config (+ the dashboard settings.json saved next to its journal) and turns it into the
   backtest's settings (her_trades.py env vars), so both run the same rules.
2. Runs the backtest on the day's markets (the data folder needs settled.json, ktr/ and prices; --download builds
   it with recent_dl.py and stitches the earlier price history in front so the sweep checks see 30+ days).
3. Compares each market: side, entries, contracts, stopped, P&L. Markets the paper bot skipped for news, risk blocks or
   restarts are listed separately. Writes results/daily/<date>_compare.csv and .txt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PT = ZoneInfo("America/Los_Angeles")
sys.path.insert(0, str(ROOT / "src"))


def backtest_env(cfg_path: str, data_dir: Path) -> tuple[dict, str, str]:
    from kalshibot import settings_store
    from kalshibot.config import Settings
    s = Settings.load(cfg_path)
    settings_store.apply(s, settings_store.load(data_dir))
    c = s.strategy
    env = {"STOPX": c.stop_pct, "LEADSTOP": c.stop_lead, "JUMP": c.jump_pause, "TP": c.take_profit,
           "MAXR": c.max_range_2h, "MAXSPREAD": c.max_spread, "LIM": c.limit_offset, "HYB": "1" if c.limit_fallback else "0",
           "SIZE_E": c.size_early, "SIZE_L": c.size_late, "MAXHELD": c.max_held, "STEP": c.check_every_s,
           "FIRST": c.first_check_s, "MNY_START": c.mny_start, "MNY_FLOOR": c.mny_floor,
           "CONFIRM": "bid" if c.stop_recheck else "off", "FVSTOP": "2" if c.stop_fair else "0"}
    if c.sweep_days >= 3 and c.sweep_max_days >= 30 and c.sweep_4h_m60 == 0:
        env["SWEEPRULE"] = "b314"
    return {k: str(v) for k, v in env.items()}, c.sizing, "on" if c.filters else "off"


def journal_day(path: Path, day: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    db = sqlite3.connect(path)
    M = pd.read_sql("select * from markets", db)
    L = pd.read_sql("select * from lots", db)
    ev = pd.read_sql("select ts, kind, message from events", db)
    M["o"] = pd.to_datetime(M.open_ts, unit="s", utc=True).dt.tz_convert(PT)
    M = M[M.o.dt.strftime("%Y-%m-%d") == day]
    return M, L[L.ticker.isin(M.ticker)], ev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--data", default=None)
    ap.add_argument("--config", default=str(ROOT / "config.paper.yaml"))
    ap.add_argument("--journal", default=os.path.expanduser("~/kalshibot-data/journal.sqlite"))
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args(argv)
    day = a.date
    data = ROOT / (a.data or f"data_daily/{day}")
    if a.download:
        data.mkdir(parents=True, exist_ok=True)
        start = dt.datetime.fromisoformat(day).replace(tzinfo=PT) - dt.timedelta(hours=2)
        subprocess.run([sys.executable, "-P", "-u", str(ROOT / "scripts/recent_dl.py"),
                        start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")], cwd=data, check=True)
        subprocess.run([sys.executable, "-P", str(ROOT / "scripts/stitch_history.py"), str(data)], check=True)
    if not (data / "settled.json").exists():
        print(f"no data in {data}: run with --download (or --data <folder>)")
        return 2
    jpath = Path(a.journal)
    env, sizing, filt = backtest_env(a.config, jpath.parent)
    if not (data / "cands.pkl").exists():
        ts = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        pd.DataFrame({"op": sorted(int(ts(x["close_time"])) - 900 for x in json.load(open(data / "settled.json")))}).to_pickle(data / "cands.pkl")
    out = ROOT / "results" / "daily"
    out.mkdir(parents=True, exist_ok=True)
    pre = out / f"{day}_bt"
    d0 = dt.datetime.fromisoformat(day).replace(tzinfo=PT)
    utc_days = sorted({(d0 + dt.timedelta(hours=h)).astimezone(dt.timezone.utc).strftime("%Y-%m-%d") for h in (0, 24)})
    r = subprocess.run([sys.executable, "-P", str(ROOT / "scripts/her_trades.py"), "0", "composite", "0", str(pre), sizing, filt],
                       cwd=data, env={**os.environ, **env, "START_DATE": utc_days[0], "END_DATE": utc_days[-1]},
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:])
        return 3
    B = pd.read_csv(f"{pre}_markets.csv")
    BE = pd.read_csv(f"{pre}_entries.csv") if os.path.getsize(f"{pre}_entries.csv") > 2 else pd.DataFrame(columns=["ticker", "n", "exit"])
    B["o"] = pd.to_datetime(B.day + " 2026 " + B.window_pt, format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT)
    B = B[B.o.dt.strftime("%Y-%m-%d") == day].set_index("ticker")
    M, L, ev = journal_day(jpath, day)
    have = {x["ticker"] for x in json.load(open(data / "settled.json"))}
    rows = []
    for m in M.sort_values("o").itertuples():
        l = L[L.ticker == m.ticker]
        lp = (l.exit_px - l.buy) * l.qty - l.fee_in - l.fee_out
        p = dict(ticker=m.ticker, window=m.o.strftime("%-I:%M %p") if os.name != "nt" else m.o.strftime("%I:%M %p"),
                 paper_side=("YES" if (l.side.iloc[0] == "y") else "NO") if len(l) else "",
                 paper_entries=len(l), paper_ct=int(l.qty.sum()), paper_stopped=int(l[l.exit == "stop"].qty.sum()),
                 paper_pnl=round(float(lp.sum()), 2) if len(l) else 0.0, paper_skip=m.skipped or "")
        if m.ticker in B.index:
            b = B.loc[m.ticker]; be = BE[BE.ticker == m.ticker]
            p.update(bt_side=str(b.side).split(" ")[0], bt_entries=int(b.entries), bt_ct=int(b.contracts),
                     bt_stopped=int(be[be.exit == "stopped"].n.sum()) if len(be) else 0, bt_pnl=round(float(b.pnl), 2))
        else:
            p.update(bt_side="", bt_entries=0, bt_ct=0, bt_stopped=0, bt_pnl=0.0)
        both = p["paper_entries"] > 0 and p["bt_entries"] > 0
        p["match"] = ("no backtest data" if m.ticker not in have else
                      "both traded, same side" if both and p["paper_side"] == p["bt_side"] else
                      "both traded, OPPOSITE side" if both else
                      "neither traded" if not p["paper_entries"] and not p["bt_entries"] else
                      "only paper traded" if p["paper_entries"] else "only backtest traded")
        rows.append(p)
    D = pd.DataFrame(rows)
    D.to_csv(out / f"{day}_compare.csv", index=False)
    lines = [f"Paper vs backtest, {day} PT ({len(D)} markets the paper bot saw; backtest settings from {Path(a.config).name}"
             f" + dashboard settings: {sizing}, filters {filt}, {', '.join(f'{k}={v}' for k, v in env.items())})", ""]
    for k, g in D.groupby("match"):
        lines.append(f"{k:28s} {len(g):3d} markets | paper ${g.paper_pnl.sum():+9,.2f} | backtest ${g.bt_pnl.sum():+9,.2f}")
    t = D[((D.paper_entries > 0) | (D.bt_entries > 0)) & (D.match != "no backtest data")]
    C = D[D.match != "no backtest data"]
    lines += ["", f"COMPARED ({len(C)} markets)  paper ${C.paper_pnl.sum():+,.2f} ({int(C.paper_ct.sum()):,} contracts) | backtest "
              f"${C.bt_pnl.sum():+,.2f} ({int(C.bt_ct.sum()):,} contracts)", "", "Markets that differ (paper skip reason in brackets):"]
    diff = t[(t.match != "both traded, same side") | ((t.paper_pnl - t.bt_pnl).abs() > 0.25 * t[["paper_pnl", "bt_pnl"]].abs().max(axis=1) + 5)]
    for r_ in diff.itertuples():
        lines.append(f"  {r_.window:>8s} {r_.match:26s} paper {r_.paper_side or '-':3s} {r_.paper_entries:2d} entries ${r_.paper_pnl:+8.2f}"
                     f"  backtest {r_.bt_side or '-':3s} {r_.bt_entries:2d} entries ${r_.bt_pnl:+8.2f}" + (f"  [{r_.paper_skip}]" if r_.paper_skip else ""))
    txt = "\n".join(lines)
    (out / f"{day}_compare.txt").write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
