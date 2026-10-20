# Revised BTC 15-minute trading system

## What the evidence supports

The original short-horizon fair-value strategy has negative preliminary results.
The existing `scripts/codex_rules.py` is not an exact replay of the installed bot:
it uses log-return standard deviation instead of robust dollar volatility, different
shrinkage, reconstructs the target from Binance, and uses future trade VWAP to
decide whether its signal clears the edge threshold. Its results are evidence
against the approach, but do not establish exact bot performance.

`scripts/her.py` also reconstructs the target, has no 76–85c entry-price gate,
does not round order fees, and credits a 95c limit fill from aggregate trade VWAP.
Do not overwrite those scripts or their original results.

`scripts/momentum_audit.py` screens a fixed, provisional momentum rule:

- 120–839 seconds after open; latest completed second only.
- Three consecutive BTC readings at least 0.15% past the published target.
- Leading side's completed three-second tape VWAP between 76c and 85c.
- One-second delay; next three-second tape VWAP must be within the preselected
  limit and aggregate volume at least five contracts. Charge the full limit.
- One entry per window; five-contract research size.
- Hold versus 95c touch versus minimum trade price strictly above 95c.
- If no qualifying 95c exit, hold to official Kalshi settlement.
- Test raw Binance against the target and Binance offset once at open to match
  the published target. The latter reduces initial basis error, but is not BRTI.
- Report uncapped screening and a separate $4/trade, $20/UTC-day premium cap.

This is an execution proxy, not proof of fills or a production strategy. The saved
tape has per-second minimum price, quantity and notional; it lacks the original
trade ordering, maximum price, quote depth, queue priority and block-trade flags.
Even a strict through-price rule cannot recreate queue position or partial fills.
Future aggregated execution prices never participate in the decision-price gate.

Run from the project root:

```sh
python3 scripts/momentum_audit.py --windows 700
python3 scripts/momentum_audit.py --windows 6322 --out results/momentum_audit_available.json
python3 scripts/momentum_audit.py --windows 700 --fee-quantum 0.0001 --out results/momentum_audit_subcent.json
```

Fee rates and quantum are explicit assumptions. July 7, 2026 official fee text
specifies multipliers and centicent rounding, while examples still show cent
amounts. The old claim that every order universally rounds to a cent is not safe.
Reconcile fees against the series fee history and actual account fills. The
default cent quantum is a conservative stress assumption; maker rate .0175 is
also an assumption, configurable to zero if the applicable series charges none.

## Build sequence

1. Record official BRTI with source timestamp and local receipt timestamp; collect
   full Kalshi order-book snapshots/deltas, raw non-block trades, market metadata
   and official settlements. Persist raw events before interpreting them. The
   CLI defaults to recorder now, but its current polling recorder logs only
   top-of-book quotes: this is not yet the complete capture service.
2. Build a sequenced order book with gap detection, reconnect and resnapshot.
   Calculate YES ask from NO bids and NO ask from YES bids. Measure quote age,
   displayed quantity and depth-weighted execution cost at decision time.
3. Replay the fixed momentum candidate over those recorded events. Base all
   decisions on receipt time; insert latency; consume displayed depth; maintain
   partial fills. For maker exits, model queue ahead and report sensitivity to
   cancellations instead of automatically crediting price touches.
4. Compare hold and 95c exits on identical signals. Set the exit fallback before
   testing. Add stop variants only as separately named experiments. A 95c cap
   can reduce profit on contracts that would settle at $1, so it needs evidence.
5. Freeze parameters, reserve later dates as an untouched test, and forward-paper
   trade across volatility regimes. Compare net P&L with a matched benchmark
   buying the same price/time buckets without the distance filter. Bootstrap
   calendar days; report missing coverage, total fees, equity and drawdown.
6. Only after a positive result, implement live execution with a durable state
   machine: observing → entry pending → partially/full filled → exit resting →
   cancel/reconcile → closed/settled. Reconcile fills and positions after restart;
   never infer inventory from an order submission response. Persist daily caps
   and distinguish premium committed, premium filled and realized loss.

The current legacy bot increments risk before confirming a fill, resets its
in-memory cap on restart, and never rolls it at a calendar boundary. Those are
additional reasons not to use it as a production executor. This revision changes
the CLI default to recording; it does not repair or activate legacy live execution.

## Official references

- Order book: https://docs.kalshi.com/getting_started/orderbook_responses
- Trade fields: https://docs.kalshi.com/api-reference/market/get-trades
- Fees: https://kalshi.com/docs/kalshi-fee-schedule.pdf
- Settlement: https://help.kalshi.com/en/articles/13823838-crypto-markets
