# Kalshi BTC 15-minute bot

Updated research status: the CLI now defaults to **recording only**. The original
probability strategy has negative preliminary backtest results and requires
`--strategy legacy` to enable even paper signals. No new momentum strategy is
connected to live orders. See `results/momentum_audit.json` for the revised offline
screen and `BUILD_PLAN.md` for the execution design and limitations.

A deliberately conservative trader for Kalshi's `KXBTC15M` markets. It tracks the
official CF Benchmarks BRTI, models the 60 one-second observations used for
settlement, compares that probability with the executable Kalshi ask, and takes at
most one position per market.

It starts in **paper mode**. Live mode uses immediate-or-cancel orders and requires
an explicit real-money confirmation phrase.

## What the first version does

- Discovers the current open `KXBTC15M` contract rather than constructing tickers.
- Routes crypto market discovery and orders to Kalshi exchange shard 2.
- Uses Kalshi's authenticated CF Benchmarks passthrough (`BRTI`), the settlement
  source—not Coinbase or the web-page chart.
- Estimates short-horizon BRTI volatility robustly from recent one-second changes.
- Models the final 60-second average, including observations already collected.
- Shrinks the model probability toward 50% to account for basis/model error.
- Trades only when edge, spread, time, data freshness, entry-price, position-size,
  per-trade premium, and daily premium checks all pass.
- Writes every quote, signal, paper fill, order response, and error to JSONL.

This is not a promise of profit. A friend's result is not evidence that the same
strategy, latency, fill quality, capital, or risk is reproducible.

## Install

```bash
cd /Users/jvidhani/trading/research/kalshi_btc
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Create a Kalshi API key under **Account & security → API Keys**. Keep the private
key outside this repository. Export:

```bash
export KALSHI_API_KEY_ID='your-key-id'
export KALSHI_PRIVATE_KEY_PATH='/absolute/path/to/kalshi-private-key.pem'
```

The base install supports RSA keys through the system OpenSSL executable. For an
Ed25519 key, install the optional development dependencies with
`pip install -e '.[dev]'`.

The BRTI passthrough is entitlement-gated. If Kalshi returns an authorization
error for `/cfbenchmarks/values`, ask Kalshi to enable the entitlement. The bot
intentionally does not silently substitute another price source.

## Recording and research paper signals

```bash
kalshi-btc15m
```

The command above records quotes only. To reproduce original paper signals,
explicitly add `--strategy legacy`. Inspect `data/trades.jsonl`. Run it across many market and volatility regimes,
then calculate results using actual settlements. Paper fills at the displayed ask
are optimistic; live slippage and rejected IOC quantities will be worse.

Useful overrides:

```bash
kalshi-btc15m --min-edge 0.10 --max-contracts 3 --max-premium 2 --max-daily-premium 10
```

## Live trading

Only after paper validation:

```bash
kalshi-btc15m --strategy legacy --live --confirm-live I_UNDERSTAND_REAL_MONEY \
  --max-contracts 2 --max-premium 1 --max-daily-premium 5
```

Live orders are IOC: unfilled quantity cancels instead of resting. Buying Down is
submitted through Kalshi's V2 single YES book as an ask at `1 - Down ask`.

## Test

```bash
pytest -q
```

## Before risking meaningful capital

This version is a safe executable baseline, not a production HFT system. Add a
replay/backtest over Kalshi market history and BRTI history, reconcile actual fees
and fills, stream the 5 Hz BRTI and order book over WebSockets, persist daily risk
state across restarts, and add alerting/kill-switch supervision. Do not increase
size based only on gross winning days.
