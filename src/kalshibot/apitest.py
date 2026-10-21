"""Order smoke test on Kalshi's DEMO exchange (fake money): every order path the bot uses, against the real API.

    python -m kalshibot.apitest [--series KXBTC15M]      (needs KALSHI_DEMO_API_KEY_ID + KALSHI_DEMO_PRIVATE_KEY_PATH)

Steps (1 contract, a market with >3 minutes left): balance; resting post-only buy far below the market -> listed ->
cancel -> gone; immediate buy of 1 contract -> fill + position; resting take-profit sell -> listed -> cancel; immediate
sell of the contract -> position back to 0; the restart reconcile sees the right position at each point. Prints
PASS / FAIL per step. Never runs against the real exchange.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from kalshibot.config import DEMO_REST, kalshi_credentials
from kalshibot.marketdata.kalshi_rest import KalshiRest, parse_orderbook

R: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, info: str = "") -> bool:
    R.append((name, ok, info))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info else ""))
    return ok


def position(rest: KalshiRest, ticker: str) -> float:
    for p in rest.positions(ticker):
        if p.get("ticker") == ticker:
            v = p.get("position_fp", p.get("position", 0))
            return float(v or 0)
    return 0.0


def resting_ids(rest: KalshiRest, ticker: str) -> set[str]:
    return {str(o.get("order_id")) for o in rest.orders("resting", ticker)}


def wait(fn, ok, timeout=10.0, every=0.5):
    t = time.time()
    v = fn()
    while not ok(v) and time.time() - t < timeout:
        time.sleep(every)
        v = fn()
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.apitest")
    ap.add_argument("--series", default="KXBTC15M")
    a = ap.parse_args(argv)
    kid, kp = kalshi_credentials(demo=True)
    if not (kid and kp):
        print("needs the DEMO key: KALSHI_DEMO_API_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH")
        return 2
    rest = KalshiRest(kid, kp, base_url=DEMO_REST)
    assert "demo" in rest.base_url, "refusing: not the demo exchange"
    print(f"Kalshi DEMO order smoke test ({rest.base_url}, {a.series})")
    try:
        b = rest.balance()
        step("signed request (balance)", "balance" in b or "balance_dollars" in str(b), "")
        per = {int(x["exchange_index"]): float(x["balance"]) for x in b.get("balance_breakdown", [])}
        if not step(f"money on exchange shard {rest.exchange_index} (crypto/commodities)", per.get(rest.exchange_index, 0) > 0,
                    f"shards with money: {[k for k, v in per.items() if v > 0] or 'none'}"):
            print("  -> add demo funds on demo.kalshi.co and move them to exchange index "
                  f"{rest.exchange_index} (account settings, exchange indexes), then run again")
            return summary()
    except Exception as e:
        step("signed request (balance)", False, str(e)[:150])
        return 1
    now = time.time()
    ms = [m for m in rest.open_markets(a.series) if m.open_ts <= now < m.close_ts - 180]
    if not step("open market with 3+ minutes left", bool(ms), ms[0].ticker if ms else "none; try again in a few minutes"):
        return 1
    m = ms[0]
    t = m.ticker
    book = parse_orderbook(rest.request("GET", f"/markets/{t}/orderbook"))
    start = position(rest, t)
    step("starting position read", True, f"{start:+g} contracts")
    # 1. resting post-only buy far below the market, then cancel
    try:
        r = rest.create_order(t, "y", "buy", 1, 0.01, post_only=True)
        oid = str((r.get("order") or r).get("order_id"))
        listed = wait(lambda: resting_ids(rest, t), lambda s: oid in s)
        step("resting buy placed and listed", oid in listed, f"order {oid[:8]}...")
        rest.cancel_order(oid, t)
        gone = wait(lambda: resting_ids(rest, t), lambda s: oid not in s)
        step("resting buy cancelled", oid not in gone)
    except Exception as e:
        step("resting buy / cancel", False, str(e)[:200])
    # 2. immediate buy of 1 contract (the side with an ask), check fill + position
    side = "y" if book.best_ask("y") is not None else ("n" if book.best_ask("n") is not None else None)
    if not step("demo book has an ask to buy from", side is not None, f"YES ask {book.best_ask('y')}, NO ask {book.best_ask('n')}"):
        return summary()
    try:
        t0 = time.time()
        r = rest.create_order(t, side, "buy", 1, 0.99, time_in_force="immediate_or_cancel")
        o = r.get("order") or r
        pos = wait(lambda: position(rest, t), lambda p: abs(p - start) >= 1)
        delta = pos - start
        step("immediate buy filled (position +1)", abs(delta) >= 1, f"{'YES' if side == 'y' else 'NO'}, position {pos:+g}")
        fl = wait(lambda: rest.fills(t0 - 5, t), lambda f: len(f) > 0)
        step("fill reported by /portfolio/fills", len(fl) > 0, f"{len(fl)} fill row(s)")
    except Exception as e:
        step("immediate buy", False, str(e)[:200])
        return summary()
    # 3. restart reconcile sees it
    try:
        from kalshibot.config import Settings
        from kalshibot.execution.live import LiveBroker
        s = Settings(); s.risk.mode = "demo"
        br = LiveBroker(rest, s, inline=True)
        held = asyncio.run(br.reconcile(t))
        step("restart reconcile sees the position", any(h["qty"] >= 1 for h in held), str([(h["side"], h["qty"]) for h in held]))
    except Exception as e:
        step("restart reconcile", False, str(e)[:200])
    # 4. resting take-profit sell, then cancel
    try:
        r = rest.create_order(t, side, "sell", 1, 0.99)
        oid = str((r.get("order") or r).get("order_id"))
        listed = wait(lambda: resting_ids(rest, t), lambda s_: oid in s_)
        step("take-profit sell resting", oid in listed)
        rest.cancel_order(oid, t)
        step("take-profit sell cancelled", oid not in wait(lambda: resting_ids(rest, t), lambda s_: oid not in s_))
    except Exception as e:
        step("take-profit sell / cancel", False, str(e)[:200])
    # 5. immediate sell (like a stop), back to the starting position
    try:
        rest.create_order(t, side, "sell", 1, 0.01, time_in_force="immediate_or_cancel", reduce_only=True)
        pos = wait(lambda: position(rest, t), lambda p: abs(p - start) < 1)
        step("immediate sell filled (back to start)", abs(pos - start) < 1, f"position {pos:+g}")
    except Exception as e:
        step("immediate sell", False, str(e)[:200])
    return summary()


def summary() -> int:
    bad = [n for n, ok, _ in R if not ok]
    print(f"\n{len(R) - len(bad)}/{len(R)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else " - every order path works on demo"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
