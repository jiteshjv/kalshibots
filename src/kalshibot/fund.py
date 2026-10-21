"""Move money between your own Kalshi exchange shards (BTC / gold markets trade on shard 2). Same account, no withdrawal.

    python -m kalshibot.fund --show                       balances per shard (real account)
    python -m kalshibot.fund --to 2 --amount 500          move $500 from shard 0 to shard 2 (asks you to confirm;
                                                          --yes confirms in the command)
    add --demo for the demo account (no confirmation needed: fake money)
Uses Kalshi's POST /portfolio/intra_exchange_instance_transfer. Kalshi's website can do the same (exchange indexes page).
"""
from __future__ import annotations

import argparse
import time
import uuid

from kalshibot.config import DEMO_REST, kalshi_credentials
from kalshibot.marketdata.kalshi_rest import KalshiRest


def shards(rest: KalshiRest) -> dict[int, float]:
    b = rest.request("GET", "/portfolio/balance", auth=True)
    return {int(x["exchange_index"]): float(x["balance"]) for x in b.get("balance_breakdown", [])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.fund", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--from", dest="src", type=int, default=0)
    ap.add_argument("--to", dest="dst", type=int, default=2)
    ap.add_argument("--amount", type=float, help="dollars")
    ap.add_argument("--yes", action="store_true", help="confirm a REAL-money move without the typed prompt")
    a = ap.parse_args(argv)
    kid, kp = kalshi_credentials(demo=a.demo)
    if not (kid and kp):
        print("Kalshi key not set (" + ("KALSHI_DEMO_API_KEY_ID / KALSHI_DEMO_PRIVATE_KEY_PATH" if a.demo else
                                        "KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH") + ")")
        return 2
    rest = KalshiRest(kid, kp, **({"base_url": DEMO_REST} if a.demo else {}))
    acct = "DEMO" if a.demo else "REAL"
    before = shards(rest)
    print(f"{acct} account, dollars per shard: " + ", ".join(f"{k}: ${v:,.2f}" for k, v in sorted(before.items())))
    if a.show or a.amount is None:
        return 0
    if a.amount <= 0 or a.amount > before.get(a.src, 0):
        print(f"amount must be > 0 and at most the ${before.get(a.src, 0):,.2f} on shard {a.src}")
        return 2
    if not a.demo and not a.yes:
        ok = input(f"Move ${a.amount:,.2f} of REAL money from shard {a.src} to shard {a.dst} in your own account? Type YES: ")
        if ok.strip() != "YES":
            print("cancelled")
            return 1
    rest.request("POST", "/portfolio/intra_exchange_instance_transfer", auth=True, json={
        "client_transfer_id": str(uuid.uuid4()), "source": "event_contract", "destination": "event_contract",
        "amount": int(round(a.amount * 10000)), "source_exchange_shard": a.src, "destination_exchange_shard": a.dst})
    for _ in range(30):
        time.sleep(1)
        after = shards(rest)
        if after.get(a.dst, 0) > before.get(a.dst, 0):
            break
    print("after: " + ", ".join(f"{k}: ${v:,.2f}" for k, v in sorted(after.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
