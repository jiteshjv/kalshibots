"""Install kalshibot for one person: a BTC bot and/or a gold bot that share that person's Kalshi key.

Run from the unzipped bundle folder (it contains app/ and this file):
    python install.py --person jitesh --port 8787            -> BTC on 8787, gold on 8788
    python install.py --person wife   --port 8789            -> BTC on 8789, gold on 8790
Options: --bots btc,gold (default both)  --size 1 (contracts per entry)  --root C:\\kalshibot (default: C:\\kalshibot on
Windows, ~/kalshibot elsewhere)  --service (Windows: also install + start NSSM services; nssm.exe must be on PATH).

Layout it creates (re-running never overwrites a .env, a key, data or an existing config.yaml; use --force-config):
    <root>/app/src/kalshibot, <root>/app/run.py           shared code (always refreshed)
    <root>/<person>/.env, <root>/<person>/secrets/        that person's Kalshi Key ID + private key (you add them)
    <root>/<person>/btc|gold/config.yaml, data/, logs/    one folder per bot
Everything starts in PAPER mode. Live: edit that bot's config.yaml (see the comment at its end).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def btc_config(size: int, port: int, data: Path, record: bool = True) -> str:
    return f"""# BTC 15-min (KXBTC15M): the RECOMMENDED setup, {size} contract(s) per entry. Starts in PAPER mode.
strategy:
  series: KXBTC15M
  sizing: equal         # same size every entry
  size_late: {size}
  max_held: {50 * size}         # max contracts held per market (50 entries)
  stop_pct: 0.25        # stop an entry 25% below its buy price ...
  stop_lead: 0.0003     # ... AND BTC within 0.03% of the strike
  stop_recheck: true
  jump_pause: 0.005     # no new entries for 15 min after a 1-minute BTC move above 0.5%
  sweep_days: 3         # skip only after a break of a 3- to 14-day high/low on our side
  sweep_max_days: 30
  sweep_4h_m60: 0
risk:
  mode: paper           # paper | live
  daily_loss_limit: {15 * size}   # $: no new entries for the rest of the UTC day after this loss
  news_mode: window     # NFP / CPI / FOMC: no new entries from the release ...
  news_events: jobs,cpi,fomc
  news_before_min: 0
  news_after_min: 60    # ... until 60 minutes after it
io:
  data_dir: {data.as_posix()}
  record: {"true" if record else "false"}          # order book recording (every change, <= every 0.1 s, about 70 MB/day for BTC)
  dashboard_port: {port}
# LIVE (real money): set risk.mode to live and add this line under risk:
#   live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"
"""


def gold_config(size: int, port: int, data: Path, record: bool = True) -> str:
    return f"""# GOLD 15-min (KXGOLD15M): Version C + all gold filters, {size} contract(s) per entry. Starts in PAPER mode.
# Gold price: Hyperliquid xyz:GOLD oracle (free), Swissquote as backup; Pyth if PYTH_API_KEY is set in .env.
strategy:
  series: KXGOLD15M
  sizing: equal
  size_late: {size}
  max_held: {50 * size}
  max_range_2h: 0.012   # skip if gold's 2-hour range is above 1.2%
  filters: true         # 2-day+ sweep filter ...
  sweep_4h_m60: 1.0     # ... the BTC 4h rule is off for gold
  skip_hours_pt: "7-9"  # skip markets starting 7:00-8:59 AM PT
  mny_start: 0.00132    # Version C: 0.132% at minute 2 ...
  mny_floor: 0.00044    # ... to 0.044% by minute 15
  stop_pct: 0.20        # stop 20% below the buy price AND gold within 0.01% of the strike
  stop_lead: 0.0001
  take_profit: 0.999    # resting sell at 99.9c
risk:
  mode: paper           # paper | live
  daily_loss_limit: {15 * size}
  stale_btc_s: 6.0      # gold price older than 6 s -> no new entries
  news_mode: window
  news_windows: "jobs:15:60,cpi:15:60,claims:15:60,fomc:0:60"   # NFP/CPI/claims 5:15-6:30 AM PT, FOMC 11-12 PT
io:
  data_dir: {data.as_posix()}
  record: {"true" if record else "false"}          # order book recording
  book_poll_s: 0.5
  dashboard_port: {port}
# LIVE (real money): set risk.mode to live and add this line under risk:
#   live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"
"""


ENV_TEMPLATE = """# {person}'s secrets (never share, never commit)
KALSHI_API_KEY_ID=
KALSHIBOT_DASH_TOKEN={token}
# optional: Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", required=True, help="folder name for this person, e.g. jitesh")
    ap.add_argument("--port", type=int, required=True, help="dashboard port of the first bot (the next bot gets +1)")
    ap.add_argument("--bots", default="btc,gold")
    ap.add_argument("--size", type=int, default=1, help="contracts per entry (1 = plumbing test, 10 ~ backtest-like)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--no-record", action="store_true", help="do not record the order book (use for the second person: same markets)")
    ap.add_argument("--service", action="store_true", help="Windows: install and start NSSM services")
    ap.add_argument("--force-config", action="store_true", help="rewrite existing config.yaml files")
    a = ap.parse_args(argv)
    root = Path(a.root or (r"C:\kalshibot" if os.name == "nt" else "~/kalshibot")).expanduser()
    bots = [b.strip() for b in a.bots.split(",") if b.strip()]
    for b in bots:
        if b not in ("btc", "gold"):
            ap.error(f"unknown bot {b!r} (btc, gold)")
    # shared code
    app = root / "app"
    if (app / "src" / "kalshibot").exists():
        shutil.rmtree(app / "src" / "kalshibot")
    shutil.copytree(HERE / "app" / "src" / "kalshibot", app / "src" / "kalshibot",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(HERE / "app" / "run.py", app / "run.py")
    # person
    person = root / a.person
    (person / "secrets").mkdir(parents=True, exist_ok=True)
    env = person / ".env"
    if not env.exists():
        import secrets
        env.write_text(ENV_TEMPLATE.format(person=a.person, token=secrets.token_urlsafe(24)))
    made = []
    for i, b in enumerate(bots):
        d = person / b
        (d / "data").mkdir(parents=True, exist_ok=True)
        (d / "logs").mkdir(exist_ok=True)
        cal = d / "data" / "news_calendar.csv"
        shutil.copy2(HERE / "news_calendar.csv", cal)                 # calendar always refreshed
        cfg = d / "config.yaml"
        port = a.port + i
        if a.force_config or not cfg.exists():
            cfg.write_text((btc_config if b == "btc" else gold_config)(a.size, port, d / "data", not a.no_record))
        made.append((b, d, port))
    key = person / "secrets" / "kalshi.pem"
    print(f"\nInstalled for {a.person} in {person}")
    for b, d, port in made:
        print(f"  {b:4s}: {d}   dashboard http://127.0.0.1:{port}")
    print("\nNext:")
    print(f"  1. Put {a.person}'s Kalshi private key at {key}")
    print(f"  2. Put {a.person}'s Kalshi Key ID in {env} (KALSHI_API_KEY_ID=...). The dashboard password is already there.")
    print(f"  3. Check:  python {app / 'run.py'} {made[0][1]} --check")
    py = sys.executable
    for b, d, port in made:
        name = f"kalshibot-{a.person}-{b}"
        cmd = [["nssm", "install", name, py, str(app / "run.py"), str(d)],
               ["nssm", "set", name, "AppDirectory", str(d)],
               ["nssm", "set", name, "AppStopMethodConsole", "30000"],
               ["nssm", "set", name, "AppRestartDelay", "5000"],
               ["nssm", "set", name, "AppStdout", str(d / "logs" / "service.out.txt")],
               ["nssm", "set", name, "AppStderr", str(d / "logs" / "service.err.txt")]]
        if a.service and os.name == "nt" and shutil.which("nssm"):
            for c in cmd:
                subprocess.run(c, check=False)
            subprocess.run(["nssm", "start", name], check=False)
            print(f"  service {name}: installed and started")
        else:
            print(f"  service {name} (Windows, admin PowerShell):")
            for c in cmd:
                print("     " + " ".join(f'"{x}"' if " " in x else x for x in c))
            print(f"     nssm start {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
