# Research runbook: KXBTC15M historical backtest

Separate from the bot in `src/kalshi_btc_bot/` (see README.md). This covers the
historical-data research: downloads, analysis scripts, and findings.

## If anything dies (shell, terminal, reboot)

```bash
cd ~/trading/research/kalshi_btc
bash scripts/status.sh     # what's done / running
bash scripts/resume.sh     # restarts anything unfinished; safe to run repeatedly
```

Every downloader resumes where it stopped. Files are written atomically (`.tmp`, then
renamed), so a crash never leaves a half-written file that would be skipped.

## Downloads (started 2026-10-15)

| Job | Script | Output | Log | Resume unit |
|---|---|---|---|---|
| Kalshi trade tape, all KXBTC15M windows Aug 10 – Oct 15 (~6,322) | `scripts/ktrades_all.py` | `data/ktr/<TICKER>.json` (per-second taker VWAP/min/size, Yes and No) | `logs/kalshi_trades.log` | per window |
| Coinbase BTC-USD trades (~38M) → per-second VWAP | `scripts/cb.py` | `data/cb/NNN.json` (48 trade-ID chunks; plan in `data/cb/_plan.json`) | `logs/coinbase.log` | per chunk |
| Kraken XBTUSD trades → per-second VWAP | `scripts/kr.py` | `data/kr/YYYY-MM-DD.json` | `logs/kraken.log` | per day |
| Binance BTCUSDT 1s klines | `scripts/binance.sh` | `data/bn/*.zip`, `data/bn/today.json` | — | complete |

Already complete: `data/settled.json` (6,322 settled markets with BRTI settlement values),
`data/cb1m.json` (Coinbase 1m), `data/kc.json` (Kalshi 1m candles).

Logs are appended with UTC timestamps; `==== (re)start ====` marks each restart.
A `caffeinate` sleep guard (`sleepguard-kalshi`) keeps the Mac awake while downloads run.
Keep the Mac plugged in and online. Rate limits: Kalshi ~9 req/s, Coinbase ~6 req/s, Kraken ~1 req/s.

## Analysis scripts (run from `data/`: `cd data && python3 -I ../scripts/<x>.py`)

| Script | What it tests |
|---|---|
| `test.py`, `trend.py`, `candles15.py`, `inwin.py` | Chart, trend, candlestick and MA signals; in-window accuracy by minute |
| `pnl.py` | Minute-level P&L against Kalshi 1m candles |
| `sec.py` | Second-level fair-value strategy, calibration, Kalshi-vs-BTC lag (output: `results/sec_full.txt`) |
| `fav.py` | Favorite/longshot buying |
| `her.py` | Friend's moneyness + volatility rules with a 95¢ exit and stop variants |

## Findings so far (7-day second-level sample unless noted)

- Pre-window chart, trend and indicator signals: 47–53% over 6,300 windows; no edge.
- "Side BTC is on at minute 12": 85% hit rate, −1.7¢ per contract (priced in).
- Fair-value vs Kalshi price: negative at every delay and threshold; Kalshi is better calibrated.
- Kalshi reacts to BTC within 0–1s; no lag after 2s.
- Friend's rules (0.15% moneyness, 95¢ exit): 81–88% wins, ~31 trades/day, but entries fill ~85¢
  vs ~76¢ implied by her +$300/−$1,200; EV about −1¢ per contract. Execution and entry price look like the edge.

## Next, once downloads finish

1. Merge Coinbase + Kraken + Binance into a per-second composite; fit weights to the 6,322 BRTI settlement values.
2. Rerun `her.py` on 66 days with the composite, taker fills and conservative limit fills
   (count a limit order as filled only when trades print below it).

## Update 2026-10-16 ~01:50 UTC
- Composite BRTI proxy (`scripts/build_composite.py` → `data/prices_1s.npz`; `scripts/calibrate_brti.py` → `data/composite_1s.npy`):
  weights CB 0.65 / KR 0.25 / BN 0.10. On unseen dates: MAE $2.99 vs official 60s settlement, outcome match 98.9%,
  close windows (<$25 move) 95.6%. Results in `results/brti_calibration.txt`.
- Friend-style strategy (`scripts/friend_v3.py`, 29 days, 2,755 windows): roughly break-even. All 72 configs average +0.08¢/contract
  (composite) vs −0.11¢ (Binance); best is about +1.3 to +1.7¢ with optimistic 95¢ fills, about 0 with conservative fills. Win rate
  87–91%, matching hers. Older half +2¢, newer half −2¢. Week to week ranges −5 to +6¢ (noisy). Higher 2h volatility looks better
  (top third about +4¢), but that's suggestive only. Run `scripts/weekly.py` for the breakdown.
- Gotcha: run analysis with `python3 -P` (not `-I`); numpy lives in user site-packages.

## Update 2026-10-16 ~18:10 UTC — full 66-day rerun (all downloads complete)
- `friend_v3.py 67 composite` → `results/friend_v3_composite_66d.txt` (6,321 windows, 66.7 days). `weekly.py 67` → `results/weekly_66d.txt`.
- No config is significantly positive. Optimistic fills: −1.8 to +0.3¢/contract; conservative fills: all negative (−0.3 to −2.5¢).
  The older half (Aug 10 – mid Sep, mostly unseen by the 29-day run) is negative in nearly every config (−1 to −3¢), so the earlier
  +1.3 to +1.7¢ "best" was a recent-period artifact. Weekly P&L swings −7.7 to +5.7¢.
- The volatility lead is gone: 2h-range terciles are −0.4 / −0.2 / +0.2¢ (config A) and −2.0 / −2.1 / −0.6¢ (config B).
- Conclusion: copying her entry/exit rules as a taker doesn't reproduce her P&L. Her edge, if real, is execution (maker fills, lower entry
  prices) or something not in the rules.
- Resting-bid entry (`scripts/limit_sim.py 67 composite` → `results/limit_sim_66d.txt`): after the signal, once the leading side trades
  above L, rest a bid at L (70–78¢), cancel after 60/180/600s, maker fees. Adverse selection kills it: filled bids win ~70–80% vs 86–89%
  for all posted signals. Nothing is significant; the best are 60s-cancel touch fills at about +1 to +2.7¢ ±2.5–3. Longer resting is worse.
  Her claimed combination (~85% wins at ~76¢ entry) isn't reproduced by taker or maker entry. Next: get her real fills.
- User's clarified rule (2026-10-16): once one side is validated, buy at 65–75¢, sell at 95¢. `scripts/band65.py 67 composite` → `results/band65_66d.txt`.
  Taker in 65–75¢: every config negative (−1 to −3¢), win ~73%, median entry minute ~2.8. Maker bids at 65/70/75¢: mostly −2 to +2¢, none
  significant. The only consistent lead: 0.10% signal, bid 75¢, cancel when BTC falls back inside 0.10% → ~30/day, +0.55¢ cons / +1.24¢ touch
  (±1.6–1.7), positive in both halves. Queue position can't be known from the tape, so this needs a live order-book forward test.

## Update 2026-10-18 — rule search and exit grid (date split: train Aug 11–Sep 19, val Sep 19–Oct 2, test Oct 2–15)
- `scripts/features.py` → `data/cands.pkl` (352K candidate entries every 5s, 33 features: BTC lead/z-score/vol/momentum, exchange
  moves, Kalshi taker flow/volume/price moves, hour, previous window). `scripts/rule_search.py` → `results/rule_search.txt`.
- Rule search (10K one- and two-condition rules): the top train rules fail on val; the most robust picks fail or are noise on test.
  Logistic regression and boosted trees predict the win no better than the Kalshi price itself (Brier equal or worse on val and test).
- Pre-registered check (`scripts/flow_test.py` → `results/flow_test.txt`): Kalshi taker-flow and volume filters looked consistent in
  train+val (buy when others were selling our side) but all lose on test (−4 to −12¢).
- Test period Oct 2–15 is bad for her rule: −7¢/contract, 66% wins.
- Exit grid (`scripts/exit_test.py` → `results/exit_test.txt`; 168 combos of TP 85/90/95, stop −15/25/35¢, BTC-cross exit, time exit):
  nothing is positive in all 3 periods. Stops and the cross exit cut variance (±1.4 vs ±2.4) and test loss (−3.5 vs −5.7¢) but don't add
  edge. The 95¢ take-profit is slightly worse than holding to settlement (it gives up the last 5¢ on winners).
- Averaging gap (`scripts/avg_gap.py` → `results/avg_gap.txt`, `data/avg_gap.pkl`): the user says her bot uses "the difference between what
  Kalshi uses and where BTC is". In the last 2 minutes, a model using the locked-in part of the 60s average beats a spot-vs-strike model,
  but Kalshi's price beats both (Brier at 10–30s left: spot 0.023, avg model 0.014, Kalshi 0.007; at 3–10s: 0.025 / 0.011 / 0.001).
  Kalshi's price already follows the average (weights 0.92 avg vs 0.12 spot). Trading avg-fair vs ask loses at every threshold (−1.4 to −12¢).
  Reading: Kalshi traders have a more accurate BRTI than our composite (MAE ~$3, which is exactly the size that decides close windows).
  The real BRTI is computed from order books on its constituent exchanges (Coinbase, Kraken, Bitstamp, Gemini, LMAX, Bullish, Crypto.com, itBit;
  Binance is not a constituent); we only have trades from CB/KR plus Binance.
- Millisecond data download started 2026-10-18 20:58 UTC (`scripts/ms_dl.py`, log `logs/ms_dl.log`): Oct 8–15 Kalshi trades
  (`data/ktr_ms/`), Binance aggTrades (`data/bn_agg/`), Coinbase trades (`data/cb_ms/`). Restart with the same nohup command if it dies;
  it resumes per file.
- Millisecond lag study (`scripts/lag_study.py` → `results/lag_study.txt`; Oct 8–15, 723 windows, 27.8M Kalshi prints, µs timestamps):
  after a sharp move ($20–40 in 0.5s), Kalshi has 87–95% of its move done within 50ms of the Coinbase print, and 93–98% within 100–200ms
  of Binance. ~40% happens BEFORE the Coinbase print (others react to faster feeds). In the first 0–100ms, 80–92% of Kalshi volume buys
  the side BTC moved toward: sub-100ms snipers exist. Her rule at 0ms vs 2s delay: entry 79.5 vs 79.9c, P&L −1.2 vs −1.6c. Speed
  doesn't rescue her rule; the threshold crossing isn't a jump.
- Her 0.6% volatility rule on 66 days (`scripts/vol06.py` → `results/vol06.txt`; 2h high-low range before window, median 0.60%):
  0.10% + range <= 0.6% + 95c exit: train +1.3c, val +3.6c, test −1.9c (±4.7/7.4/11.2), win 77–82%, but only ~6 trades/day.
  >= 0.6% is negative. The best lead so far, but not significant and far fewer trades than her ~30/day.

## Paper trader (built 2026-10-18; real run starts 2026-10-19)
Sends no orders. It reads public data (Coinbase + Kraken websockets, Kalshi REST order book once a second) and logs what the
calm-market rule would do: 2h range <= 0.6%, BTC proxy >= 0.10% past Kalshi's strike after min 2, buy if best ask 65–75c,
exit when our side's bid hits 95c, else settle. Shadow variants (0.15% calm, 0.10% any vol, 0.10% volatile) are logged on the same data.
Cost for 1,500 contracts is worked out by walking the live book. Binance is geo-blocked (HTTP 451), so the proxy uses CB/KR reweighted.
- Start: `bash scripts/paper_start.sh`   Stop: `bash scripts/paper_stop.sh`   Status: `bash scripts/paper_status.sh`
- Report: `python3 -P scripts/paper_report.py` (win%, win% minus entry, P&L/ct, $/trade and $/month at 1,500, proxy accuracy)
- Output in `logs/paper/`: trades.csv, windows.csv, ticks-YYYY-MM-DD.csv (per-second prices + top of book), paper.log, state.json.
  State persists across restarts; the supervisor restarts it on crash; caffeinate keeps the Mac awake. A reboot needs a manual start.
- Goal: ~250–300 'main' trades (~6–7 weeks). Decision: win >= 85% and profitable at 1,500 → small live test; 80–84% → too thin; <= 80% → stop.

## Update 2026-10-19: millisecond stop-out test (scripts/stop_ms.py -> results/stop_ms.txt)
User learned her edge is "how she gets out / her stop out". Oct 8-15, 146 entries (her rule, 65-75c, 100 ms entry delay).
Stops fire on Coinbase or Binance trades (lead below X% past strike, or $D trail from best), exit = first real Kalshi print
on the other side after trigger + delay (median gap ~45 ms). Hold to settle: -1.0c/ct (71% win). Best stops (lead<0-0.02%,
0 ms): -0.5 to -0.8c, win% 41-47%. No stop is positive; 0 ms vs 2 s is worth only ~1-2c. 95c TP made it worse here (-4.0c hold).
Any stop that fires often drops win% far below her 85%, and her -$1,200 avg loss = full stake, so her figures don't fit a frequent stop.
(First run had a closure bug -- every exit read the last window's prints; fixed with default args.)

## Update 2026-10-19: her full rule set (scripts/her_full.py -> results/her_full.txt, data/her_full.pkl)
User's description: moneyness threshold, low volatility, stop out of losers (15% of trades; of those ~10% would have won if held,
~5% "break even"), checks the order book so she can get out. Grid: band 65-75c / 76-85c x mny 0.05-0.20% x 2h range <= 0.4/0.5/0.6%/any
x stop none / drop 15-35c / BTC back at start; 95c TP; taker 1s latency.
- Her fingerprint (81-84% win, 16-20% stopped, loss ~3.5x win) only appears with the 76-85c band + mny 0.125-0.15% + range <= 0.5-0.6%
  + stop "BTC back at start" or "drop 35c". With 65-75c, stops push win% to 57-74% and loss:win to 1.6x -- not her.
- Best of those: 76-85c, 0.125%, <= 0.6%, BTC back at start: ~7/day, train +2.64c±2.38, val +3.78c±3.70, test -1.20c±4.45.
  Positive in 2 of 3 periods like the 65-75c calm lead; the test period (Oct 2-15) is negative for nearly everything.
- 1,500 contracts doesn't fit her $: at ~80c entry a 95c win is ~15c, so +$300 needs ~2,000 contracts; loss 3.5x -> ~-$1,050.
- Follow-up (scripts/her_full90.py -> results/her_full90.txt). User: without the stop the 5% go to 0 and she'd be break-even.
  Then 0.95*300 = 0.05*stake -> stake ~$5,700; +$300 win = 5.3% -> entry ~90c, ~6,300 contracts; -$1,200 loss = ~19c -> stop ~20c below entry.
  Backtest of 86-92c entries matches her whole fingerprint: 86-92c, 0.15%, any vol, drop 20c stop = 31/day, 79% win, 21% stopped,
  64% of stops would have won, loss 4.3x win -- but P&L -0.47 / -0.16 / -0.81c per contract (train/val/test), ~-$30/trade at 6,300 ct.
  Her +$75/trade needs ~+1.2c/ct. Taker fees are ~1c/ct round trip at 90c; maker entries would cut that (gap ~1.7c).
  Calm (<=0.5%) 0.15% drop 20c: 3.5-5/day, +1.73/+1.11/-2.84c. Stops ~neutral vs no stop overall. Curiosity: 0.25% + <=0.5% + 86-92c
  won 100% (~45 trades, +4.4/+4.3/+4.7c) but only 0.6/day.

## Update 2026-10-19: sell at 95c vs sit to settlement (scripts/sit_vs_tp.py -> results/sit_vs_tp.txt)
Same entries and stops; holding to settlement beats the 95c sell by ~+1 to +1.5c/ct in almost every config (at ~90c entry the 95c
sell caps a winner at +5c instead of +10c). First config positive in all three periods with real volume:
  86-92c entry, >= 0.15% past start, any volatility, stop when BTC is back at the start, sit: 30/day, 82% win,
  train +1.46c±1.20, val +1.13c±2.04, test +0.73c±2.32, all 66d +1.27c±0.94. (drop-20c stop version: +1.25/+1.15/+0.34, all +1.07±0.75)
  Matches her stated 0.15% and 30-40 markets/day. ~+1.3c x 6,300 ct ~ $80/trade (her $75). Caveat: chosen after many configs
  (multiple testing), taker 1s latency, size/slippage at 6,300 contracts unknown -> paper trade it.

## Update 2026-10-19: best entry / stop / exit (scripts/mny_sweep.py, exit_grid2.py -> results/mny_sweep.txt, exit_grid2.txt)
Entries only after min 2 (her wait). Moneyness sweep 0.05-0.30%: sweet spot 0.15-0.175% (below ~0.10% the edge fades; above 0.25% too few trades).
Best combined (any volatility): buy 80-95c once BTC >= 0.175% past start, stop when BTC is back at the start (or drop 30c), SIT to settlement:
  ~28/day, 79% win, train +1.37c±1.29, val +2.23c±2.07, test +1.23c±2.41, all +1.52c±1.00 (~+$96/trade at 6,300 ct).
Stops: tight (10-15c) hurt; 25-30c or BTC-back-at-start best. Exits: sit > sell 99c > 97c > 95c (95c is the worst target, ~-1.3c vs sit).
Calm (<= 0.6%) version: ~6/day, +2.4 to +5.6c train/val, test ~0 to +2c, wide CIs.
User: she worries that if others copy her exit, the order book won't support everyone's exit. Exit liquidity at the stop is the untested risk.

## Update 2026-10-19: her own measurement, every-10s snapshots (scripts/snap10.py -> results/snap10.txt, data/snap10.pkl)
Her chat (screenshot): her strategy "isn't just straight up 2h range <= .6, moneyness > .15"; <= 0.6% is "the most profitable bucket because
it's where BTC range sits most often"; she trades up to 3.1% range; enters ~60 markets/day on average; made $16.8K Fri-Sun; 2 accounts;
offered raw data from her S3 for a replay engine.
Snapshot every 10 s from min 2, buy leading side at the real ask, hold, taker fee, CI clustered by window:
- range <= 0.6% & mny >= 0.15%: 10.6 windows/day, avg buy 93.7c, win 95.8%, +1.70 / +2.41 / -0.03c (all +1.56c±1.58).
- by range (mny >= 0.15%): 0-0.4% best, +3.34 / +4.18 / +2.15 (all +3.23c±1.41, positive all 3); 0.4-0.6% +0.93; 0.6-0.8% +1.50;
  0.8-1.0% -0.55; 1.0-1.5% +0.95; 1.5-3.1% +0.13. All <= 3.1%: 38.8 windows/day, +0.59 / +1.73 / +0.44 (all +0.80c±1.01).
- moneyness 0.05-0.10% loses (-0.7 to -1.0c); >= 0.10% mildly positive. Earlier in the window is better (min 2-8 > min 8-13).

## Update 2026-10-19: algo v1 chosen and live in the paper trader (scripts/algo_v1.py -> results/algo_v1.txt)
Strict train+val selection favoured volatility-tiered moneyness, which then lost on test (-0.5 to -1.6c) -> overfit; kept the simple rule.
v1 = 2h range <= 3.1%; after min 2, BTC >= 0.175% past strike; buy leading side if ask 80-95c; stop = sell at bid when BTC is back at
the strike; else hold to settlement. Paper trader (restarted 18:40 UTC) now logs v1, v1_calm, v1_mny15, v1_drop25, v1_tp95,
v1_bookcheck (enter only if bids within 20c cover 1,500) + the old 65-75c variants, into logs/paper/trades_v2.csv with fills at
1,500 and 6,300 contracts and bid depth at entry and at the stop. Old trades.csv kept; paper_report.py reads both.
Backup of the previous trader: scratchpad paper_trader_v1_backup.py (session 6a0c1d4f).

## Update 2026-10-19: limit-order entry for v1 (scripts/limit_v1.py -> results/limit_v1.txt)
v1 signal, then rest a bid k cents below the signal-time ask; fill when the tape trades down to it (cons = through the bid, touch = at it);
cancel after T s or when BTC is back at the strike; optional "then take" at cancel. Maker fee on limit fills.
Taker v1: +1.09 / +2.11 / +1.18c (all +1.32c±1.02). Bid at ask-1c (ask-0c is marketable = taker, ignore it):
  cons, 120 s, then take: 92% of signals traded, +1.79 / +2.66 / +1.92c (all +1.99c±1.09); touch: ~+2.6c. Gain ~+0.7c = mostly maker fee.
Deeper bids (ask-3c/-5c) fill less and fills win less (adverse selection: 81-83% vs 88%) -> worse.

## Update 2026-10-19: recent data Oct 15-19 (scripts/recent_dl.py, recent_ktr2.py, recent_build.py -> data_recent/; scripts/v1_final.py)
data_recent/ = same layout as data/ (379 settled markets, Kalshi tape, CB/KR trades, Binance 1s through Oct 18; Oct 19 uses CB/KR only).
Final v1 rules (official strike, 1c-under bid then take at 2 min, stop = BTC at strike OR our side <= 50c, hold), 1,500 contracts:
  Oct 5-15 (end of the 66-day set): 215 trades, 1c bid +$1,170, taker -$926.
  Oct 15-19 (new data): Fri +$2,046 (22 trades), Sat +$144 (4), Sun +$1,029 (13), Mon to noon PT -$1,834 (16; 6 stops).
  Fri-Sun = 39 trades, +$3,219 (~$83/trade) vs her reported $16.8K Fri-Sun.
Trade page: claude.ai/artifact/FV6rijvJ6eprNaqhT2kRV8 (built by scripts/make_trade_page.py from results/v1_final_*.csv).
stop_v2.py: tighter stops lose; Kalshi price <= 50-60c ~= BTC-at-strike stop (+1.4c/ct over 66 d); confirm-2s / projected-avg add nothing.

## Update 2026-10-19: HER SCALE-IN RULE (scripts/decay_scale.py, scale_stop.py -> results/scale_stop_66d.txt, scale_stop_recent.txt)
Her chat: moneyness threshold "decays to .05 by min 15"; "I buy 100 contracts every entry, up to 50 entries per market. Max 5k per market";
she held 1,600 contracts in the Oct 19 1:00 PM PT market at +0.115% with 8:41 left; buys and waits (not arbing).
Model: every 10 s from min 2, threshold linear from 0.15% (min 2) to 0.05% (min 15); if BTC >= threshold past the official strike and
the ask <= 97c, buy 100 (taker, real prints); same side only; up to 50 entries. Stop whole position at BTC back at strike OR side <= 50c.
66 days: 63 markets/day, avg 1,874 contracts, 27% stopped, +1.28c/ct; $/day train +1,861 / val +1,423 / test +498; all +$1,504/day
(~$45K/month -- her claim $30-40K). Without the stop: +$838/day (test -$881) -> the stop roughly doubles it, as she says.
New data Oct 15-19: Fri +$5,061, Sat +$1,176, Sun +$2,023 (Fri-Sun +$8,260 vs her $16.8K), Mon to noon -$6,932 (16 stops).
This is the first rule that matches her activity, size AND roughly her P&L, positive in all three periods.
- v2 of her rule (scripts/scale_stop2.py -> results/scale_stop2_66d.txt, scale_stop2_recent.txt): per-entry % stop (her avg 68c stop on 90c avg buy
  ~ 25%), buy until 98.5c, re-entry allowed, exit hold vs her resting sell at 99.5c. 66 days: 25% stop + hold +$1,523/day (+1,993/+1,430/+186);
  25% stop + sell 99.5c +$1,506/day (+2,164/+878/+108) -> same; 30% stop + hold +$1,639/day. No stop +$961/day (test -$834).
  Oct 15-19 (her exact: 98.5c, 25%, sell 99.5c): Fri +$4,316, Sat +$1,646, Sun +$2,854 (Fri-Sun +$8,816 vs her $16.8K), Mon to noon -$3,976.
- Limit vs market entries for her scale-in (scale_stop2.py ... x limit -> results/scale_limit_66d.txt, scale_limit_recent.txt): each 100-lot as a bid
  k below the ask, resting <= 10 s, fills only on trade-through, maker fee. 66 days (sell 99.5c): market +$1,506/day; 0.5c under +$1,835/day
  (+2,505/+1,149/+458, 1,575 ct avg); 1c under +$1,179; 2c under +$316. Recent: market +$1,658, 0.5c under +$1,595, 1c +$1,100.
  Limits earn more per contract but fill less and their fills are stopped more often (adverse selection): 18% -> 23-33% of lots.
- Stops check (scale_stop2.py ... x stops): her avg stop ~68c. Model 25%-per-entry: stopped lots avg buy 86.9c, stop level 65.2c, actual sale 58.9c
  (~6c slippage past the stop on every variant). 66 days: 25% per entry +$1,506/day beats one fixed level for all lots (68c +$1,271, 70c +$1,004,
  65c +$1,328, 60c +$1,439). Recent: 25% +$1,658, fixed 60c +$1,657, 68c +$1,324.
- 30% per-entry stop (user's choice, 2026-10-19), sell 99.5c: 66 days +$1,609/day (+2,198/+1,028/+375; 16% of lots stopped, stop level 59.5c,
  sale 54.6c) vs 25% +$1,506. Recent: Fri +$5,090, Sat +$1,464, Sun +$2,690 (Fri-Sun +$9,244), Mon to noon -$4,229; 4-day avg +$1,636/day.
  Working model of her rule from here: 0.15%->0.05% decay, 100/entry, <= 50 entries, buy < 98.5c, 30% stop per entry, sell at 99.5c.
- Rolling + fast stops (scripts/her_sim.py -> results/her_sim_66d.txt, her_sim_recent.txt). User: she can roll to the other side after being
  stopped out if it has moneyness. ROLL = new entries may switch side once all open entries are closed; FAST = stop sells at the first second
  with trades after the trigger (no 1 s wait / 3 s average). 66 days $/day: base +1,605; fast +1,662; roll +1,641 (6.9 rolls/day);
  fast+roll +1,709 (+2,169/+1,236/+764, best in every period); fast+roll+96c cap +1,532. Recent 4 days: base +1,648, fast+roll +1,779,
  Fri-Sun with fast+roll +$9,337. Working model now = fast + roll, 98.5c cap.
- News/time-of-day check (scripts/her_by_time.py -> results/her_by_time.txt, her_markets_66d.csv), working model fast+roll, 66 days:
  worst PT hours 7 AM (-$31.9/market, 26% lots stopped), 6 AM, 3-4 AM, 6 PM; best 1 AM, 10 AM, 7 PM. Weekday 6:30-8:00 AM PT block (US open +
  10 ET data): train -$37.7/mkt, val -$217.7/mkt, test +$20/mkt -> skipping = +$156 / +$891 / -$92 per day: suggestive, not consistent.
  8:30 ET (5:30 PT) and Fed 2 PM ET windows: no clear effect. 15 worst markets are full 4-5K-contract stop-outs spread over all hours/days.
- Sizing / timing (scripts/her_size.py -> results/her_size_66d.txt, her_combo_66d.txt, her_combo_recent.txt): stops hit early cheap entries most
  (<85c: 36% stopped, 98c+: 2%), but early entries earn most per entry; entries after min 12 lose (-$0.44/100). Front-loading = more $ mostly from
  more contracts. 66 days: flat 100 +$1,703/day; 200 before min 6 then 100 +$2,107; + no entries after min 12 +$2,136/day (+2,730/+1,634/+813),
  $849/day per 1,000 ct/market vs $731 flat. Recent: +$2,397/day vs +$1,771.
- Stops: 16.3% of entries stopped; 49% of those would have won if held (cost $391,913), 51% would have lost (saved $455,682): net +$898/day.
- Smarter stop (scripts/her_stop3.py -> results/her_stop3_66d.txt, her_stop3_recent.txt), on the best sizing version:
  current price -30%: 66 d +$2,136/day (wrong stops 50%), recent +$2,397. Best: price -30% AND BTC lead < +0.01%: 66 d +$2,186 (+2,623/+1,620/
  +1,403; wrong 44%), recent +$2,541. Lead < +0.02%: +$2,176 / +$2,512. Confirm 2-5 s, BTC-only, AND at strike: worse. Gains modest (~+$50/day
  overall, mostly in the test period) -> stop is near its best; adopt "price -30% AND lead < +0.01%" as working model.
- Sheet updated to full working model (her_trades.py: 200/100 sizing, last entry min 12, stop = -30% AND lead < +0.01%, fast, roll, sell 99.5c):
  claude.ai/artifact/5btJ6R4sX4D8aNzpiJGgjo. 66 days +$143,953 (~$2,158/day); Oct 15-19: Fri +$6,511, Sat +$1,907, Sun +$3,285
  (Fri-Sun +$11,703 vs her $16.8K), Mon to noon -$2,630.
- her_trades.py 5th arg: mixed (200 before min 6 then 100, default) | equal (100). Equal-100 with the other improvements: 66 d +$111,487,
  Oct 15-19 +$7,679 (Fri +$4,615, Sat +$1,518, Sun +$2,744). Sheet now loads mixed.json / equal.json with a switch
  (scratchpad her_site/; built from results/her_*_{markets,entries}.csv).
- Capital: one market at a time; peak cost held in a market max $4,853 (median $1,903). Peak drawdown -$16,888 (Fri Aug 21 3:30 AM-8 PM PT,
  66 markets); worst day -$6,753 (Sep 28); 16 of 71 days negative; longest losing streak 3 days. Suggest $20-25K account at this size.
- Jan 1 - Aug 10 download started 2026-10-19 ~3 PM PT: scripts/hist_dl.py via hist_start.sh / hist_status.sh into data_hist/ (Kalshi
  /historical/ endpoints: 21,041 markets; Coinbase 139M trades = the slow part). Rest-of-Monday: data_mon/ via recent_dl.py from 17:00 UTC.
- Decay end point (her_stop3.py ... decay -> results/her_decay_66d.txt, her_decay_recent.txt): 0.05% reached at min 12 is worse (66 d +$2,087 vs
  +$2,186; recent +$2,377 vs +$2,541) -- more late entries and stops. 0.06-0.07% at min 12 ~= current (+$2,202/+$2,208), since the current
  line is already at 0.073% by min 12. Keep 0.15% -> 0.05% at min 15 with last entry at min 12.

## Update 2026-10-19: why markets go bad (scripts/bad_entries.py, liq_levels.py -> results/bad_entries.txt, liq_levels.txt, *_features.csv)
Bad = first side fully stopped (22.4% of 4,050 markets; they lose $538K, the rest make $682K). At the first entry, bad markets show more
extended moves our way (1h +0.166% vs +0.113%, 4h +0.178% vs +0.113%), higher 5-min vol, more strike crosses, earlier entries: exhaustion /
mean reversion rather than a trend change. Liquidity: "just ran through the prior 24h high/low on our side in the last 5 min" = 165 markets,
34% bad, -$108/market, negative in train/val/test (-58/-191/-180) -> skip-candidate (~+$270/day). Prior 4h sweep: -$8/mkt (+28/-49/-74).
4h sweep AND top-20% 1h move: 284 mkts, -$53 (-21/-189/-35). Top-20% 1h extension: -$14 (-7/-57/+1). Clean (<= 2 strike crosses): +$53
(+66/+38/+25); choppy (6+): ~$0. Round numbers ($250/$500/$1000): no consistent effect.
- Liquidity filters in her_trades.py (6th arg on|off): at the first entry skip the market if BTC ran through its prior 24h high/low (our side)
  in the last 5 min, or through its 4h high/low with a 1h move our way >= 0.3879% (train-only 80th pct). 66 days mixed: +$2,149 -> +$2,427/day
  (train +2,577 -> +2,622, val +1,713 -> +2,498, test +1,069 -> +1,508); 334 markets skipped. Oct 15-19: only 14 skipped, total +$10,419 ->
  +$8,519 (Fri-Sun worse, Monday -$2,217 -> +$463). Sheet now has Sizing (mixed/equal) and Filters (off/on) switches: 4 JSON files.
- Filters by weekday (mixed): saved Mon-Thu +$15,433 (+$376/day; Wed +$827, Thu +$758), Fri-Sun +$1,298 (+$43/day; 4 of 10 weekends negative,
  Oct 16-18 the worst at -$4,581). Weekend levels (scripts/weekend_levels.py -> results/weekend_levels.txt; fixed levels need an actual cross):
  weekend-so-far high/low sweep 59 mkts -$46 (-8/+114/-187); Friday's high/low 7 mkts (too few); weekday crossing of the previous weekend's
  high/low 67 mkts -$91 (-116/-93/+51). 24h sweep on weekdays 122 mkts -$112 (-65/-278/-134) vs weekends 43 mkts -$95 (-34/+46/-262).
  Weekend-specific levels: suggestive, too few markets. The weekday 24h sweep is the most consistent signal.
- Multi-day levels (scripts/multiday_levels.py -> results/multiday_levels.txt): crossing the PREVIOUS calendar day's high/low (UTC, NY, 5 PM ET
  session or PT midnight) is GOOD: +$76 to +$99/market, positive all periods (breakout continuation). Rolling extremes get worse with length
  (biggest level swept: 1 day only +$33, 2 day -$139, 3 day -$75, 5 day -$257, 7+ day -$296 with 54% bad). Filter 1 changed to the 48h+
  (2-day) rolling high/low: 66 d mixed $161,998 (vs $162,584 with 24h; 306 skipped). Full sheet with filters: mixed $170,518, max drawdown
  -$13,496; equal $129,098, -$10,972.
- 2-day filter definition check (user: does it stay out of a continuing trend?): current version (2-day extreme up to 5 min ago; last 5 min
  beyond it) = 101 skips, -$197/mkt (-122/-301/-348). "Older level, first cross only" versions catch few and are weaker (>= 3 h old: 19 skips,
  -$163; >= 1 h: 27, -$83). The 82 markets the current version skips but a first-cross version would trade made -$16,764 (-$204/mkt, 40% bad):
  markets still pushing fresh 2-day highs are exhaustion, not continuation. It rarely sits out long: 79 skip runs, 63 are a single market,
  longest 5 in a row (75 min). Keep the current definition. Skip reasons now labelled by the longest level (2/3/5/7/14/30-day).
- First vs repeat fresh 2-day highs/lows (66 d): repeats (another fresh 2-day extreme on the same side in the prior 2-12 h, i.e. after a pause)
  are the worst: 70-79 mkts, 42-44% bad, -$234 to -$246/mkt, negative in all periods. First ones: 22-31 mkts, -$19 to -$96. So re-skipping on
  each new high is right; the danger grows with every additional push (exhaustion).
- Near-level entry filter (her_trades.py near_level, NEAR_ATR=0.5, part of FILT): no entry within 0.5 ATR of / beyond the 2-day high/low.
  66 d mixed $161,998 -> $162,960 (+$962); 2,232 entries blocked, skipped markets 306 -> 356. Near neutral.
- Direct stretch (scripts/stretch.py -> results/stretch.txt): BTC vs its 15m/1h/4h average in ATRs at first entry. Bad rate rises mildly with
  stretch (1h: 19% at 0-0.5 ATR -> 28-29% at 1.5-3 ATR; 4h: 19% -> 28% above 3 ATR) but $/market flips sign between periods; skip rules
  (1h >= 1.5/2/2.5/3 ATR, 4h >= 1.5/2/2.5/3 ATR) are all inconsistent across train/val/test. Generic stretch is NOT a usable filter; the
  multi-day level sweep is what carries the signal.
- 2h range cap check (her_trades.py MAXR env, range2h column; MAXR=1 -> results/herR_66d_*): filters on, mixed, 66 d. Positive at nearly every
  range: <=0.5% +$44/mkt (14% entries stopped), 0.5-0.75 +$19, 0.75-1 +$58, 1-1.5 +$77, 1.5-2 -$4, 2-2.5 +$219, 2.5-3.1 -$103 (41% stopped),
  3.1-4 +$162 (28 mkts), >4 +$431 (18 mkts). Totals by cap: <=1% $103K, <=1.5% $149K, <=2.5% $168K, <=3.1% $163K, none $175K. Above 3.1% only
  46 markets, all in the train period (Aug) -> can't judge; stop rate rises with range. Keep 3.1% (her number) until Jan-Aug data.
- Moneyness line check (her_stop3.py ... mny -> results/her_mny_66d.txt, her_mny_recent.txt; working model without the liquidity filters):
  66 d $/day: start 0.10% +1,816, 0.125% +2,171, 0.15% +2,186, 0.175% +1,901, 0.20% +1,481 (all -> 0.05%); end 0.03% +2,198, 0.04% +2,248,
  0.05% +2,186, 0.06% +2,138, 0.07% +2,064 (from 0.15%). Flat optimum around 0.125-0.15% start; end level barely matters. Recent 4 days:
  0.10% best (+3,146) but most volatile. Keep 0.15% -> 0.05%.
- First-check time (her_stop3.py ... first -> results/her_first_66d.txt, her_first_recent.txt): see table; recent 4 days min 2 best (+$2,541), min 0.5-1.5 +$2,396-2,492, min 3 +$2,239, min 4 +$1,650.
  66 d $/day: min 0.5 +2,189, 1 +2,223, 1.5 +2,226, 2 +2,186, 3 +2,054, 4 +1,720. Flat from 0.5 to 2 min (within ~$40/day); later starts cost. Keep min 2 (her rule).
- Entry interval (her_stop3.py ... step -> results/her_step_66d.txt, her_step_recent.txt), 66 d $/day: same buying pace at any interval ~equal
  (2 s +2,086, 5 s +2,136, 10 s +2,186, 20 s +2,215). 5 s at full size = 2x buying: +2,738 (+25% for 2x the contracts; hits 5,000 cap sooner).
  20 s at half pace +1,113. Interval doesn't matter; size does, with diminishing returns. Keep every 10 s.
- Fill realism (mixed, filters on, 600 sampled markets): entries are 5% of the contracts others bought on our side in the 3 s we price at (median;
  89% of entries <= 25%) -> realistic. Stop sales (median 400 ct, max 5,000) vs the one second we price them at: only 56% <= half that second's
  volume, 29% are larger than it -> the big stop-outs are optimistic (would walk the book). See stress test in chat: stops N c worse.
- Her spread rule (no entry if spread > 3c): her_trades.py MAXSPREAD (default 0.03) with a trade-based estimate (lowest YES + lowest NO taker
  price over t-3..t+1, minus $1). The estimate is never > 3c (p99 +1c, median about -1c, since prices move within the window), so it blocks
  nothing: the history has no order books. BTC 15m books are usually 0.1-1c wide; enforce and measure this rule live from the real book.
- Sheet now has 3 filter settings: off / sweep filters (herW_*) / sweeps + near-level (herF_*, NEAR=on). Mixed: $154,373 / $170,518 / $171,510; equal $119,461 / $129,098 / $132,537.
- 2x buying option (STEP=5 env in her_trades.py; results/her5*_): mixed every 5 s, Aug 10-Oct 19: off $194,050 (DD -$20,776), sweeps $215,479 (-$19,801), sweeps+near $219,042 (-$21,350); median capital per market ~$3,500 vs ~$1,800 at 10 s; max $4,868 (5,000 cap). On the sheet as 'Mixed every 5 s (2x buying)'.
- Liquidity (1,000 sampled BTC 15m markets, Aug 10-Oct 15): median 2.29M contracts / $1.08M traded per market (10th-90th pct 1.26M-3.47M);
  min 2-12: 1.20M contracts, of which 207K at 80-98.5c (10th pct only 16K); trades every second; median 1,291 contracts per second. Our median
  position 2,200 = 0.8% of the 80-98.5c volume in min 2-12 (90th pct 1.7%). Quietest 12-4 AM PT (~1.38M), busiest 12-8 PM (~2.7-2.8M);
  weekends ~15% lower. Stop risk: a 5,000-contract stop ~= 4 s of typical volume in one go.
- Gold price build (gold_dl.py duka, Dukascopy XAUUSD ticks read-only from ~/trading/gold cache + fetched Oct 8-19): vs Kalshi gold settlement
  median abs err $0.12, p90 $0.41, outcome match 98.5% (4,335 mkts). Gold vs BTC liquidity (median per market): contracts 278K vs 2.28M (12%),
  $133K vs $1.07M, at 80-98.5c in min 2-12 17.9K vs 205K (9%), 90 vs 1,270 contracts per active second (7%), quiet-market 80-98.5c volume 522 vs
  16.8K. Gold ~73 markets/day (gold hours, weekdays). A 5,000-contract gold position would be ~28% of the 80-98.5c volume -> size must drop ~10x.
- Exit realism. Size-aware stop sales (her_trades.py STOPFRAC env, sell_sized: our stop qty sold across following seconds at <= frac x traded
  volume at each second's price): 66 d mixed sweeps $161,998 (old) -> $161,677 (100%) / $161,340 (50%) / $162,186 (25%): no material change
  (prices after stops bounce as often as they keep falling; own price impact not modelled). 99.5c exit (800 sampled markets): our position
  median 2,000 (max 5,100) vs median 185K contracts bought at >= 99.5c from first touch to close; position <= 10% of it in 99% of markets;
  enough volume within the same second (median); 0.5% of those markets then lost -> the 99.5c exit is realistic.
- Exit price (her_trades.py TP env; results/herTP*_): 66 d mixed sweeps $/day: 99.5c +2,418 (+2,634/+2,398/+1,524, DD -$13,496); 99.7c +2,513
  (+2,696/+2,630/+1,575, DD -$13,161); 99.9c +2,514 (+2,580/+2,770/+1,776, DD -$12,820); hold +2,445 (DD -$12,691). Recent 4 d: +8,106 / +8,627
  / +8,731 / +9,121. Fill check: position vs volume at >= 99.7c after first touch median 1.4% (<= 10% in 98%); at 99.9c median 3.8%, > 100% in
  5% of markets. 99.7c = better in all 3 periods and still easy to fill -> recommended exit.
- Late 99c+ buys (scripts/late995.py -> results/late995.txt + chat checks): BTC >= 0.05% past strike with 30 s left: 4,039 markets in 65 d,
  0 lost, closest finish $9.95, but median price 99.9c and median 0 contracts offered in those 2 s -> ~$3.57 per 1,000 ct; capacity-limited.
- Sheet switched to the 99.7c exit (results/v7_*): mixed off $162,610 (DD -$16,631), sweeps $177,410 (-$13,161), +near $177,869; equal $126,271/$134,668/$137,591; mixed5 $204,546/$224,358/$227,316. Sheet is now shared 'anyone with the link' (user's choice).
- Limit entries (her_trades.py LIM / HYB env; results/lim_*; summary results/limit_entries.txt), mixed sweeps, 99.7c exit, 66 d $/day:
  market +2,513 (DD -13,161, 2,438 ct/mkt); limit only 0.1c under +3,294 (2,408 ct), 0.3c +3,127, 0.5c +3,007 (1,921 ct), 1c +2,272 (1,465 ct);
  limit then market after 10 s: 0.1c +3,321, 0.3c +3,374, 0.5c +3,430 (+3,687/+3,445/+2,256, DD -10,620, 2,406 ct), 1c +3,258.
  Oct 15-19: market +8,627, hybrid 0.5c +10,376. Gain is mostly fees (maker 0.0175 vs taker 0.07) + a little price; hybrid avoids starvation.
  Fill model: resting bid fills only when the tape trades THROUGH it (back of queue). Needs a live test to confirm fill rates.
- Sheet on v8 (results/v8_*): entries = bid 0.5c under, market after 10 s (LIM=0.005 HYB=1 defaults), 99.7c exit. Aug 10-Oct 19 totals: mixed off $234,801 (DD -$14,039), sweeps $240,597 (-$10,620), +near $238,737; equal $179,680/$181,452/$182,670; mixed5 $321,765/$328,787/$328,138. ~70-74% of entries fill as limit orders.
- 2-day sweeps split by distance still to the 5-day level (66 d, filters off): also broke 5-day 48 mkts 48% bad -$250/mkt; still below it 53 mkts 36% bad -$148/mkt; far below (>2 ATR) 46 mkts -$137 (-156/-168/-87 by period). Skipping 2-day sweeps far from the 5-day level still saves money -> keep the 2-day filter.

## 2026-10-19: kalshibot build started (src/kalshibot/)
Foundation written by the lead: ARCHITECTURE.md (contracts, order of operations, file ownership), types.py, config.py
(defaults = working model). Four parallel agents: strategy (strategy/history/fees/backtest parity vs results/v8_mixed_f_66d_*),
marketdata (Kalshi REST/WS with openssl signing, public BTC feeds, ViewBuilder), execution (PaperBroker/LiveBroker/RiskGuard),
ops (journal, alerts, dashboard, recorder, Docker, docs/SETUP.md). Integration (app.py, __main__) by the lead afterwards.
- Gold backtest (her_trades.py in data_gold/ with SIZE_E/SIZE_L/MAXHELD/MNY_START/MNY_FLOOR env; gold 15-min moves ~0.88x BTC's): 57 trading days
  (Jul 31-Oct 19). 20/10/500 scaled thresholds: +$4,156 (+$73/trading day, DD -$1,921, position 0.8% of 80-98.5c volume). 20% of BTC size
  (40/20/1,000): +$8,757 (+$154/trading day, DD -$3,721, median 1.6% / p90 5% of volume, comparable to BTC's ~1.2%).
- Polymarket BTC 15m (scripts/pm_dl.py -> data_pm/mk, tr): slug btc-updown-15m-<start_ts>, resolves on Chainlink BTC/USD 60 s TWAP; ~$15-28K
  volume and 1,300-3,200 trades per market (vs Kalshi ~$1.1M). Download Aug 10-Oct 20 started 2026-10-19 ~7:30 PM PT.
- Gold tuning (scratchpad gold_grid.sh -> results/gg_*, combos results/gc_*; her_trades.py now also takes STOPX, LEADSTOP env), 20% size,
  57 trading days. One-at-a-time vs base (+$154/trading day): lower moneyness better (0.10% +$243, 0.08% +$253 but DD -$8.5K); range cap
  1.2% +$177 (DD -$2.1K); stop 20% +$185 (tighter than BTC's 30%); hold +$175 / 99.9c +$162; filters off +$187 (BTC-tuned filters don't help).
  Combined A (0.10% -> 0.033%, range <= 1.2%, stop 20%, exit 99.9c, filters off): +$262 / +$395 / +$397 per trading day (train/val/test),
  +$327 overall, DD -$3,594. Conservative C (0.132%, same otherwise): +$244, DD -$1,790. Picked on the same 57 days -> overfit risk.
- Gold failure analysis (scripts/gold_fail.py -> results/gold_fail.txt, gold_fail_features.csv; Version A, filters off, 3,158 markets):
  1h/4h sweeps are PROFITABLE in gold (+$8.8 / +$14.1 per market vs avg +$5.9), so BTC's 4h+1h filter is wrong for gold. 2-day+ sweeps lose
  (-$4.5/mkt, negative in all 3 periods). 07-09 AM PT (US open/data) worst hours: -$3.5/mkt, 35% bad. First entries late in the window
  earn ~0. Near the 2-day level (0-1 ATR) loses overall but flips sign by period (unreliable).
  Filter combos (whole-market skips): skip 2d+ sweep AND 07-09 PT -> +$20,601 (vs +$18,658), DD -$3,594 -> -$2,134 (11 -> 5.9 days),
  better in all 3 periods. Adding the 0-1 ATR skip raises total but hurts test -> not adopted.
- Gold price gap: Dukascopy returned 429 from ~Oct 8 and gold_dl.py stored those hours as empty -> backtests ended Oct 7. Fixed (429 = retry,
  only 404 = empty); scripts/gold_duka_retry.sh retries every 30 min (logs/gold_duka_retry.log). PAXG fill (scripts/gold_hybrid.py) rejected:
  89.8% outcome match vs Dukascopy 98.5%. Pyth history needs a key (Hermes 401). Kalshi KXGOLD15M starts Jul 31, nothing older.
  Gold sheet v2: Version A / C x No filters / Gold filters (A+f: +$20,601 DD -$2,134; C+f: +$14,990 DD -$1,191), Aug 3 - Oct 7.
- News calendar: data_news/calendar.csv (BLS jobs/CPI/PPI, BEA GDP/PCE, retail Oct 15 only, FOMC 2026 from federalreserve.gov; BLS blocks curl,
  dates via WebFetch). scripts/news_check.py -> results/news_check.txt. Dates verified on gold 1 s prices: 60 s move at release vs normal
  days: jobs 41x, CPI 30x, FOMC 20x, GDP/PCE 7x, PPI 2.3x. P&L: only 9 gold / 10 BTC release days and 1 FOMC (Sep 16) in data -> no
  reliable signal. Gold 5:30 window on release days +$7/mkt (normal -$16); 5:45 -$77 (4 mkts). BTC FOMC Sep 16: 11:15+11:30 windows -$2,742
  (all stopped), 11:45+12:00 +$2,161. Retest on Jan-Aug BTC (~35 more release days, 5 FOMC). (BTC jump check printed nothing: data_recent
  doesn't cover the release times; use the 66-day folder.)
- Does the 2h range cap catch news? It is measured at window open, so the window containing the release is never caught. Gold (1.2% cap):
  blocks 26 of 40 windows in the hour AFTER jobs/CPI/FOMC releases (jumps push the 2h range to 1.2-2.5%). BTC (3.1% cap): blocks 0 of 60;
  BTC's 2h range after releases is 0.5-2.8%, so the cap never fires on news.
- Warsh sell-off (Trump named Warsh Fed chair Jan 30 2026; gold worst day in 40+ years; no specific Feb Warsh speech dates found by search).
  Kraken BTC: $87K (Jan 29) -> $61K (Feb 5); daily ranges 8-18% Jan 29-Feb 6; 11 of the 25 largest 15-min moves of Q1 fell Jan 29-Feb 8.
  The 3.1% 2h cap skips only 0-18% of windows on those days (49% on Feb 5). Key test for the Jan-Aug backtest: P&L Jan 29-Feb 8.
- Fast volatility (scripts/fast_vol.py -> results/fast_vol.txt, herR_66d): range 15/30 min before open, 5 min before first entry, 30m/2h.
  Stop rate rises with fast vol (12% -> 55%) but $/market does not fall (big leads pay more). Skip rules on top of the 3.1% cap: best
  r30 > 1.2% (total -$500, DD -$13.6K -> -$12.0K), r15 > 1.0% (+$2K, same DD); others cost $5-20K. No reliable gain -> not adopted.
- Fast move x direction (BTC herR_66d, cap 3.1%; fast = 30-min range before open in top 10%, > 0.70%): WITH our first side +$105/mkt
  (41% stop; +49/+113/+297 train/val/test), AGAINST our side +$22/mkt (44% stop; +89/-80/-99). Stop slippage is NOT worse after fast moves
  in the tape model (median 9c calm vs 6c fast). Rule "skip if 30m range > 0.70% and the 30m move is against our side" (197 mkts):
  val/test per day +2,613 -> +2,828 and +1,535 -> +1,787, train 2,682 -> 2,414, total -$4.4K, DD -$13.6K -> -$11.7K. Candidate; retest Jan-Aug.
- Gold markets on Kalshi: KXGOLD15M starts Jul 31 2026 (paged /historical/markets: earliest close 2026-07-31T18:15Z); KXGOLDH from Jul 7.
- Size-limited stop fills (scripts/stop_fill.py -> results/stop_fill.csv; v8_mixed_f_66d): her_trades sells a whole stop at the first
  second's VWAP. Walking the tape taking 100/50/25% of each second's prints changes the average stop fill by <0.5c even in fast markets
  and for >3,000-contract stops (BTC tape volume per second is large). NOT captured: our own market impact (no historical book) and
  intra-second price collapse. Stress: +5c on every stop = -$77K over 66 days; +5c only on fast-market stops = -$17K; +10c fast only = -$34K.
- Market history limits: KXBTC15M (the only Kalshi 15-min BTC series) starts Wed Dec 10 2025 (ticker probes; same launch as ETH).
  Before that only hourly/daily BTC (KXBTCD/KXBTC, multi-strike). Polymarket btc-updown-15m starts between Oct 6 and Oct 15 2025
  ($1K-31K volume per market). No 15-min market data covers a strong BTC uptrend; Oct-Nov 2025 (Polymarket) is a strong downtrend.
- kalshibot paper run started 2026-10-19 9:04 PM PT on this Mac: config.paper.yaml (paper, record: true), data ~/kalshibot-data,
  log logs/kalshibot_paper.log, dashboard http://127.0.0.1:8787. No Kalshi key -> public polling (occasional 429 on trades).
- PARKED (user, 2026-10-19 ~9:30 PM PT): synthetic price-only backtest for strong-uptrend periods. Plan: fit Kalshi price vs moneyness /
  time left / vol on real data, write synthetic ktr files, run her_trades.py unchanged, validate vs real Aug-Oct, then 2024-2025 trends.
  BTC 1 s Binance archive Oct 2024 - Nov 2025 downloading to data_synth/bn (scripts/synth_dl.sh, logs/synth_dl.log).
- Stop variants (results/stg_*, 66 d, mixed, filters on) vs current 30% / BTC within 0.01% (+$229,784, DD -$10,620, 90th-pct one-second
  stop block 2,300): 25% +$233,904; 35% +$224,408; strike touch +$216,297; BTC within 0.03% +$229,442, DD -$8,968, block 1,400;
  within 0.05% +$223,981, DD -$9,242, block 1,100. Within 0.03% = same profit, smaller DD and stop blocks -> candidate (retest Jan-Aug).
- kalshibot switches (2026-10-19): KILL = cancel all + sell every lot at market each second until flat, then idle; DISARM = no new
  entries/markets, current positions managed to the end, then idle; REARM (dashboard, token) only when idle. Switch file at start ->
  start idle. SIGINT/SIGTERM now cancel all orders and exit. Tests 199 pass. Paper bot: scripts/paper_start.sh (pid ~/kalshibot-data/
  bot.pid, token ~/kalshibot-data/dashboard_token.txt), switch check scripts/switch_test.sh.
- Gold Sep drawdown (Version A, Sep 1-10 -$2,448): not 2h volatility (median 0.54% vs 0.51%); full stop-outs 34% vs 26%. Phase 1 Sep 1-4
  whipsaw days after the Aug 28 -3.5% crash (-2.4%, +2.3%); phase 2 Sep 7-9 low-range directionless chop. 7-9 AM PT worst block (-$1,758);
  gold filters cut Sep 1-10 to -$585 (DD -$3,594 -> -$2,134). Idea (untested): day-level regime check (24h range vs 2-week average).
- Gold levels ahead of our side at first entry (scripts/gold_levels.py -> results/gold_levels.txt): round numbers $25/$50/$100,
  yesterday / 7-day / Asia / London highs-lows. Nothing consistently bad: $100 within $2 +$15.5/mkt; $25 within $2 +$0.7 (31% stop);
  7-day level near/through ~$0/mkt (33-34% stop, 111 mkts, covered by the 2-day sweep filter); London level already broken ~$0/mkt
  (752 mkts). No new gold level filter.
- Gold sweep-and-reverse (scripts/gold_sweeps.py -> results/gold_sweeps.txt): level broken in the last 5/15 min at the first entry, WITH our
  side (we bet continuation) vs AGAINST. Yesterday / Asia / London / $25 / $50 breaks WITH us are positive (Asia 5 min +$24/mkt, $50 5 min
  +$16.5): within a 15-min window gold mostly continues. Only the 7-day high/low reverses: WITH us -$1.6 (5 min) / -$5.7 (15 min) per
  market, 32-34% stop. Candidate: extend the 2-day+ sweep filter lookback from 5 to 15 min for gold (test on Oct 8-19).
- Kalshi 15-min series scan (2026-10-19, 7-day settled volume, contracts per market): BTC 3.01M, gold 416K, ETH 203K, WTI 112K, XRP 92K,
  SOL 88K, silver 54K, DOGE 46K, HYPE 31K, ZEC 27K, NEAR 25K, natgas 23K, BNB 20K, copper 20K, palladium/platinum ~7K, FOREX: EURUSD 5.6K,
  USDJPY 4.6K, GBPUSD 3.9K, USDCAD 3.4K, AUDUSD 2.5K (forex since Sep 23 / Oct 7). Treasury yields 15M: ~0-40. Stock indexes KXINX15M /
  KXNDQ15M / KXDJIA15M: series exist but no markets ever listed (not launched).
- Gold first entry check (her_trades.py FIRST env, threshold falls from MNY_START at FIRST to MNY_FLOOR at 900; results/gf_{A,C}_{60..300},
  filters off, through Oct 7): Version A min 1 / 1.5 / 2 / 3 / 5: +$18.7K / +$19.0K / +$18.7K / +$17.7K / +$14.2K, DD -3.9 / -3.8 / -3.6 /
  -3.4 / -2.1K. Version C: +$14.7K / +$14.4K / +$13.9K / +$12.8K / +$10.1K, DD -1.85 / -1.84 / -1.79 / -1.60 / -0.94K. Minute 1-2 flat (noise);
  later = less profit and less drawdown. Keep minute 2.
- BTC threshold anchor (her_trades.py ANCHOR env = second where threshold = MNY_START; FIRST = first check; results/anc_*, 66 d, mixed,
  filters on): now (check min 2, 0.15% at min 2) +$229.8K DD -$10.6K; 0.15% at min 0, check from min 2 (0.137% at min 2) +$244.8K DD -$12.2K,
  better in train/val/test (+145/+517/+160 per day); check from min 1 +$237.9K DD -$11.9K; check from min 0 +$245.7K DD -$12.6K.
  Same as a lower starting threshold. Candidate; retest on Jan-Aug (kalshibot would need a mny_anchor_s setting).
- Exit difficulty (66 d, BTC): stopped contracts / one-second stop block p90 / p99 / profit per stopped contract: now 1.54M / 2,300 /
  5,000 / 15c; 0.15% at min 0: 1.87M / 2,300 / 5,000 / 13c; stop within 0.03%: 1.66M / 1,400 / 3,800 / 14c (tot/DD 25.6); within 0.05%:
  1.70M / 1,100 / 3,200; combo min0 anchor + 0.03% (results/anc_a0_l3): +$247.6K, DD -$10.0K, tot/DD 24.8, 2.01M / 1,300 / 3,800 / 12c.
  Each 1c of real stop slippage costs ~$15K (now) - $20K (combo) over 66 days.
- BTC first check with stop BTC within 0.03% (results/bf_*, 66 d): min 1 / 1.5 / 2 / 2.5 / 3 / 4 / 5: total +237.5 / +233.9 / +229.4 /
  +218.0 / +204.0 / +162.6 / +118.2K, DD -9.7 / -9.3 / -9.0 / -8.8 / -8.2 / -7.9 / -6.9K, tot/DD 24.5 / 25.2 / 25.6 / 24.8 / 24.7 / 20.5 / 17.2,
  stopped 1.94 / 1.81 / 1.66 / 1.52 / 1.38 / 1.12 / 0.84M. Minute 2 = best total/DD; keep it.
- False-flag stops (stopped entry's side then won; 66 d): now 42% of stops (cost $331K of payouts given up, correct stops saved $421K, net
  +$91K); 0.03% 47% (net +$92K); 0.05% 48% (+$87K); stop 25% 43% (+$95K); 25% + 0.03% (results/stg_s25_l3) 50% (net +$90K), total
  +$226.4K, DD -$8.2K, tot/DD 27.5 (best). Wider trigger = more false flags but stops still pay the same net; smaller drawdown.
- JAN 1 - AUG 10 BACKTEST (data_hist/, scripts/run_hist.sh -> results/jh_<variant>_<month>; friend_v3.py now takes START_DATE/END_DATE):
  13,457 markets, 223 days. now: +$198.8K = +$891/day (vs ~$3.4K/day Aug 10-Oct 19), max DD -$31.9K (Feb), tot/DD 6.2, worst day
  -$12.7K, 6.42M stopped contracts, only 3.1c profit per stopped contract (each 1c of stop slippage ~ -$64K). stop 0.03%: +$195.5K,
  DD -$26.5K, 7.4; 25% + 0.03%: +$221.6K, DD -$24.2K, 9.2 (best); no cap: +$203.1K, DD -$36.6K; filters off: +$172.7K, DD -$38.4K.
  $/market by month: Jan 9, Feb 2, Mar 16, Apr 12, May 27, Jun 19, Jul 20, Aug 1-10 ~5 | tuned set Aug 10+ 52, Sep 65, Oct 45.
  Data checked: composite vs Kalshi strike 0.004% median every month; historical-endpoint trades identical to tuned-set files (Aug 13/15/18).
  => The tuned 66 days were the strategy's best period; realistic expectation is far lower. Warsh Jan 29 - Feb 8: now +$1.8K (Feb 4 -$7.7K,
  Feb 6 -$3.8K, Feb 8 +$10.8K); no cap -$3.9K (the 3.1% cap helped).
- Stop re-check at the sell second (her_trades.py CONFIRM=bid|btc|both; results/cf_*, 66 d): bid re-check +$1.5K (0.01%) / +$2.3K (0.03%),
  same DD; btc/both no better. Small but positive. Oct 19 11:00 PM paper market: 4 false-flag stops at 65c (bid had bounced above 61-63c stops).
- KXBTC15M volume growth (median contracts per market, all prints): Jan 12K, Feb 89K, Mar 169K, Apr 221K, May 344K, Jun 798K,
  Jul 1.41M, Aug 1.84M, Sep 2.34M, Oct 2.88M (230x Jan->Oct). Our buying = 5.7% of volume in Jan, 2.7% Feb, 1.4% Mar, <=0.4% from May,
  0.1% Jul-Oct. Strategy $/market rises with volume (Jan 9, Feb 3.5, Mar 15, Apr 13, May 26, Jun 19, Jul 21, Aug 51, Sep 66, Oct 46).
  Jan-May = a much thinner, different market; Aug-Oct (1.8-2.9M/market) is the current regime. Caveat: only ~2.5 months of it.
- BTC volatility / news / sharp-move study, Jun 1 - Oct 19 (scripts/run_vol.sh no-cap runs jh_/v9_{nc,ncs}; scripts/vol_study.py ->
  results/vol_study_{nc,ncs}.txt/.csv). Higher volatility = more stops (21% -> 59% of markets) but NOT less profit, except the top 1%
  of "biggest 1-min move in the 15 min before the window" (>0.43%): -$150/market, 64% stopped. Skip rules on top of the 3.1% cap
  (25% + 0.03% stop): 1-min move > 0.4%: +$331.5K vs +$320.0K, DD -$16.8K vs -$19.6K, better in Jun-Jul AND Aug-Oct (also with the
  current stop). NFP/CPI/FOMC release + next hour: ~neutral (-$0.4K, DD -$1.1K). Claims, US morning, relative 2h/24h volatility: hurt or
  inconsistent. 2h cap 1.5%: -$37K total but DD -$13.3K and 27% fewer stopped contracts (option if exits are hard).
  Best combo: 3.1% cap + 1-min > 0.4% + NFP/CPI/FOMC hour: +$329.2K, DD -$16.1K, tot/DD 20.4 (vs 16.3).
- In-window sharp-move rule (her_trades.py JUMP env: no new entries / fallback buys if any clock minute in the last 15 moved > JUMP;
  scripts/run_jump.sh -> jh_/v9_ j4, j5; 25% + 0.03% stop, 3.1% cap): JUMP 0.5% +$323.8K DD -$17.0K (19.0); 0.4% +$318.2K DD -$16.9K.
  + NFP/CPI/FOMC release + next hour: j5 +$323.3K, DD -$15.7K, tot/DD 20.6 (vs base 16.3). Caps on top: 2.5% +$317.5K / -$15.5K;
  1.5% +$280.0K / -$13.3K (21.0), 26% fewer stopped contracts. The 1.5% cap removes 959 markets (12%) worth +$43.4K (+$45/mkt, 48% had a
  stop vs 27%), positive in both periods -> keep 3.1%. RECOMMENDED: 25% + 0.03% stop, bid re-check, 3.1% cap, JUMP 0.5% in window,
  NFP/CPI/FOMC hour off.
- Key level / cooldown / chop study on the recommended setup (scripts/level_study.py -> results/level_study.txt, Jun 1 - Oct 19, news hour
  removed): 2-day or 5-day level AHEAD within 0.5 / 1 ATR: -$1K to -$6K, DD same or worse (0-0.5 ATR is positive, 0.5-1 ATR ~$0 / -$28
  per market: no clean edge). Cooldown after a 2-day break (30/60/120 min): markets after breaks are profitable (30-60 min after: +$89/mkt),
  -$25K to -$75K. Chop (10+ strike crosses before first entry): -$3K total, DD -$15.7K -> -$13.8K, tot/DD 23.2, but Jun-Jul better and
  Aug-Oct worse. None adopted; chop 10+ is a possible risk reducer (or size-down) to revisit with more data.
- GOLD OUT OF SAMPLE (Oct 8-19, 10 days; Dukascopy now to Oct 19 2 PM PT, 98.5% outcome match; results/gn_A, gn_C, gold_news.txt):
  Version A + gold filters: +$2/day (total +$15, DD -$1,463) vs +$361/day on the tuned Aug 3 - Oct 7; $/market 0.0 vs +7.5.
  Version C + filters: -$18/day. Days: Oct 12 -610, Oct 14 (CPI) +897, Oct 15 (PPI/retail) -1,075. News rules: tiny effects (gold cap
  already blocks most post-release windows). => Gold settings did not carry over; do not trade gold yet.
- Gold news windows ADDED to the gold rules (sheet option "+ news"): NFP/CPI/claims 5:15-6:30 AM PT (window before, release, next hour),
  FOMC 11:00 AM-12:00 PM PT. Version A + gold filters + news: +$21,853 (vs +$20,616), DD -$2,126; NEW Oct 8-19 +$129/day (vs +$2).
  Gold trades the window before and the release window; the 1.2% cap then blocks most of the next hour for NFP/FOMC (not claims).
- Sweep filters before rolls (her_trades.py ROLLFILT=1/2, results jh_/v9_ rf1, rf2; recommended setup + news hour): +$322.5K / +$323.5K vs
  +$323.3K, DD unchanged (~-$15.7K); 29-35 fewer rolled markets. No effect: Jun 9 9:30 AM still rolls, because at the roll (9:38:22)
  the 2-day low ($60,804) had not been broken yet; BTC broke it at 9:39-9:40 ($60,738) and reversed. Not adopted.
- Gold sheet: skipped rows now labelled (7-9 AM PT / 2-day+ sweep / news window).
- Gold sheet now at 2x size (80/40, max 2,000; results/gn2_A, gn2_C; scripts/build_gold_sheet.py) with the BTC sheet's metrics.
  Version A + gold filters + news: +$44,304, DD -$4,186; Version C + news: +$32,043, DD -$2,378. Trading size to be decided later.
- Sizing with the recommended rules (Jun 1 - Oct 19; jh_/v9_ eqrec, m5rec; sheet: Recommended works for all sizings):
  Mixed +$323.3K, DD -$15.7K, tot/DD 20.6, stopped 4.13M, block p90 2,000; Equal 100 +$250.4K, DD -$10.9K, tot/DD 22.9, stopped 3.22M,
  block p90 1,500, worst day -$6.0K; Mixed every 5 s +$451.9K, DD -$24.5K, 18.5, stopped 6.56M, 6.9c per stopped contract.
