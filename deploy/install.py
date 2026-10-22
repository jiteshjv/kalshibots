"""Install kalshibot for one person: a BTC bot and/or a gold bot that share that person's Kalshi key.

Run from the unzipped bundle folder (it contains app/ and this file):
    python install.py --person jitesh --port 8787            -> BTC on 8787, gold on 8788
    python install.py --person wife   --port 8789            -> BTC on 8789, gold on 8790
Options: --bots btc,gold (default both)  --size 1 (contracts per entry)  --root C:\\kalshibot (default: C:\\kalshibot on
Windows, ~/kalshibot elsewhere)  --service (also install + start the services: Windows NSSM, nssm.exe on PATH; Linux systemd via sudo)
--s3-bucket NAME (recording bots upload each finished hour file to s3://NAME/vps/<bot>/rec/...; AWS keys in .env;
with --update give --person too: only that person's bots upload, so the same markets are never pushed twice).

Layout it creates (re-running never overwrites a .env, a key, data or an existing config.yaml; use --force-config):
    <root>/app/src/kalshibot, <root>/app/run.py           shared code (always refreshed)
    <root>/<person>/.env, <root>/<person>/secrets/        that person's Kalshi Key ID + private key (you add them)
    <root>/<person>/btc|gold/config.yaml, data/, logs/    one folder per bot
Everything starts in PAPER mode. Live: edit that bot's config.yaml (see the comment at its end).
"""
from __future__ import annotations

import argparse
import getpass
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
  stop_fair: true       # also stop when BTC's fair value for our side is below the stop price (backtest +$7.7K)
  jump_pause: 0.005     # no new entries for 15 min after a 1-minute BTC move above 0.5%
  sweep_days: 3         # skip only after a break of a 3- to 14-day high/low on our side
  sweep_max_days: 30
  sweep_4h_m60: 0
  risk_max: 0.15        # her risk check: enter only if the chance BTC ends on the wrong side is <= 15% ...
  mny_start: 0          # ... instead of the moneyness line (off)
  mny_floor: 0
  limit_offset: 0.003   # entry bid 0.3c under the ask above 90c (whole cents 10-90c: 1c under)
  entry_wait_s: 5       # unfilled bid -> market after 5 s
  fast_stop_ms: 100     # check stops every 100 ms, sell at once (her book, Jun-Oct: +$55K vs once a second)
risk:
  mode: paper           # paper | demo (Kalshi demo exchange, fake money) | live
  daily_loss_limit: {15 * size}   # $: no new entries for the rest of the UTC day after this loss
  news_mode: window     # NFP / CPI / FOMC: no new entries from the release ...
  news_events: jobs,cpi,fomc
  news_before_min: 0
  news_after_min: 60    # ... until 60 minutes after it
io:
  data_dir: {data.as_posix()}
  record: {"true" if record else "false"}          # record every Kalshi book message, exchange trade and 100 ms prices (about 1 GB/day for BTC, gzip)
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
  mny_start: 0.00115    # 0.115% at minute 2 ... (2026-10-20: backtest +32% vs Version C's 0.132% -> 0.044%)
  mny_floor: 0.00030    # ... to 0.030% by minute 15
  stop_pct: 0.20        # stop 20% below the buy price AND gold within 0.01% of the strike
  stop_lead: 0.0001
  take_profit: 0.999    # resting sell at 99.9c
  stop_recheck: true    # sell a stop only if the bid is still at or below the stop price
  limit_offset: 0.003   # entry bid 0.3c under the ask above 90c (whole cents 10-90c: 1c under)
  entry_wait_s: 5       # unfilled bid -> market after 5 s (2026-10-21, same as BTC)
  fast_stop_ms: 100     # check stops every 100 ms, sell at once (same as BTC)
risk:
  mode: paper           # paper | demo (Kalshi demo exchange, fake money) | live
  daily_loss_limit: {15 * size}
  stale_btc_s: 6.0      # gold price older than 6 s -> no new entries
  news_mode: window
  news_windows: "jobs:15:60,cpi:15:60,claims:15:60,fomc:0:60"   # NFP/CPI/claims 5:15-6:30 AM PT, FOMC 11-12 PT
io:
  data_dir: {data.as_posix()}
  record: {"true" if record else "false"}          # record every Kalshi book message and gold price (about 0.35 GB/day, gzip)
  book_poll_s: 0.2      # only used without a Kalshi key (with a key the book streams in real time)
  dashboard_port: {port}
# LIVE (real money): set risk.mode to live and add this line under risk:
#   live_confirm: "I UNDERSTAND THIS TRADES REAL MONEY"
"""


ENV_TEMPLATE = """# {person}'s secrets (never share, never commit)
KALSHI_API_KEY_ID=
# Kalshi DEMO exchange key (only for mode: demo; secrets/kalshi-demo.pem)
KALSHI_DEMO_API_KEY_ID=
KALSHIBOT_DASH_TOKEN={token}
# optional: Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
""" + "{aws}"

AWS_ENV = """# optional: upload the recordings to S3 (install.py --s3-bucket); an IAM user that may only write to that bucket
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-2
"""


SYSTEMD_UNIT = """[Unit]
Description=kalshibot {name}
After=network-online.target
Wants=network-online.target

[Service]
User={user}
WorkingDirectory={dir}
ExecStart={py} {run} {dir}
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=append:{dir}/logs/service.out.txt
StandardError=append:{dir}/logs/service.err.txt

[Install]
WantedBy=multi-user.target
"""


def add_aws_env(env: Path) -> None:
    """Add the AWS placeholders to an existing .env once (nothing else in it is read or changed)."""
    if env.exists() and "AWS_ACCESS_KEY_ID" not in env.read_text():
        with env.open("a") as fh:
            fh.write("\n" + AWS_ENV)
        print(f"  {env}: added AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY lines (fill them in)")


def unset_s3(cfg: Path) -> bool:
    lines = cfg.read_text().splitlines()
    keep = [l for l in lines if not l.strip().startswith(("s3_bucket:", "s3_prefix:"))]
    if len(keep) == len(lines):
        return False
    cfg.write_text("\n".join(keep) + "\n")
    return True


def set_s3(cfg: Path, bucket: str, prefix: str) -> bool:
    """Point a recording bot's config.yaml at S3 (io.s3_bucket / io.s3_prefix). Bots with record: false are left alone."""
    lines = cfg.read_text().splitlines()
    if not any(l.strip().startswith("record: true") for l in lines):
        return False
    lines = [l for l in lines if not l.strip().startswith(("s3_bucket:", "s3_prefix:"))]
    i = next(k for k, l in enumerate(lines) if l.rstrip() == "io:")
    lines[i + 1:i + 1] = [f"  s3_bucket: {bucket}          # finished hour files -> s3://{bucket}/{prefix}rec/<date>/<hour>.jsonl.gz",
                          f"  s3_prefix: {prefix}"]
    cfg.write_text("\n".join(lines) + "\n")
    return True


def copy_code(root: Path) -> Path:
    """Shared code: <root>/app/src/kalshibot + run.py + kb.py (always replaced by the bundle's)."""
    app = root / "app"
    if (app / "src" / "kalshibot").exists():
        shutil.rmtree(app / "src" / "kalshibot")
    shutil.copytree(HERE / "app" / "src" / "kalshibot", app / "src" / "kalshibot",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("run.py", "kb.py"):
        shutil.copy2(HERE / "app" / f, app / f)
    return app


def apply_sets(bots: list[Path], sets: list[str]) -> int:
    """--set btc:strategy.stop_fair=true -> each matching bot's data/settings.json (what the dashboard saves)."""
    sys.path.insert(0, str(HERE / "app" / "src"))
    from kalshibot import settings_store
    want: dict[str, dict] = {}
    for item in sets:
        kind, _, kv = item.partition(":")
        key, _, val = kv.partition("=")
        if kind not in ("btc", "gold", "all") or not key or not _:
            print(f"bad --set {item!r} (use btc|gold|all:section.name=value)")
            return 2
        clean, errs = settings_store.validate({key.strip(): val.strip()})
        if errs:
            print("; ".join(errs))
            return 2
        want.setdefault(kind, {}).update(clean)
    for d in bots:
        ch = {**want.get("all", {}), **want.get(d.name, {})}
        if not ch:
            continue
        cur = settings_store.load(d / "data")
        cur.update(ch)
        settings_store.save(d / "data", cur)
        print(f"  {d.parent.name}/{d.name}: saved {', '.join(f'{k}={v}' for k, v in ch.items())}")
    return 0


def update(root: Path, sets: list[str] = (), s3_bucket: str = "", person: str = "") -> int:
    """New version: refresh the code and news calendar for every installed bot, then restart their services."""
    bots = sorted(p.parent for p in root.glob("*/*/config.yaml") if p.parent.parent.name != "app")
    if not bots:
        print(f"no bots installed under {root}")
        return 1
    if s3_bucket:                                # exactly one person uploads: the same markets, so one copy is enough
        rec = sorted({d.parent.name for d in bots if "record: true" in (d / "config.yaml").read_text()})
        if not person:
            print(f"--s3-bucket needs --person (the one person whose bots upload); recording now: {', '.join(rec) or 'nobody'}")
            return 2
        if person not in {d.parent.name for d in bots}:
            print(f"no bots installed for {person!r} under {root}")
            return 2
    copy_code(root)
    if sets and apply_sets(bots, list(sets)):
        return 2
    if s3_bucket:
        for d in bots:
            if d.parent.name != person:
                if unset_s3(d / "config.yaml"):
                    print(f"  {d.parent.name}/{d.name}: S3 upload removed (only {person} uploads)")
                continue
            add_aws_env(d.parent / ".env")
            if set_s3(d / "config.yaml", s3_bucket, f"vps/{d.name}/"):
                print(f"  {d.parent.name}/{d.name}: recordings upload to s3://{s3_bucket}/vps/{d.name}/rec/")
            else:
                print(f"  {d.parent.name}/{d.name}: not recording (record: false), so nothing to upload")
    for d in bots:
        (d / "data").mkdir(exist_ok=True)
        shutil.copy2(HERE / "news_calendar.csv", d / "data" / "news_calendar.csv")
    print(f"code updated in {root / 'app'}; calendar refreshed for {len(bots)} bots")
    for d in bots:
        name = f"kalshibot-{d.parent.name}-{d.name}"
        if os.name == "nt" and shutil.which("nssm"):
            r = subprocess.run(["nssm", "restart", name], capture_output=True, text=True)
            print(f"  {name}: {'restarted' if r.returncode == 0 else 'restart FAILED: ' + (r.stdout + r.stderr).strip()[:200]}")
        elif shutil.which("systemctl") and Path(f"/etc/systemd/system/{name}.service").exists():
            r = subprocess.run(["sudo", "systemctl", "restart", name], capture_output=True, text=True)
            print(f"  {name}: {'restarted' if r.returncode == 0 else 'restart FAILED: ' + (r.stdout + r.stderr).strip()[:200]}")
        else:
            print(f"  {name}: restart it yourself (no nssm here)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", help="folder name for this person, e.g. jitesh")
    ap.add_argument("--port", type=int, help="dashboard port of the first bot (the next bot gets +1)")
    ap.add_argument("--set", action="append", default=[], metavar="BOT:KEY=VALUE",
                    help="with --update: save a setting on every installed bot of that kind (btc / gold / all), like the "
                         "dashboard Settings page, e.g. gold:strategy.mny_start=0.115 (display units: %% and cents)")
    ap.add_argument("--update", action="store_true", help="refresh the code + calendar of EVERY installed bot and restart "
                                                          "its service (keys, .env, settings, data, configs untouched)")
    ap.add_argument("--bots", default="btc,gold")
    ap.add_argument("--size", type=int, default=1, help="contracts per entry (1 = plumbing test, 10 ~ backtest-like)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--no-record", action="store_true", help="do not record the order book (use for the second person: same markets)")
    ap.add_argument("--service", action="store_true", help="install and start the services (Windows: NSSM; Linux: systemd, via sudo)")
    ap.add_argument("--force-config", action="store_true", help="rewrite existing config.yaml files")
    ap.add_argument("--s3-bucket", default="", help="recording bots upload each finished hour file to this S3 bucket "
                                                    "(AWS keys in the person's .env). With --update also give --person: only "
                                                    "that person's bots upload, any other person's S3 upload is removed")
    a = ap.parse_args(argv)
    root = Path(a.root or (r"C:\kalshibot" if os.name == "nt" else "~/kalshibot")).expanduser()
    if a.update:
        return update(root, a.set, a.s3_bucket, a.person or "")
    if not a.person or a.port is None:
        ap.error("--person and --port are needed (or --update)")
    bots = [b.strip() for b in a.bots.split(",") if b.strip()]
    for b in bots:
        if b not in ("btc", "gold"):
            ap.error(f"unknown bot {b!r} (btc, gold)")
    app = copy_code(root)
    # person
    person = root / a.person
    (person / "secrets").mkdir(parents=True, exist_ok=True)
    env = person / ".env"
    if not env.exists():
        import secrets
        env.write_text(ENV_TEMPLATE.format(person=a.person, token=secrets.token_urlsafe(24), aws=AWS_ENV))
    elif a.s3_bucket:
        add_aws_env(env)
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
        if a.s3_bucket and not a.no_record:
            set_s3(cfg, a.s3_bucket, f"vps/{b}/")
        made.append((b, d, port))
    key = person / "secrets" / "kalshi.pem"
    print(f"\nInstalled for {a.person} in {person}")
    for b, d, port in made:
        print(f"  {b:4s}: {d}   dashboard http://127.0.0.1:{port}")
    print("\nNext:")
    print(f"  1. Put {a.person}'s Kalshi private key at {key}")
    print(f"  2. Put {a.person}'s Kalshi Key ID in {env} (KALSHI_API_KEY_ID=...). The dashboard password is already there.")
    if a.s3_bucket and not a.no_record:
        print(f"  2b. Put the AWS key of the S3 upload user in {env} (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")
    print(f"  3. Check:  python {app / 'run.py'} {made[0][1]} --check")
    py = sys.executable
    for b, d, port in made:
        name = f"kalshibot-{a.person}-{b}"
        if os.name != "nt":
            unit = root / "systemd" / f"{name}.service"
            unit.parent.mkdir(exist_ok=True)
            unit.write_text(SYSTEMD_UNIT.format(name=name, user=getpass.getuser(), dir=d, py=py, run=app / "run.py"))
            cmd = [["sudo", "install", "-m", "644", str(unit), f"/etc/systemd/system/{name}.service"],
                   ["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "enable", "--now", name]]
            if a.service and shutil.which("systemctl"):
                for c in cmd:
                    subprocess.run(c, check=False)
                print(f"  service {name}: installed and started (logs: {d / 'logs'}; sudo systemctl status {name})")
            else:
                print(f"  service {name} (Linux):")
                for c in cmd:
                    print("     " + " ".join(c))
            continue
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
