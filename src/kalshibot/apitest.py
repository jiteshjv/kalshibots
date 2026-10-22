"""Order smoke test on Kalshi's DEMO exchange (fake money): every order path the bot uses, against the real API.

    python -m kalshibot.apitest [--series KXBTC15M]      (needs KALSHI_DEMO_API_KEY_ID + KALSHI_DEMO_PRIVATE_KEY_PATH)
    python -m kalshibot.apitest --stops                  (stop + restart test through the bot's own order code)
    Stop the demo bot first: the tests trade 1-2 contracts and must be alone in the market.

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


def flat_market(rest: KalshiRest, series: str, max_wait_s: float = 1200) -> str | None:
    """A market with 3+ minutes left where the account holds nothing and has no resting orders: the tests assume they
    are alone in the market (never run them where the demo bot holds a position). Waits up to max_wait_s."""
    t0 = time.time()
    while True:
        now = time.time()
        for m in rest.open_markets(series):
            if m.open_ts <= now < m.close_ts - 180 and abs(position(rest, m.ticker)) < 1e-9 and not resting_ids(rest, m.ticker):
                step("open market with 3+ minutes left, account flat in it", True, m.ticker)
                return m.ticker
        if time.time() - t0 > max_wait_s:
            step("open market with 3+ minutes left, account flat in it", False,
                 "none found; stop the demo bot (it may hold this market) or try later")
            return None
        print("  ...no flat market with 3+ minutes left yet (demo bot holding this one?); checking again in 30 s")
        time.sleep(30)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.apitest")
    ap.add_argument("--series", default="KXBTC15M")
    ap.add_argument("--stops", action="store_true", help="run the stop test instead (2 contracts per side with a bid)")
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
    if a.stops:
        return stop_test(rest, a.series)
    t = flat_market(rest, a.series)
    if t is None:
        return 1
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


def stop_test(rest: KalshiRest, series: str) -> int:
    """Stops the way the bot sends them, through LiveBroker and the strategy's order intents, on each side that has
    an ask and a bid: buy 2 lots of 1 (market), a resting 99.7c take-profit sell per lot, then
      partial stop: cancel lot 1's TP + market sell lot 1 -> 1 held, lot 2's TP still resting on the exchange;
      full stop:    cancel lot 2's TP + market sell lot 2 -> 0 held, nothing resting;
    and the broker's fills, positions and order states agree with the exchange at each point."""
    import uuid
    from kalshibot.config import Settings
    from kalshibot.execution.live import LiveBroker
    from kalshibot.types import OrderIntent
    t = flat_market(rest, series)
    if t is None:
        return 1
    s = Settings(); s.risk.mode = "demo"
    done: dict[str, str] = {}
    br = LiveBroker(rest, s, on_done=lambda cid, st: done.__setitem__(cid, st), inline=True,
                    exchange_index=rest.exchange_index)
    tag = uuid.uuid4().hex[:6]
    n = iter(range(1, 1000))
    cid = lambda: f"kbstop{tag}-{next(n):03d}"

    def run(it: OrderIntent):
        asyncio.run(br.submit(it, t))

    def settle(c: str, timeout=10.0):           # an IOC order ends once its fills have been read
        t0 = time.time()
        while br.orders[c].active and time.time() - t0 < timeout:
            time.sleep(0.5)
            asyncio.run(br.poll_fills())
        return br.orders[c]

    for side in ("y", "n"):
        name = "YES" if side == "y" else "NO"
        book = parse_orderbook(rest.request("GET", f"/markets/{t}/orderbook"))
        ask, bid = book.best_ask(side), book.best_bid(side)
        if ask is None or bid is None:
            print(f"  skip  {name}: demo book has no {'ask' if ask is None else 'bid'} on this side")
            continue
        start = position(rest, t)
        br.restore_position(t, side, 0)
        lots = []
        for k in (1, 2):                                 # entries: market buys, as the bot's fallback sends them
            c = cid()
            run(OrderIntent(kind="market_buy", side=side, qty=1, price=0.99, lot_id=f"{tag}{side}L{k}", client_id=c,
                            reason="entry_market_fallback"))
            o = settle(c)
            lots.append(o)
        if not step(f"{name}: 2 market buys filled", all(o.filled == 1 for o in lots),
                    f"at {[o.avg_price for o in lots]}, broker holds {br.position(t, side)}"):
            for o in lots:                               # undo whatever filled
                if o.filled:
                    run(OrderIntent(kind="market_sell", side=side, qty=o.filled, lot_id=o.lot_id, client_id=cid(), reason="stop"))
            continue
        tps = []
        for o in lots:                                   # one resting TP per lot (local reduce-only clip)
            c = cid()
            run(OrderIntent(kind="limit_sell", side=side, qty=1, price=0.997, lot_id=o.lot_id, client_id=c, reason="tp"))
            tps.append(br.orders[c])
        listed = wait(lambda: resting_ids(rest, t), lambda r: all(x.exchange_id in r for x in tps))
        step(f"{name}: 2 take-profit sells resting", all(x.exchange_id in listed for x in tps),
             f"states {[x.status for x in tps]}")
        # partial stop: exactly what the strategy emits for one stopped lot (cancel its TP, then a market sell)
        run(OrderIntent(kind="cancel", side=side, qty=0, lot_id=lots[0].lot_id, client_id=tps[0].client_id, reason="stop"))
        c = cid()
        t0 = time.time()
        run(OrderIntent(kind="market_sell", side=side, qty=1, price=None, lot_id=lots[0].lot_id, client_id=c, reason="stop"))
        o = settle(c)
        sgn = 1 if side == "y" else -1                  # Kalshi positions: YES > 0, NO < 0
        pos = wait(lambda: position(rest, t), lambda p: abs(p - (start + sgn)) < 1e-9)
        res = resting_ids(rest, t)
        step(f"{name}: partial stop sold 1 at the bid", o.filled == 1 and o.status == "filled",
             f"sold at {o.avg_price}, book bid was {bid}, {time.time() - t0:.1f}s, status {o.status}")
        step(f"{name}: after partial stop: 1 held, lot 2 TP still resting, lot 1 TP gone",
             abs(pos - (start + sgn)) < 1e-9 and tps[1].exchange_id in res and tps[0].exchange_id not in res
             and br.position(t, side) == 1, f"exchange position {pos:+g}, broker {br.position(t, side)}")
        # full stop of the last lot
        run(OrderIntent(kind="cancel", side=side, qty=0, lot_id=lots[1].lot_id, client_id=tps[1].client_id, reason="stop"))
        c = cid()
        run(OrderIntent(kind="market_sell", side=side, qty=1, price=None, lot_id=lots[1].lot_id, client_id=c, reason="stop"))
        o = settle(c)
        pos = wait(lambda: position(rest, t), lambda p: abs(p - start) < 1e-9)
        res = resting_ids(rest, t)
        step(f"{name}: full stop sold the last contract", o.filled == 1, f"sold at {o.avg_price}, status {o.status}")
        step(f"{name}: after full stop: 0 held, no resting orders",
             abs(pos - start) < 1e-9 and not any(x.exchange_id in res for x in tps) and br.position(t, side) == 0,
             f"exchange position {pos:+g}, broker {br.position(t, side)}")
        step(f"{name}: broker order states", done.get(tps[0].client_id) == "cancelled" and done.get(tps[1].client_id) == "cancelled",
             f"TPs {[done.get(x.client_id) for x in tps]}")
    # restart: 2 contracts held with their TPs resting, the bot process dies, a NEW broker (as after a restart)
    # reconciles with Kalshi, adopts the resting TPs under their client ids, then stops both lots
    for side in ("y", "n"):
        book = parse_orderbook(rest.request("GET", f"/markets/{t}/orderbook"))
        if book.best_ask(side) is None or book.best_bid(side) is None:
            print(f"  skip  restart ({'YES' if side == 'y' else 'NO'}): demo book has no ask or bid on this side")
            continue
        name = "YES" if side == "y" else "NO"
        sgn = 1 if side == "y" else -1
        start = position(rest, t)
        br.restore_position(t, side, 0)
        lots, tps = [], []
        for k in (1, 2):
            c = cid()
            run(OrderIntent(kind="market_buy", side=side, qty=1, price=0.99, lot_id=f"{tag}R{k}", client_id=c, reason="entry"))
            lots.append(settle(c))
        for o in lots:
            c = cid()
            run(OrderIntent(kind="limit_sell", side=side, qty=1, price=0.997, lot_id=o.lot_id, client_id=c, reason="tp"))
            tps.append(br.orders[c])
        wait(lambda: resting_ids(rest, t), lambda r: all(x.exchange_id in r for x in tps))
        step(f"restart ({name}): before restart 2 held + 2 TPs resting", all(o.filled == 1 for o in lots)
             and all(x.status == "resting" for x in tps), f"position {position(rest, t):+g}")
        br0 = LiveBroker(rest, s, inline=True, exchange_index=rest.exchange_index)
        asyncio.run(br0.cancel_entries())                # shutdown path (cancels resting buys, must keep the TPs)
        res = resting_ids(rest, t)
        step(f"restart ({name}): shutdown kept both TPs", all(x.exchange_id in res for x in tps))
        br2 = LiveBroker(rest, s, on_done=lambda c_, st_: done.__setitem__(c_, st_), inline=True,
                         exchange_index=rest.exchange_index)                      # the restarted bot
        held = asyncio.run(br2.reconcile(t))
        adopted = {o.client_id: o for o in br2.open_orders(t) if o.kind == "limit_sell"}
        step(f"restart ({name}): reconcile sees 2 held", [(h["side"], h["qty"]) for h in held] == [(side, 2)],
             str([(h["side"], h["qty"]) for h in held]))
        step(f"restart ({name}): both TPs adopted under their own ids (not re-placed)",
             all(x.client_id in adopted for x in tps) and len(adopted) == 2, f"{len(adopted)} adopted sells")
        for i, (o, x) in enumerate(zip(lots, tps)):            # stop each lot through the restarted broker
            asyncio.run(br2.submit(OrderIntent(kind="cancel", side=side, qty=0, lot_id=o.lot_id, client_id=x.client_id,
                                               reason="stop"), t))
            c = cid()
            asyncio.run(br2.submit(OrderIntent(kind="market_sell", side=side, qty=1, price=None, lot_id=o.lot_id,
                                               client_id=c, reason="stop"), t))
            t0 = time.time()
            while br2.orders[c].active and time.time() - t0 < 10:
                time.sleep(0.5)
                asyncio.run(br2.poll_fills())
            so = br2.orders[c]
            exp = start + sgn * (1 - i)
            pos = wait(lambda: position(rest, t), lambda p: abs(p - exp) < 1e-9)
            step(f"restart ({name}): stop {i + 1} after restart sold", so.filled == 1 and abs(pos - exp) < 1e-9,
                 f"sold at {so.avg_price}, exchange position {pos:+g}, broker {br2.position(t, side)}")
        res = resting_ids(rest, t)
        step(f"restart ({name}): nothing left resting", not any(x.exchange_id in res for x in tps))
        br.errors += br2.errors
    if br.errors:
        print("  broker errors:", "; ".join(br.errors)[:600])
    return summary()


def summary() -> int:
    bad = [n for n, ok, _ in R if not ok]
    print(f"\n{len(R) - len(bad)}/{len(R)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else " - every order path works on demo"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
