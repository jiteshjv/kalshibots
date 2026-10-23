# kalshibot: what it is, how it runs, and what to check

Written 2026-10-23 for an independent reviewer (human or agent). It describes the trading bots, the evidence behind
them, how they are deployed, and where they could be wrong. Code: `src/kalshibot/` on branch `ec2-live-prep`.

**Status right now**
- **BTC bot: LIVE with real money** since 2026-10-23 7:49 AM PT. 2 contracts per entry, max 100 held per market,
  $300 daily loss limit.
- **Gold bot: paper.** 1 per entry, max 50, $50 daily loss limit. Planned to go live at 1 contract.
- Two "shadow" paper bots and a Kalshi-demo bot also run (see Deployment).

---

## 1. Rules for the reviewer

- **Do not** read, print or copy any `.env`, `*.pem` or `*.key` file, or any Key ID or token.
- **Do not** place, cancel or change orders on Kalshi, and do not press Kill / Disarm / Rearm / Live.
- **Do not** restart the bots or run `deploy/install.py --update`. It restarts every bot, including the live one.
- **Do not** change settings. This is a read-only review. Report findings; the owner decides.
- Run tests locally with `.venv/bin/python -m pytest -q tests/kalshibot` (273 passed, 2 skipped as of commit `316e04b`).
  `tests/` at the top level has an unrelated collection error; ignore it.

---

## 2. The markets

- **KXBTC15M** (Kalshi): every 15 minutes, "will BTC be above the strike at the end?" The strike is the CF BRTI
  60-second average at the start of the window. Settlement is the BRTI 60-second average at the end.
- **KXGOLD15M** works the same way with gold.
- YES + NO pay $1 total. Prices run from 1¢ to 99¢.
- **Price steps:** 0.1¢ steps below 10¢ and above 90¢, whole cents in between. Fractional contracts are allowed.
- **Fees (observed on live fills Oct 22):**
  - Maker fills: $0.
  - Taker fills: exactly 0.07·p·(1−p) per contract, with no rounding to the cent.
  - The backtest and `fees.py` round fees **up** to the cent, which overstates fees at small sizes.

---

## 3. The strategy (BTC)

It is based on a friend's description of her bot. Her real trades are not available. Her order-book and trade data
for Apr–Oct is in `s3://kalshi-btc` (reduced copy in `data_her/`).

The strategy runs once per 15-minute market. It is a pure, deterministic state machine in `strategy.py`, and the
reference implementation is `scripts/her_trades.py`. `tests/kalshibot/test_parity.py` replays 66 days through both
and checks that they agree market by market.

### Skip filters

Checked at market start or at the first entry check.

| Filter | Rule |
|---|---|
| 2-hour range | Skip if BTC's prior 2 h (high−low)/mean > 3.1%. |
| Sweep | Skip if, in the last 5 min, BTC ran through a 3- to 14-day high or low on our side. |
| Jump pause | No entries for 15 min after a 1-minute move > 0.5%. |
| News windows | No entries from the release until 60 min after (jobs, CPI, FOMC). |
| Daily loss limit | No entries for the rest of the UTC day after that loss. |
| Stale feed | No entries if the BTC price or the Kalshi book is older than 3 s. |

### Entry rule (her "risk check")

- Checked every 10 s, from minute 2 to minute 12.
- `Z = |BTC − strike| / (σ × √(seconds left − 40 s))`.
  - σ = the larger of the per-second standard deviations of 5-second BTC changes over the last 5 min and the last
    15 min.
- `risk = 1 − Φ(Z)`, the chance BTC finishes on the wrong side. Enter only if risk ≤ 15%. This is N(d₂) using
  realised volatility.
- Also required: the ask is below 98.5¢ and the spread is ≤ 3¢.
- `risk()` returns None (no entry) unless each window has ≥ 80% of its per-second samples. Otherwise it falls back
  to minute closes.
- The window lengths and the 15% cutoff are our guesses; she did not give them.

### Entry execution

1. A limit bid at the ask minus 0.3¢ (above 90¢) or minus 1¢ (10–90¢), rounded to a valid price step.
2. If it is not filled within 5 s, cancel it and buy at market. The fallback re-checks risk first (`_still_safe`).

### Size

- Equal size per entry (now 2 contracts).
- At most `max_held` contracts open per market (now 100, i.e. 50 entries).

### Exits

- **Take-profit:** a resting sell at 99.7¢ for each lot.
- **Stop (per lot), sell at market when all of these hold:**
  - our side's last trade ≤ the lot's stop price (buy × 0.75), **or** the fair value from BTC ≤ the stop price;
  - **and** BTC is within 0.03% of the strike on our side;
  - **and** the bid is still ≤ the stop price (re-check).
- Stops are checked on every trade print, at most every 100 ms (`fast_stop_ms`), not just once a second.
- If a stop's market sell does not fill, it retries after 1, 2 and 4 s.
- **Roll:** after every lot is stopped out, the bot may enter the other side.
- **Otherwise** the lot is held to settlement.

### Gold differences

- Uses a moneyness line instead of the risk check: the required lead falls from 0.115% at minute 2 to 0.030% at
  minute 15.
- 20% stop, triggered within 0.01% of the strike.
- Take-profit at 99.9¢.
- Skips markets starting 7–9 AM PT, the 2-day+ sweep and news windows (incl. jobless claims). Gold price feed: Pyth,
  with Hyperliquid as a fallback.

---

## 4. Evidence (backtests) and how much to trust it

### BTC, Jun 1–Oct 21, risk rule, 100/entry

| Fill model | P&L | Max drawdown | Losing days |
|---|---|---|---|
| Trade model | **+$349K** | −$9.6K | 26 |
| Her real order book (more realistic) | **+$278K** | −$9.8K | 33 |
| Real book + 100 ms stops | **+$336K** | −$6.8K | — |

Filled limit bids are adversely selected, and the trade model peeks ~2 s ahead, so the trade model overstates.

### Robustness

- **Jan–Aug 10 (out of sample for the older rule):** only ~$891/day vs ~$3.4K/day on the Aug–Oct tuning set, with a
  −$31.9K drawdown.
- Market volume grew ~230× from Jan to Oct. **Aug–Oct numbers are the current regime but likely optimistic.**

### Rejected ideas (all lost money in backtests)

- Chart and trend signals.
- Fair-value-vs-price trading.
- Limit-ladder stops, including speed-gated versions with $0 maker fees: −$2.7K to −$37K vs market stops.
- Adding a moneyness line to the risk rule.
- A cheap-side (<5¢) scalp: −$31.7K.
- ETH at the same rules: +0.9¢/contract, thin and fill-optimistic.
- Early exits before the stop.

### Live and paper results

- **First live session**, Oct 22, 9:00–11:47 PM PT, 1 contract per entry: 11 markets, 211 entries (185 take-profit,
  26 stopped), **+$8.88 after $1.07 fees**. 280 of 432 fills were maker fills.
- **Paper vs backtest gap** on Oct 21–22: paper +$1,157 vs backtest +$2,546 on the same 65 markets. Cause: fair-value
  stops skipped the bid re-check. Now fixed.

### Gold, Aug 3–Oct 20, 40/entry, all gates on

- +$32.7K, max drawdown −$3.0K, 16 of 67 days losing.
- The 7–9 AM gate is the weakest.
- The news-window gate is partly in-sample.

---

## 5. Code map (`src/kalshibot/`)

| File | Role |
|---|---|
| `strategy.py` | Entry, stop, take-profit and roll state machine; risk calc; per-check log (`checks`). |
| `app.py` | Main loop: market roll, per-second step, 100 ms stop task, step lock, resume after restart, Live/Paper switch, shadow mode, dashboard callbacks. |
| `execution/live.py` | Real Kalshi orders: submit, cancel retries, unconfirmed-cancel tracking, bounded shutdown cancel, reconcile. |
| `execution/paper.py` | Simulated fills from the live book and trades. |
| `risk.py` | Daily loss limit, kill and disarm files, open-order cap, live-mode check (confirm phrase). |
| `marketdata/kalshi_ws.py`, `kalshi_rest.py` | Kalshi WebSocket (book, trades, BRTI) and REST. |
| `marketdata/btc.py`, `marketdata/gold.py` | Price feeds. |
| `history.py` | Per-second BTC history (+ disk cache for restarts), minute bars, sweep levels. |
| `journal.py` | SQLite journal (orders, fills, lots, markets, events) and `dashboard.json` export. |
| `recorder.py` | Writes every book message, trade and price to hourly gz files (background thread, bounded queue). |
| `dashboard/server.py`, `dashboard/index.html` | Dashboard: Dashboard / Live / Settings tabs, market chart, buttons. |
| `settings_store.py` | Settings changeable from the dashboard (saved to `data/settings.json`, applied over `config.yaml`). |
| `fees.py`, `config.py`, `types.py` | Fees, config dataclasses, shared types. |
| `deploy/install.py`, `deploy/run.py` | Install and update bots as systemd services; loads the person's `.env`. |

---

## 6. Modes and safety controls

### Modes

- `paper`: simulated fills.
- `demo`: Kalshi demo exchange, fake money.
- `live`: real money. Needs a Kalshi key **and** the phrase `I UNDERSTAND THIS TRADES REAL MONEY`.

**Switching between Live and Paper:**
- Done from the dashboard button: the bot token, plus the checkbox for live.
- The switch writes `data/mode.json` and restarts **only when the bot holds nothing**.
- Live and paper keep separate journals: `journal-live.sqlite` and `journal.sqlite`.

### Safety controls

- **Kill:** cancel everything and sell every position at market, then idle.
- **Disarm:** no new entries; open positions are still managed; then idle.
- **Rearm:** start trading again from the next market.
- **Restart in the middle of a market (live):** positions and resting orders come from Kalshi. If they differ from the
  journal, the bot manages Kalshi's position and makes no new entries in that market.
- **Unconfirmed cancels:** retried outside the step lock, and new entries are blocked until Kalshi confirms them.
- **Shutdown:** cancels entry bids within bounded time limits and leaves take-profit sells resting. systemd allows
  120 s.
- **Balance check:** the bot warns at startup if account shard 2 (where these markets trade) has too little money.

### Reviews already done

18 rounds of adversarial review (Codex) fixed 16 High findings. Round 18 left one Medium: a take-profit configured
below 99¢ would be misclassified at shutdown. Today's TP is 99.7¢, so it is accepted for now.

---

## 7. Deployment

### Server

- AWS EC2 `c7g.medium` in us-east-2, the same region as Kalshi: ~10.6 ms order-book round trip.
- Security group: SSH only from the owner's IP; ports 80/443 open for Caddy.

### Services (systemd)

| Service | Folder | Port | Mode |
|---|---|---|---|
| `kalshibot-jitesh-btc` | `~/kalshibot/jitesh/btc` | 8787 | **live** |
| `kalshibot-jitesh-gold` | `~/kalshibot/jitesh/gold` | 8788 | paper |
| `kalshibot-jitesh-demo-btc` | `~/kalshibot/jitesh-demo/btc` | 8789 | demo |
| `kalshibot-jitesh-btc-shadow` | `~/kalshibot/jitesh/btc-shadow` | 8791 | shadow (paper) |
| `kalshibot-jitesh-gold-shadow` | `~/kalshibot/jitesh/gold-shadow` | 8792 | shadow (paper) |

Also: `kalshibot-journal-backup.timer` (hourly journal copy to S3) and `kalshibot-cleanup.timer` (daily).


### Dashboards and access

- **Ports:** all bot ports listen on localhost only.
- **Caddy:** HTTPS (Let's Encrypt) plus basic auth in front of each dashboard:
  - `btc.13-184-83-254.sslip.io`
  - `gold.13-184-83-254.sslip.io`
  - `demo.13-184-83-254.sslip.io`
- **Login protection:** fail2ban blocks repeated failed logins.
- **Bot token:** buttons and settings changes need it in the `X-Kill-Token` header. It lives in
  `~/kalshibot/jitesh/.env`, shared by btc and gold, and the owner sets it with `sudo set-bot-token`.
- **Shadow pages:** each shadow bot is served read-only at `<host>/shadow/*`. Caddy refuses anything but GET/HEAD
  there, and the shadow bot has no token, so all of its POSTs return 403.

### Shadow bots (`io.shadow_of`)

- Paper only.
- Every skip filter off: hours, 2 h range, sweep, jump pause, news and the daily loss limit.
- Each new market they copy the main bot's `strategy.*` settings from its `settings.json`.
- The main dashboard shows their result on rows the main bot skipped, as "would ±$x", greyed and not counted in
  totals.

### Data

- Recordings go to `s3://kalshi-btc/jv-{btc,gold}/rec/<date>/<hour>.jsonl.gz`.
- An hourly journal copy goes to `jv-{btc,gold}/journal/`. Uploads use an instance role allowed only `PutObject` on
  `kalshi-btc/jv-*`.
- Local recordings older than 7 days are deleted daily.

### Deploying code

- `rsync src/kalshibot/` to `~/kalshibot/app/src/kalshibot/`. Running bots keep their old code until restarted.
- `dashboard/index.html` is read on every request, so page changes go live without a restart.

---

## 8. Known limits and open questions

1. **Backtest optimism.** Most tuning used Aug–Oct 2026, a period of fast volume growth. The earlier period made
   about a quarter as much per day. Several gates (gold news windows, the 7–9 AM skip) were picked partly in-sample.
2. **Her parameters are guesses.** The risk-rule windows (5 s samples, 5 and 15 min, 40 s clock adjustment, 15%)
   are reconstructions, not her real values.
3. **Fill realism.** Paper and backtest fills assume queue position and liquidity. Limit-bid fills are adversely
   selected. At live size this matters more than at 1–2 contracts.
4. **Fee model mismatch.** `fees.py` rounds taker fees up to the cent; Kalshi does not. Maker fees are $0 live.
   Strategy-side P&L estimates therefore differ slightly from the journal.
5. **The take-profit below 99¢ shutdown Medium** (see §6).
6. **Shadow results are paper estimates**, even when the main bot is live. Shadow bots started 2026-10-23 8:36 AM PT;
   older skipped markets have no "would" result.
7. **Demo bot settings.** The demo bot was changed by an `install.py --set btc:...` meant for the paper bot, so it now
   uses 10/500/300. It trades fake money.
8. **Possible dashboard label bug, not confirmed.** One screenshot showed the market-chart dropdown labelled
   "7:45–8:00 AM" while charting the 8:15 market. It may be the selected option not matching the "now" market
   between markets.
9. **Single server, single region.** There is no failover. If the server dies, live positions stay open on Kalshi
   (take-profit sells keep resting; stops do not run).

---

## 9. Suggested review checklist (highest risk first)

1. **Live order safety** (`execution/live.py`, `app.py`):
   - Can the bot ever hold more than `max_held`?
   - Can it double-buy on a timeout or an unconfirmed fill?
   - Can it leave a resting entry bid after Kill / Disarm / shutdown?
   - Can a stop and a take-profit both fill (oversell)?
   - Is reduce-only respected?
2. **Restart and reconcile** (`app._resume`): a restart mid-market with open lots, resting take-profits and pending
   cancels.
3. **Stop logic** (`strategy._stop_intents`, `fast_stops`):
   - Does it match the backtest (`her_trades.py`, CONFIRM=bid)?
   - Edge cases: no bid, a side switch, a lot whose buy has not yet confirmed.
4. **Risk calculation** (`strategy.risk`):
   - Are the sample windows, the 80% coverage rule and the clock adjustment correct?
   - Any look-ahead in the backtest's version?
5. **Mode switching:** can a bot become live without the phrase or token? Is the shadow bot unable to go live?
6. **Daily loss limit and the kill file:** are they enforced on the live journal? Which day boundary (UTC)?
7. **Dashboard and auth:**
   - The token compare is constant-time.
   - Caddy basic auth plus the token are both needed for every POST.
   - The shadow route is GET-only.
   - No secrets appear in `dashboard.json` or the logs.
8. **Backtest validity** (`scripts/her_trades.py`, `test_parity.py`):
   - Look-ahead (the 2 s peek in the trade model).
   - Fill assumptions.
   - Fee rounding.
   - In-sample tuning.
9. **Ops:** systemd restart behaviour, disk usage of recordings, clock sync (chrony), what happens if Kalshi's WS
   drops for minutes while lots are open.

## 10. Where to look for more

- `RESEARCH_RUNBOOK.md`: research scripts and results.
- `BUILD_PLAN.md`, `README.md`, `SETUP.md`: background and setup.
- `deploy/INSTALL.md` and `deploy/CLAUDE.md`: install steps and hard rules for agents.
- `git log --oneline` on `ec2-live-prep`: every fix, including the Codex review rounds.
- Trade-log sheet with the backtest per market and per day: https://claude.ai/artifact/5btJ6R4sX4D8aNzpiJGgjo
