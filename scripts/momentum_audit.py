"""Offline screening only: aggregated trade tape is NOT a historical order book.

Run from any directory: python3 scripts/momentum_audit.py --windows 700
Decision proxies use completed seconds only; later tape is used only for execution.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import io
import json
from pathlib import Path
import random
import statistics
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def fee(price, count, rate, quantum="0.01"):
    """Explicit historical assumption; cent rounding is a stress case, not universal."""
    p = Decimal(str(price))
    value = Decimal(str(rate)) * count * p * (1 - p)
    unit = Decimal(quantum)
    return float((value / unit).to_integral_value(rounding=ROUND_CEILING) * unit)


def timestamp(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def load_prices(folder, start, end):
    prices = {}
    first_day = datetime.fromtimestamp(start, timezone.utc).date().isoformat()
    last_day = datetime.fromtimestamp(end, timezone.utc).date().isoformat()
    for path in sorted(folder.glob("*.zip")):
        day = path.stem[-10:]
        if not first_day <= day <= last_day:
            continue
        with zipfile.ZipFile(path) as archive:
            for row in csv.reader(io.TextIOWrapper(archive.open(archive.namelist()[0]))):
                raw = int(row[0])
                t = raw // (1_000_000 if raw > 1e14 else 1000)
                if start <= t <= end:
                    prices[t] = float(row[4])
    today = folder / "today.json"
    if today.exists():
        for t, value in json.loads(today.read_text()):
            t = int(t) // 1000
            if start <= t <= end:
                prices[t] = float(value)
    return prices


def tape_price(tape, t, side, seconds=3):
    """VWAP over [t, t+seconds), not an executable quote."""
    quantity = notional = 0.0
    for second in range(t, t + seconds):
        row = tape.get(second)
        if row:
            quantity += row[side][1]
            notional += row[side][2]
    return (notional / quantity, quantity) if quantity else (None, 0.0)


def exit_fill(row, side, target, count, mode):
    if mode == "hold" or not row or row[side][1] < count:
        return False
    # min > limit is stricter than VWAP >= limit, but neither proves queue priority.
    price = row[side][0] if mode == "through" else row[side][2] / row[side][1]
    return price > target if mode == "through" else price >= target


def replay(market, prices, tape, *, basis, exit_mode, slippage,
           count=5, quantum="0.01", taker_rate=0.07, maker_rate=0.0175):
    close = timestamp(market["close_time"])
    opened = close - 900
    strike = float(market["floor_strike"])
    before = [prices.get(t) for t in range(opened - 60, opened)]
    if any(p is None for p in before):
        return None, "missing_btc"
    # Anchor once at open using the published strike. Never use future settlement.
    offset = strike - statistics.mean(before) if basis == "anchored" else 0.0
    for elapsed in range(120, 840):
        now = opened + elapsed
        # Latest fully completed 1s candle; require 3 consecutive confirmations.
        observed = [prices.get(now - k) for k in (3, 2, 1)]
        if any(p is None for p in observed):
            continue
        distances = [(p + offset - strike) / strike for p in observed]
        side = "y" if min(distances) >= .0015 else "n" if max(distances) <= -.0015 else None
        if side is None:
            continue
        past_price, _ = tape_price(tape, now - 3, side)
        if past_price is None or not .76 <= past_price <= .85:
            continue
        # IOC limit decided now. A later print above it means no assumed fill.
        limit = min(.85, past_price + slippage)
        later, size = tape_price(tape, now + 1, side)
        if later is None or size < count or later > limit:
            continue
        # Charge the limit, not an improvement inferred from another trader's print.
        entry = limit
        entry_fee = fee(entry, count, taker_rate, quantum)
        end_price = float(market["result"] == ("yes" if side == "y" else "no"))
        reason, end = "settlement", close
        for t in range(now + 5, close):
            if exit_fill(tape.get(t), side, .95, count, exit_mode):
                end_price, reason, end = .95, "95c", t + 1
                break
        exit_fee = fee(end_price, count, maker_rate, quantum) if reason == "95c" else 0
        pnl = count * (end_price - entry) - entry_fee - exit_fee
        return dict(ticker=market["ticker"], decision_ts=now, entry_proxy_end_ts=now+4,
                    exit_ts=end, side=side, entry=entry, count=count, premium=count*entry,
                    entry_fee=entry_fee, exit_fee=exit_fee, exit=end_price, reason=reason,
                    pnl=pnl, day=datetime.fromtimestamp(now, timezone.utc).date().isoformat()), None
    return None, "no_fill_or_signal"


def summarize(rows, eligible_days):
    if not rows:
        return {"trades": 0}
    daily = defaultdict(list)
    equity = peak = drawdown = 0.0
    for row in rows:
        daily[row["day"]].append(row)
        equity += row["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    # Resample calendar days, including zero-trade days, retaining intraday dependence.
    days = [daily[day] for day in sorted(eligible_days)]
    rng = random.Random(20261015)
    estimates = []
    for _ in range(1000):
        sample = [r for day in rng.choices(days, k=len(days)) for r in day]
        contracts = sum(r["count"] for r in sample)
        if contracts:
            estimates.append(sum(r["pnl"] for r in sample)/contracts)
    estimates.sort()
    total_contracts = sum(r["count"] for r in rows)
    split = len(rows)//2
    half = lambda rr: sum(r["pnl"] for r in rr)/sum(r["count"] for r in rr) if rr else None
    return dict(trades=len(rows), observed_calendar_days=len(days),
                trades_per_observed_day=len(rows)/len(days),
                profitable_trade_fraction=sum(r["pnl"] > 0 for r in rows)/len(rows),
                mean_entry=statistics.mean(r["entry"] for r in rows),
                pnl=equity, pnl_per_contract=equity/total_contracts,
                day_bootstrap_95=[estimates[int(len(estimates)*.025)], estimates[int(len(estimates)*.975)]],
                max_drawdown=drawdown, exits_95c=sum(r["reason"] == "95c" for r in rows),
                chronological_halves=[half(rows[:split]), half(rows[split:])])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=int, default=700)
    parser.add_argument("--data", type=Path, default=ROOT/"data")
    parser.add_argument("--out", type=Path, default=ROOT/"results"/"momentum_audit.json")
    parser.add_argument("--fee-quantum", choices=["0.01", "0.0001"], default="0.01")
    parser.add_argument("--maker-rate", type=float, default=.0175)
    args = parser.parse_args()
    if args.windows <= 0 or args.maker_rate < 0:
        parser.error("windows must be positive; maker rate must be nonnegative")
    markets = sorted(json.loads((args.data/"settled.json").read_text()), key=lambda m:m["close_time"])[-args.windows:]
    loaded, skipped = [], defaultdict(int)
    available = [m for m in markets if m["result"] in ("yes", "no") and (args.data/"ktr"/(m["ticker"]+".json")).exists()]
    if not available:
        parser.error("No settled markets with downloaded trade tape")
    start, end = timestamp(available[0]["close_time"])-960, timestamp(available[-1]["close_time"])
    prices = load_prices(args.data/"bn", start, end)
    for market in markets:
        path = args.data/"ktr"/(market["ticker"]+".json")
        close = timestamp(market["close_time"])
        if market["result"] not in ("yes", "no") or not path.exists():
            skipped["missing_tape_or_result"] += 1
            continue
        if any(t not in prices for t in range(close-960, close)):
            skipped["incomplete_btc_window"] += 1
            continue
        loaded.append((market, {int(k):v for k,v in json.loads(path.read_text()).items()}))
    eligible_days = {datetime.fromtimestamp(timestamp(m["close_time"])-900, timezone.utc).date().isoformat() for m,_ in loaded}
    print(f"Requested {len(markets)}; eligible {len(loaded)}; skipped {dict(skipped)}", flush=True)
    scenarios = []
    for basis in ("raw", "anchored"):
        for mode in ("hold", "touch", "through"):
            for slip in (0.0, .01):
                rows = []
                for market, tape in loaded:
                    row, _ = replay(market, prices, tape, basis=basis, exit_mode=mode,
                                    slippage=slip, quantum=args.fee_quantum, maker_rate=args.maker_rate)
                    if row:
                        rows.append(row)
                config = dict(basis=basis, exit_mode=mode, slippage=slip)
                summary = summarize(rows, eligible_days)
                # Apply $4 premium/trade by reducing size, then a persistent-calendar $20 cap.
                risk_rows, spent = [], defaultdict(float)
                for row in rows:
                    n = min(5, int(4/row["entry"]))
                    if spent[row["day"]]+n*row["entry"] > 20:
                        continue
                    rr = dict(row, count=n, premium=n*row["entry"])
                    rr["entry_fee"] = fee(rr["entry"], n, .07, args.fee_quantum)
                    rr["exit_fee"] = fee(rr["exit"], n, args.maker_rate, args.fee_quantum) if rr["reason"]=="95c" else 0
                    rr["pnl"] = n*(rr["exit"]-rr["entry"])-rr["entry_fee"]-rr["exit_fee"]
                    spent[rr["day"]] += rr["premium"]
                    risk_rows.append(rr)
                scenarios.append(dict(config=config, screening=summary,
                                      risk_capped=summarize(risk_rows, eligible_days), trades=rows))
                print(config, {k:round(v,5) if isinstance(v,float) else v for k,v in summary.items() if k in ("trades","mean_entry","pnl_per_contract","day_bootstrap_95","exits_95c")}, flush=True)
    result = dict(requested_windows=len(markets), eligible_windows=len(loaded), skipped=dict(skipped),
                  first_close=markets[0]["close_time"], last_close=markets[-1]["close_time"],
                  assumptions=dict(btc="Binance USDT completed 1s close proxy", entry="past tape signal; 1s latency; later 3s VWAP <= IOC limit; charge limit",
                                   exit="5-contract aggregate volume; touch VWAP >=95c; through minimum >95c; queue unknown",
                                   taker_rate=.07, maker_rate=args.maker_rate, fee_quantum=args.fee_quantum,
                                   basis="raw uses published strike; anchored offsets Binance at open to published strike",
                                   interval="day bootstrap is descriptive; configurations are not independent; no untouched holdout"),
                  scenarios=scenarios)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+"\n")
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
