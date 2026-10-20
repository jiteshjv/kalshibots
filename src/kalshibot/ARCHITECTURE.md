# kalshibot architecture (v0.1)

Live/paper trader for Kalshi `KXBTC15M`, implementing the working model of 2026-10-19 exactly as backtested in
`scripts/her_trades.py` (the reference implementation; read it). Each user runs their own copy with their own Kalshi
key. Paper mode first; live mode only with `risk.mode: live` + the confirm phrase.

Python 3.12, asyncio. Allowed third-party deps: `websockets`, `numpy`, `pyyaml`, `requests`, `boto3` (optional,
recorder only), `pytest` (tests). No `cryptography` (not installed): sign Kalshi requests with the `openssl` CLI as
`src/kalshi_btc_bot/client.py` does (you may COPY code from `src/kalshi_btc_bot/`; never edit that package).

Shared, already written — do not change without coordinating: `types.py` (data types + conventions), `config.py`
(settings, defaults = working model).

```
 marketdata/  ── BtcTick (1/s), Book + KTrades (live) ──►  view.py builds SecondView per second per market
      │                                                          │
      ▼                                                          ▼
 history.py (BtcHistory, ≥3 days)  ◄────────────────  strategy.py  Strategy.step(view) -> [OrderIntent]
                                                                 │            ▲
                                                                 ▼            │ Fill / order done
                                                  risk.py  approve(intent)    │
                                                                 ▼            │
                                                  execution/  PaperBroker | LiveBroker | BacktestBroker
                                                                 │
                                                  journal.py (SQLite)  ──► dashboard/, alerts.py
 recorder.py (research only): every book delta / trade / BtcTick -> hourly .jsonl.gz -> S3
 app.py (integration, written last): wires everything; `python -m kalshibot run --config config.yaml`
```

## File ownership (one agent per area; do not edit files you do not own)

| Area | Owner | Files |
|---|---|---|
| Strategy + history + fees + backtest parity | agent **strategy** | `strategy.py`, `history.py`, `fees.py`, `backtest/` (`replay.py`, `broker.py`), `tests/kalshibot/test_strategy*.py`, `tests/kalshibot/test_parity.py` |
| Market data | agent **marketdata** | `marketdata/` (`kalshi_rest.py`, `kalshi_ws.py`, `btc.py`, `view.py`), `tests/kalshibot/test_marketdata*.py` |
| Execution + risk | agent **execution** | `execution/` (`base.py`, `paper.py`, `live.py`), `risk.py`, `tests/kalshibot/test_execution*.py`, `tests/kalshibot/test_risk*.py` |
| Journal, alerts, dashboard, recorder, packaging | agent **ops** | `journal.py`, `alerts.py`, `dashboard/`, `recorder.py`, `Dockerfile`, `docker-compose.yml`, `config.example.yaml`, `docs/SETUP.md`, `tests/kalshibot/test_ops*.py` |
| Integration | lead | `app.py`, `__main__.py`, `cli` |

Tests run with: `cd /Users/jvidhani/trading/research/kalshi_btc && python3 -P -m pytest tests/kalshibot -q` (`-P`
matters on this Mac: numpy/websockets live in user site-packages). Put a `conftest.py` only in your own test files'
needs; `pyproject.toml` already sets `pythonpath = ["src"]`.

## Contracts

### fees.py (strategy agent)
`taker_fee(price, n) -> float` = ceil(0.07·n·p·(1−p)·100)/100 ; `maker_fee(price, n)` same with 0.0175. Per order.

### history.py (strategy agent)
`BtcHistory(max_days=4)`: per-second price store for the last few days (+ per-minute high/low/close buckets).
- `add(ts, price)`; `add_minute_bar(minute_start_ts, high, low, close)` (startup seeding from 1-min candles)
- `price_at(ts) -> float|None` (last price at or before ts); `extreme(start_ts, end_ts, side) -> float|None` (max for
  "y", min for "n"); `range_2h(ts) -> float|None` ((hi−lo)/mean over [ts−7200, ts)); `covers(seconds) -> bool`.
- Must reproduce `her_trades.py`'s `swept()` / `skip_market()` / `R[op]` numbers on the research data
  (`data/composite_1s.npy`), which is what the parity test checks.

### strategy.py (strategy agent) — pure, deterministic, no I/O, no clock
```python
class Strategy:
    def __init__(self, cfg: StrategyCfg, history: BtcHistory, id_prefix: str = "kb"): ...
    def start_market(self, m: MarketInfo) -> None            # computes range_2h at open; may mark the market "skip"
    def step(self, v: SecondView) -> list[OrderIntent]      # call once per second, s = 0..899
    def on_fill(self, f: Fill) -> None                       # creates/closes Lots
    def on_order_done(self, client_id: str, status: str) -> None   # "cancelled" | "expired" | "rejected"
    def end_market(self, yes_won: bool) -> list[Lot]         # settles remaining lots (no fee), returns all lots
    lots: list[Lot]; state() -> dict; load_state(d) -> None  # persistence across restarts
```
Order of operations inside one `step` (identical to her_trades.py, which runs every second u of the market):
1. if an open position exists and the second's trades show our side traded at ≥ `take_profit` → (TP fill is the
   broker's job: the strategy keeps one resting `limit_sell` at `take_profit` per open lot, placed as soon as the lot
   exists).
2. pending entry bid: if not filled and `s >= expiry` → emit `cancel`; if `limit_fallback`, emit `market_buy` (price
   cap `max_price`, same size) provided the ask is < `max_price` and side rules allow.
3. stops: for each open lot with `mark[side] <= lot.stop` AND BTC lead (our side, vs strike, as a fraction) <
   `stop_lead` → cancel that lot's TP order and emit `market_sell` (reason "stop").
4. entries (only if `first_check_s <= s <= last_entry_s`, `s % check_every_s == 0`, held < `max_held`, not skipped,
   no pending bid, BTC tick not stale): threshold `th = mny_start + (mny_floor−mny_start)·min((s−first)/(end−first),1)`;
   side by moneyness ≥ th; same side unless no open lots (roll); first entry of the market runs the sweep filters
   (skip the whole market if hit); spread ≤ `max_spread` (when a book exists); ask < `max_price`; emit `limit_buy` at
   `ask − limit_offset` with `expires_s = check_every_s` (or `market_buy` if `limit_offset == 0`).
Sizes: mixed = `size_early` if s < `early_until_s` else `size_late`; capped by `max_held − held`.

### backtest/ (strategy agent)
`replay.py` feeds historical seconds (from `data/ktr/*.json`, `data/composite_1s.npy`, `data/settled.json`) through
`Strategy` with `backtest/broker.py:BacktestBroker` that applies her_trades.py's exact fill rules (market buy = 3-s
taker VWAP from s+1; limit bid fills when the tape trades THROUGH it; TP fills when our side's taker VWAP ≥ TP that
second; stop sale = first second with opposite-side prints from s+1, at 1 − their VWAP). `tests/kalshibot/test_parity.py`
must reproduce `results/v8_mixed_f_66d_markets.csv` per market (same entries count, P&L within $0.05) for a sample
of ≥ 300 markets, and the 66-day total within 0.1%.

### marketdata/ (marketdata agent)
- `kalshi_rest.py`: `KalshiRest(key_id=None, key_path=None)`; signing via openssl (copy from kalshi_btc_bot);
  token-bucket rate limit (reads ≤ 8/s, writes ≤ 8/s); methods: `open_markets(series)`, `market(ticker)`,
  `orderbook(ticker) -> Book`, `trades(ticker, min_ts=None) -> list[KTrade]`, `brti() -> (ts, price)` (auth),
  `balance()`, `positions()`, `orders(status)`, `fills(min_ts)`, `create_order(...)`, `cancel_order(id)`,
  `batch_cancel(ids)`. Handle Kalshi's YES-book-only order semantics (buying NO at q = selling YES at 1−q) in ONE
  helper used by execution.
- `kalshi_ws.py`: authenticated WebSocket (`orderbook_snapshot`/`orderbook_delta`, `trade`, `fill`, `ticker`), keeps a
  `Book` per ticker with sequence checks and REST resync; reconnect with backoff. `PollingFeed` fallback (REST book
  + trades once per second) for running without a key.
- `btc.py`: `BtcFeed`: public WebSockets Coinbase, Kraken, Bitstamp, Gemini (no keys) → composite (research weights
  in `data/composite_weights.json`, renormalised over live sources) + BRTI poller (when a key exists) → one
  `BtcTick` per second; `stale` when no fresh source; seeds `BtcHistory` with ≥ 3 days of Coinbase 1-min candles.
  Existing working code to start from: `scripts/paper_trader.py`.
- `view.py`: `ViewBuilder.second(market, now) -> SecondView`: ask/bid from the live book (best levels), mark = the
  qty-weighted YES-implied price of this second's prints converted to each side (same formula as her_trades.py
  `lastp`, carried forward when a second has no prints).

### execution/ + risk.py (execution agent)
- `base.py`: `Broker` protocol — `async submit(intent, market) -> None`, `async cancel(client_id)`,
  `async cancel_all(ticker=None)`, `on_second(view)` (paper only), fills/`order_done` delivered via callbacks
  `on_fill(Fill)`, `on_done(client_id, status)`; `async reconcile(market) -> list[Lot-like dicts]` (live).
- `paper.py`: `PaperBroker` fills against the LIVE book/trades: market buy/sell walk the book (respecting the
  cap/floor); resting bid fills only when a trade prints through its price (back of queue); resting TP sell fills when
  our side trades at ≥ its price; fees from `fees.py`.
- `live.py`: `LiveBroker` on `KalshiRest`: entries = limit, post-only, good-till-cancel; fallback/stop = IOC;
  TP = resting sell (reduce-only); idempotent `client_order_id`s; fills from WS `fill` channel or `/portfolio/fills`
  polling; cancel-all on disconnect / shutdown; reconcile positions + resting orders on startup.
- `risk.py`: `RiskGuard(RiskCfg, journal)`: `allow(intent, view) -> (bool, reason)` (blocks new entries — never
  blocks stops or TP — on daily loss limit, stale data, BRTI/composite divergence, max open orders, kill file);
  `must_flatten() -> bool` (kill file); live mode requires `live_confirm == LIVE_CONFIRM_PHRASE`.

### ops (ops agent)
- `journal.py`: SQLite at `<data_dir>/journal.sqlite`: tables markets, lots, orders, fills, events; methods used by
  app/risk (`record_*`, `realized_pnl(day)`, `open_orders()`), plus `export_dashboard_json(path)` producing the same
  shape the research sheet uses (markets + per-entry rows).
- `alerts.py`: Telegram (token/chat from env) — stop-outs, daily summary, errors, bot down; rate-limited; no-op if
  unset.
- `dashboard/`: static HTML/JS (adapt the research sheet: summary, risk tiles, equity curve, calendar, markets
  table) + a tiny stdlib HTTP server serving it and the exported JSON on `io.dashboard_port`, bound to 127.0.0.1.
- `recorder.py`: append every book delta/snapshot, trade and BtcTick to `<data_dir>/rec/YYYY-MM-DD/HH.jsonl.gz`;
  optional hourly upload to S3 (boto3).
- Packaging: `Dockerfile` (python:3.12-slim + openssl), `docker-compose.yml`, `config.example.yaml` (every setting
  with comments), `docs/SETUP.md` for a non-technical user (Kalshi API key, small cloud server, start/stop, kill
  switch, updates, paper vs live).
