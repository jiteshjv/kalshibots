"""python -m kalshibot run --config config.yaml   |   python -m kalshibot check --config config.yaml"""
from __future__ import annotations

import argparse
import json
import sys

from kalshibot.config import LIVE_CONFIRM_PHRASE, Settings, kalshi_credentials


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kalshibot")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default=None, help="YAML settings file (defaults = the working model, paper mode)")
    a = p.parse_args(argv)
    s = Settings.load(a.config)
    if a.cmd == "check":
        key_id, key_path = kalshi_credentials()
        print(json.dumps(s.to_dict(), indent=2))
        print(f"\nmode: {s.risk.mode} | live confirmed: {s.risk.live_confirm == LIVE_CONFIRM_PHRASE} | Kalshi key: {'set' if key_id and key_path else 'NOT set (public data, paper only)'}")
        print(f"data dir: {s.data_path}")
        return 0
    if s.risk.mode == "live" and s.risk.live_confirm != LIVE_CONFIRM_PHRASE:
        print(f"refusing live mode: set risk.live_confirm to exactly: {LIVE_CONFIRM_PHRASE}", file=sys.stderr)
        return 2
    from kalshibot.app import run
    run(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
