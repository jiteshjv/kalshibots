"""kalshibot app: wires market data -> strategy -> risk -> broker -> journal / dashboard / alerts.

One Strategy instance per market (BTC history is shared). Each second, right after the clock crosses second S:
  1. view = ViewBuilder.second(market, now)          (describes the completed second S-1, see marketdata/view.py)
  2. broker.on_second(view)                          (paper: fills resting orders / earlier market orders)
  3. intents = strategy.step(view)                    (BtcHistory already holds that second's BTC price)
  4. each intent -> RiskGuard (new entries only) -> journal -> broker.submit
Fills and order-done callbacks are routed to the market's strategy by client id. After a market closes its resting
orders are cancelled and it waits for Kalshi's official result (polled), then end_market() settles anything left.

Switches (files in the data dir; the dashboard buttons create them):
  KILL    cancel every order, sell every open position at market (re-sent each second until flat), then go idle.
  DISARM  no new entries and no new markets; open positions in the current market close normally (stops, 99.7c sell,
          settlement), then go idle.
  REARM   (dashboard only) allowed once idle: deletes the switch files and trades again from the next market.
Idle = process keeps running (dashboard, heartbeat, alerts) but trades nothing. If a switch file is present at start the
bot starts idle, so it never re-arms by itself.

Run: python -m kalshibot run --config config.yaml   (paper by default; live needs risk.mode=live + confirm phrase)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import signal
import time
import traceback
from dataclasses import dataclass, field, replace

from zoneinfo import ZoneInfo

from kalshibot.alerts import Alerter, write_heartbeat
from kalshibot.config import Settings, kalshi_credentials
from kalshibot.dashboard.server import DashboardServer
from kalshibot.execution.paper import PaperBroker
from kalshibot.history import BtcHistory
from kalshibot.journal import Journal
from kalshibot.marketdata.btc import BtcFeed
from kalshibot.marketdata.kalshi_rest import KalshiRest
from kalshibot.marketdata.kalshi_ws import KalshiWs, PollingFeed
from kalshibot.marketdata.view import ViewBuilder
from kalshibot.risk import RiskGuard
from kalshibot.strategy import Strategy
from kalshibot.types import Fill, Lot, MarketInfo, OrderIntent

log = logging.getLogger("kalshibot")


def next_ticker(series: str, close_ts: float) -> str:
    """Ticker of the 15-min market after the one closing at close_ts, e.g. KXBTC15M-26OCT202215-15 (close time, New York)."""
    c = dt.datetime.fromtimestamp(close_ts + 900, ZoneInfo("America/New_York"))
    return f"{series}-{c.strftime('%y%b%d%H%M').upper()}-{c.strftime('%M')}"


def _clock(d) -> str:
    """'9:05 PM' with no leading zero on every OS (Windows has no %-I / %-d)."""
    return d.strftime("%I:%M %p").lstrip("0")


def _day_clock(d) -> str:
    """'Wed Oct 28 11:00 AM'."""
    return d.strftime("%a %b ") + f"{d.day} " + _clock(d)
ENTRY_KINDS = ("limit_buy", "market_buy")


@dataclass
class MarketRun:
    market: MarketInfo
    strat: Strategy
    settled: bool = False
    stops_alerted: set = field(default_factory=set)
    entry_info: dict = field(default_factory=dict)     # lot_id -> (moneyness, required threshold) at the entry decision
    skip_saved: bool = False
    blocks: dict = field(default_factory=dict)          # risk-guard block reason -> count (entries refused)
    prefetched: bool = False                            # next market's book/trades already subscribed (recording)
    series: list = field(default_factory=list)          # per second [s, btc, ask_y, ask_n, bid_y, bid_n, vol_y, vol_n]
                                                        #   (market chart; vol = contracts whose taker bought YES / NO)


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.data = settings.data_path
        from kalshibot import settings_store
        self._overrides = settings_store.load(self.data)       # dashboard Settings page (settings.json) over config.yaml
        applied = settings_store.apply(self.s, self._overrides)
        self.journal = Journal(self.data)
        self.alerts = Alerter.from_env(settings.io.telegram_token_env, settings.io.telegram_chat_env)
        self.demo = settings.risk.mode == "demo"             # Kalshi demo exchange (fake money) with the real order code
        from kalshibot.config import DEMO_REST, DEMO_WS
        key_id, key_path = kalshi_credentials(demo=self.demo)
        kw = {"base_url": DEMO_REST} if self.demo else {}
        self.rest = KalshiRest(key_id, key_path, **kw) if key_id and key_path else KalshiRest(**kw)
        sc = settings.strategy                   # history long enough for the longest sweep level checked (3 days minimum)
        self.hist_days = max(3.0, sc.sweep_days + 1, (sc.sweep_max_days + 1) if sc.sweep_max_days > 0 else 0)
        self.history = BtcHistory(max_days=self.hist_days + 1)
        if settings.strategy.series.upper().startswith("KXGOLD"):
            from kalshibot.marketdata.gold import GoldFeed      # Pyth with PYTH_API_KEY, else Hyperliquid oracle; Dukascopy history seed
            self.btc = GoldFeed(self.history, rest=self.rest, data_dir=self.data)
        else:
            self.btc = BtcFeed(rest=self.rest if self.rest.has_key and not self.demo else None, history=self.history)
        ws_kw = {"url": DEMO_WS} if self.demo else {}
        if isinstance(self.btc, BtcFeed) and not self.demo:  # BRTI pushed by the Kalshi WS each second (REST poll = fallback)
            ws_kw.update(brti=True, on_brti=self._on_brti)
        self.feed = (KalshiWs(self.rest, on_book=self._rec_book, on_trade=self._rec_trade, **ws_kw) if self.rest.has_key
                     else PollingFeed(self.rest, book_interval=settings.io.book_poll_s, on_book=self._rec_book,
                                      on_trade=self._rec_trade))
        self._rec_last: dict[str, float] = {}
        self._open_of: dict[str, float] = {}       # ticker -> open time (also for the next market, before the bot switches)
        self.views = ViewBuilder(self.feed, self.btc)
        if settings.risk.mode in ("live", "demo"):
            if self.demo and not self.rest.has_key:
                raise SystemExit("demo mode needs a Kalshi DEMO key: KALSHI_DEMO_API_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH")
            from kalshibot.execution.live import LiveBroker      # refuses unless the confirm phrase matches
            self.broker = LiveBroker(self.rest, settings, on_fill=self.on_fill, on_done=self.on_done)
        else:
            self.broker = PaperBroker(on_fill=self.on_fill, on_done=self.on_done)
        self.risk = RiskGuard(settings.risk, self.journal, data_dir=self.data, open_orders=self.broker.open_order_count)
        self.server = DashboardServer(self.data, settings.io.dashboard_port, kill_file=settings.risk.kill_file,
                                      disarm_file=settings.risk.disarm_file, max_age_s=2.0,
                                      export=lambda p: self.journal.export_dashboard_json(p, meta=self.meta()))
        self.server.on_kill = lambda: self.alerts.kill_switch("dashboard")
        self.server.on_rearm = self.rearm
        self.server.on_get_settings = self.settings_view
        self.server.on_market = self.market_chart
        self.server.on_markets = self.market_chart_list
        self.server.on_set_settings = self.update_settings
        if applied:
            log.info("dashboard settings applied over config.yaml: %s", ", ".join(applied))
        self.recorder = None
        if settings.io.record:
            from kalshibot.recorder import Recorder
            self.recorder = Recorder(self.data, settings.io.s3_bucket,
                                     s3_prefix=settings.io.s3_prefix or f"{settings.strategy.series.lower()}/")
            if isinstance(self.feed, KalshiWs):          # every book message, lossless (her data has the same)
                self.feed.on_raw = lambda d: self.recorder.write("obraw", d)
            if isinstance(self.btc, BtcFeed):            # every exchange trade message + the bot's prices every 100 ms
                self.btc.on_raw = lambda s, d: self.recorder.write("ex", {"s": s, "m": d})
            else:
                self.btc.on_price = lambda p, pub, src: self.recorder.write("gold", {"p": p, "pub": pub, "src": src})
            if hasattr(self.broker, "on_event"):         # live/demo: exact send / ack / cancel / fill times of our orders
                self.broker.on_event = lambda k, d: self.recorder.write("lv_" + k, d)
        self.runs: dict[str, MarketRun] = {}       # ticker -> run (current + those awaiting settlement)
        self.cid_ticker: dict[str, str] = {}
        self.current: MarketRun | None = None
        self.stopping = False
        self._fast_busy = False                    # a stop check started by an arriving trade is still running
        self._fast_task: asyncio.Task | None = None
        self._step_lock = asyncio.Lock()           # one strategy step + its order submissions at a time (main loop,
        #   100 ms checks, trade-triggered checks): a stop must never run while another step's orders are in flight
        self.switch: str | None = None             # None | "disarm" | "kill"
        self.idle = False                          # switch finished: trading nothing until REARM
        self.last_day = dt.datetime.now(dt.timezone.utc).date()

    # ---------------------------------------------------------------- callbacks
    def state_name(self) -> str:
        if self.switch is None:
            return "armed"
        if self.idle:
            return "disarmed (idle)"
        return "killing: selling positions" if self.switch == "kill" else "disarming: finishing positions"

    def live_line(self) -> dict:
        """Current market at a glance: BTC, strike, lead on the leading side, and the lead the rule needs now."""
        run = self.current
        if run is None:
            return {}
        m, now = run.market, time.time()
        p = self.history.price_at(now - 1)
        if p is None:
            return {"strike": f"{m.strike:,.2f}"}
        sec = int(now - m.open_ts)
        lead = (p - m.strike) / m.strike
        need = run.strat.threshold(max(sec, self.s.strategy.first_check_s))
        side = "Up" if lead >= 0 else "Down"
        name = "gold" if self.s.strategy.series.upper().startswith("KXGOLD") else "btc"
        late = "" if self.s.strategy.first_check_s <= sec <= self.s.strategy.last_entry_s else " (no entries now)"
        out = {name: f"{p:,.2f}", "strike": f"{m.strike:,.2f}", "minute": f"{sec // 60}:{sec % 60:02d}",
               "lead": f"{abs(lead) * 100:.3f}% {side}"}
        if self.s.strategy.risk_max > 0:             # her risk check: show the risk now instead of a moneyness line
            rk = run.strat.risk(now - 1, p)
            out["risk_now"] = ("—" if rk is None else f"{rk * 100:.1f}%") + f" (max {self.s.strategy.risk_max * 100:g}%)" + late
        else:
            out["needed"] = f"{need * 100:.3f}%" + late
        return out

    def meta(self) -> dict:
        err = getattr(self.btc, "last_error", "")
        feed = {"price_feed": err} if err else {}
        if getattr(self, "shard_note", None):
            feed["account"] = self.shard_note
        return {**feed, "mode": self.s.risk.mode, "state": self.state_name(), **self.live_line(), "schedule": self.schedule(), "series": self.s.strategy.series, "title": self.s.io.title, "entry_rule": "risk" if self.s.strategy.risk_max > 0 else "moneyness", "market": self.current.market.ticker if self.current else None,
                "risk": self.risk.stats() if hasattr(self.risk, "stats") else {}, "updated": time.time()}

    def _sync_lots(self, run: MarketRun) -> None:
        live = {L.lot_id for L in run.strat.lots}
        for row in self.journal.lots(run.market.ticker):     # provisional entries the strategy dropped (never bought)
            if row["lot_id"] not in live:
                self.journal.delete_lot(row["lot_id"])
        for lot in run.strat.lots:
            mny, th = self._entry_info(run, lot)
            self.journal.record_lot(lot, mny, th)
            if lot.exit == "stop" and lot.lot_id not in run.stops_alerted:
                run.stops_alerted.add(lot.lot_id)
                self.alerts.stop_out_lot(lot)

    def _entry_info(self, run: MarketRun, lot) -> tuple[float | None, float | None]:
        """Moneyness (BTC past the strike on the lot's side) and the required threshold at the second the entry was
        decided: the market order's placed second, else the second before the fill (a resting bid's fill)."""
        base = lot.lot_id.split(".")[0]
        if base not in run.entry_info:
            m = run.market
            s = next((o.placed_s for o in run.strat.orders.values() if o.lot_id == base and o.kind == "market_buy"), None)
            if s is None:
                s = int(lot.entry_ts - m.open_ts) - 1
            p = self.history.price_at(m.open_ts + s)
            mny = None if p is None else (p - m.strike) / m.strike * (1 if lot.side == "y" else -1)
            run.entry_info[base] = (mny, run.strat.threshold(s))
        return run.entry_info[base]

    def on_fill(self, f: Fill) -> None:
        t = self.cid_ticker.get(f.client_id)
        run = self.runs.get(t) if t else None
        if run is None:
            self.journal.event("warn", "orphan_fill", f"fill for unknown order {f.client_id}")
            return
        run.strat.on_fill(f)
        self.journal.record_fill(f, t)
        if getattr(self, "recorder", None) is not None:
            self.recorder.write("fill", {"t": t, "cid": f.client_id, "side": f.side, "act": f.action, "px": f.price,
                                         "qty": f.qty, "fee": f.fee, "fts": f.ts, "maker": f.maker})
        self._sync_lots(run)

    def on_done(self, client_id: str, status: str) -> None:
        t = self.cid_ticker.get(client_id)
        run = self.runs.get(t) if t else None
        if run is not None:
            run.strat.on_order_done(client_id, status)
            self._sync_lots(run)
        self.journal.update_order(client_id, status=status)
        if getattr(self, "recorder", None) is not None:
            self.recorder.write("done", {"t": t, "cid": client_id, "status": status})

    # ---------------------------------------------------------------- market lifecycle
    async def _roll_market(self, now: float) -> None:
        if self.current and now < self.current.market.close_ts:
            return
        if self.current:                                    # close the old one: cancel resting orders, await settlement
            old = self.current.market.ticker
            await self.broker.cancel_all(old)
            self._note_no_trade(self.current)
            self.current = None
        if self.switch:                                     # disarmed / killed: never open another market
            return
        m = await asyncio.to_thread(self.rest.current_market, self.s.strategy.series, now)
        if m is None or m.ticker in self.runs:
            if m is not None and m.ticker in self.runs and not self.runs[m.ticker].settled:
                self.current = self.runs[m.ticker]
            return
        st = Strategy(replace(self.s.strategy), self.history, id_prefix=f"kb{int(now) % 100000}")   # copy: settings saved later apply from the next market
        st.start_market(m)
        if st.skipped == "no price history" and self._feed_down():
            st.skipped = "no price feed"
        run = MarketRun(m, st)
        self.runs[m.ticker] = run
        self.current = run
        try:
            await self._resume(run)
        except Exception as e:                              # never trade blind: manage nothing new in this market
            st.no_entries = True
            self.journal.event("error", "resume", f"{m.ticker}: could not resume after restart ({e}); no new entries")
            self.alerts.error("resume", e)
        self.journal.record_market_open(m, getattr(st, "range2h", None), skipped=getattr(st, "skipped", None) or None)
        await self.feed.set_tickers([m.ticker])
        self.views.reset(m.ticker)
        log.info("market %s strike %.2f", m.ticker, m.strike)

    async def _resume(self, run: MarketRun) -> None:
        """Restart in the middle of a market: take over its lots (journal) and, live, check them against Kalshi.
        Live: positions + resting orders come from Kalshi (reconcile). If Kalshi's position differs from the journal,
        the journal's open lots are marked unknown, Kalshi's position becomes one lot per side and the market gets
        no new entries. Resting take-profit sells are adopted (not re-placed); stray resting buys are cancelled.
        Paper: the journal is the truth; the paper broker is told what is held and the 99.7c sells are re-placed."""
        from kalshibot.journal import _LOT_FIELDS
        t = run.market.ticker
        lots = [Lot(**{k: r[k] for k in _LOT_FIELDS}) for r in self.journal.lots(t)]
        held: dict[str, int] = {}
        for L in lots:
            if L.open:
                held[L.side] = round(held.get(L.side, 0) + L.qty, 2)
        tps: dict[str, tuple[str, int, float]] = {}
        no_new = False
        if self.s.risk.mode in ("live", "demo"):
            recon = await self.broker.reconcile(run.market)
            ex = {h["side"]: h["qty"] for h in recon}
            if ex != {k: v for k, v in held.items() if v}:
                no_new = True
                msg = f"{t}: Kalshi position {ex or 'none'} != journal {held or 'none'}; managing Kalshi's, no new entries"
                self.journal.event("error", "resume_mismatch", msg)
                self.alerts.error("resume", msg)
                for L in lots:
                    if L.open:
                        L.exit = "unknown"
                stop_pct = self.s.strategy.stop_pct
                for h in recon:
                    buy = h["buy"] or 0.0
                    lots.append(Lot(lot_id=f"{t}-R{h['side']}", ticker=t, side=h["side"], qty=h["qty"], buy=buy, fee_in=h["fee_in"],
                                    stop=round(buy * (1 - stop_pct), 4), entry_ts=time.time(), how="reconciled"))
            jo = {o["client_id"]: o for o in self.journal.open_orders(t)}
            for st in self.broker.open_orders(t):
                self.cid_ticker[st.client_id] = t
                if st.action == "buy":
                    await self.broker.cancel(st.client_id)
                    continue
                o = jo.get(st.client_id)
                if o and o.get("lot_id") and not no_new:
                    tps[o["lot_id"]] = (st.client_id, st.remaining, st.price)
        else:
            for side, q in held.items():
                self.broker.restore_position(t, side, q)
            for o in self.journal.open_orders(t):           # the old paper orders died with the old process
                self.journal.update_order(o["client_id"], status="cancelled", reason="restart")
        if not lots:
            return
        run.strat.resume(lots, tps, no_entries=no_new)
        n_open = sum(L.qty for L in run.strat.open_lots())
        self.journal.event("warn" if no_new else "info", "resume",
                           f"{t}: resumed after restart, {len(run.strat.open_lots())} open entries ({n_open} contracts), "
                           f"{len(tps)} take-profit sells adopted" + (", no new entries" if no_new else ""))
        log.info("resumed %s: %d open lots, %d contracts", t, len(run.strat.open_lots()), n_open)

    async def _second(self, now: float) -> None:
        if self.switch != "kill" and self.risk.must_flatten():
            await self._begin_kill("kill file")
        elif self.switch is None and self.risk.disarm_present():
            await self._begin_disarm()
        await self._roll_market(now)
        run = self.current
        if run is None:
            if self.switch and not self.idle and (self.switch == "kill" or all(r.settled for r in self.runs.values())):
                self._finish()
            return
        m = run.market
        view = self.views.second(m, now)
        if view.s < 0 or view.s > 899:
            return
        if not run.prefetched and now >= m.close_ts - 30:       # record the next market from its first second
            run.prefetched = True
            nxt = next_ticker(self.s.strategy.series, m.close_ts)
            self._open_of[nxt] = m.close_ts
            await self.feed.set_tickers([m.ticker, nxt])
        self.broker.on_second(view, m.ticker)
        if not run.series or run.series[-1][0] != view.s:
            bp = None if view.btc is None or not math.isfinite(view.btc.price) else round(view.btc.price, 2)
            vy = round(sum(t.qty for t in view.trades if t.taker_side == "y"), 2)
            vn = round(sum(t.qty for t in view.trades if t.taker_side == "n"), 2)
            run.series.append([view.s, bp, view.ask.get("y"),
                               view.ask.get("n"), view.bid.get("y"), view.bid.get("n"), vy, vn])
        if self.switch == "kill":
            await self._submit_all(run, run.strat.flatten(view), view)
            if not self.idle and not run.strat.open_lots() and not self._selling(run):
                self.journal.event("info", "kill_flat", f"{m.ticker}: all positions sold")
                await self.broker.cancel_all(None)
                self._finish()
            return
        await self._submit_all(run, run.strat.step(view), view)
        if run.strat.skipped and not run.skip_saved:          # sweep filters decide at the first entry check
            run.skip_saved = True
            self.journal.mark_market_skipped(m.ticker, run.strat.skipped)

    async def _fast(self, now: float) -> None:
        """Between whole seconds: run the stop rule on the current second's prints so far (fast_stop_ms)."""
        run = self.current
        if run is None or self.switch == "kill" or not run.strat.open_lots():
            return
        m = run.market
        if not (m.open_ts <= now < m.close_ts):
            return
        view = self.views.partial(m, now)
        intents = run.strat.fast_stops(view)
        if not intents:
            return
        await self._submit_all(run, intents, view)
        if hasattr(self.broker, "work_market_now"):           # paper: sell into the live book now
            self.broker.work_market_now(view, m.ticker)
        self.journal.event("info", "fast_stop", f"{m.ticker}: stop at {now - math.floor(now):.2f} s into second {view.s}")

    async def _submit_all(self, run: MarketRun, intents: list[OrderIntent], view) -> None:
        """Submit one step's intents in order. If an entry is blocked, later intents for the same lot (its 99.7c
        sell) are dropped: that lot was never bought, so there is nothing to sell."""
        blocked: set[str] = set()
        for it in intents:
            if it.lot_id and it.lot_id in blocked and it.kind != "cancel":
                if it.client_id:
                    run.strat.on_order_done(it.client_id, "rejected")
                continue
            if not await self._submit(run, it, view) and it.lot_id:
                blocked.add(it.lot_id)
        self._sync_lots(run)

    async def _submit(self, run: MarketRun, it: OrderIntent, view) -> bool:
        t = run.market.ticker
        if it.kind in ENTRY_KINDS:
            ok, reason = self.risk.allow(it, view)
            if not ok:
                self.journal.record_order(it, t, status="rejected")
                self.journal.event("warn", "risk_block", f"{t} {it.kind} {it.qty}: {reason}")
                run.blocks[reason] = run.blocks.get(reason, 0) + 1
                run.strat.on_order_done(it.client_id, "rejected")
                return False
        if it.client_id:
            self.cid_ticker[it.client_id] = t
        if it.kind != "cancel":
            self.journal.record_order(it, t)
        if getattr(self, "recorder", None) is not None:              # every order the strategy sends, next to the book it saw
            self.recorder.write("ord", {"t": t, "cid": it.client_id, "kind": it.kind, "side": it.side, "qty": it.qty,
                                        "px": it.price, "lot": it.lot_id, "why": it.reason, "mode": self.s.risk.mode})
        await self.broker.submit(it, run.market)
        return True

    async def _settle_loop(self) -> None:
        while not self.stopping:
            await asyncio.sleep(20)
            now = time.time()
            for t, run in list(self.runs.items()):
                if run.settled or now < run.market.close_ts + 45:
                    continue
                try:
                    raw = await asyncio.to_thread(self.rest.market, t)
                    mk = raw.get("market", raw)
                    res = mk.get("result")
                    if res not in ("yes", "no"):
                        continue
                    lots = run.strat.end_market(res == "yes")
                    self.journal.record_market_close(t, res == "yes", lots)
                    run.settled = True
                    pnl = sum((l.pnl() or 0) for l in lots)
                    log.info("settled %s %s pnl %+.2f", t, res, pnl)
                except Exception as e:                       # keep trying next round
                    log.warning("settle %s: %s", t, e)
            for t in [t for t, r in self.runs.items() if r.settled and r is not self.current]:
                self._save_market_chart(self.runs.pop(t))
            await self._settle_journal(now)

    async def _settle_journal(self, now: float) -> None:
        """Results for closed markets the journal still has as pending but this run is not tracking (restarts)."""
        for row in self.journal.unsettled_markets(now - 45):
            t = row["ticker"]
            if t in self.runs:
                continue
            try:
                raw = await asyncio.to_thread(self.rest.market, t)
            except Exception as e:
                log.warning("settle (journal) %s: %s", t, e)
                continue
            res = raw.get("market", raw).get("result")
            if res in ("yes", "no"):
                n = self.journal.settle_orphans(t, res == "yes")
                self.journal.record_market_close(t, res == "yes")
                log.info("settled %s %s from the journal (%d open entries settled)", t, res, n)

    # ---------------------------------------------------------------- dashboard settings / schedule
    def settings_view(self) -> dict:
        from kalshibot import settings_store
        return {"ok": True, "values": settings_store.to_display(self.s), "fields": settings_store.spec_json(), "groups": settings_store.groups_json(),
                "overridden": sorted(self._overrides), "mode": self.s.risk.mode, "series": self.s.strategy.series,
                "note": "Strategy settings apply from the next market; risk settings apply at once. "
                        "Paper/live mode is changed only in config.yaml."}

    def update_settings(self, raw: dict) -> tuple[bool, str, list]:
        from kalshibot import settings_store
        reset = [k for k, v in raw.items() if v is None]           # null = back to config.yaml's value
        clean, errs = settings_store.validate({k: v for k, v in raw.items() if v is not None})
        if errs:
            return False, "nothing saved", errs
        base = Settings.load(getattr(self.s, "_path", None)) if getattr(self.s, "_path", None) else None
        for k in reset:
            self._overrides.pop(k, None)
            if base is not None:
                sec, _, name = k.partition(".")
                setattr(getattr(self.s, sec), name, getattr(getattr(base, sec), name))
        self._overrides.update(clean)
        settings_store.save(self.data, self._overrides)
        settings_store.apply(self.s, clean)
        msg = ", ".join(f"{k}={v}" for k, v in clean.items()) or "no changes"
        self.journal.event("warn", "settings", f"dashboard settings saved: {msg}" + (f"; reset {', '.join(reset)}" if reset else ""))
        log.info("dashboard settings saved: %s", msg)
        nxt = ""
        if self.current is not None:
            nxt = _clock(dt.datetime.fromtimestamp(self.current.market.close_ts, ZoneInfo("America/Los_Angeles"))) + " PT"
        return True, f"saved. Strategy changes apply from the next market{(' (' + nxt + ')') if nxt else ''}.", []

    def schedule(self, days: float = 21) -> dict:
        """Upcoming no-entry windows (news calendar) and the recurring skipped hours, for the dashboard."""
        pt = ZoneInfo("America/Los_Angeles")
        now = time.time()
        f = lambda t: _day_clock(dt.datetime.fromtimestamp(t, pt))
        names = {"jobs": "NFP jobs report", "cpi": "CPI", "ppi": "PPI", "fomc": "FOMC decision", "claims": "jobless claims",
                 "gdp_pce": "GDP / PCE", "retail": "retail sales"}
        up = [{"start": f(a), "end": _clock(dt.datetime.fromtimestamp(b, pt)), "what": names.get(e, e),
               "now": a <= now <= b} for a, b, e in self.risk.upcoming_news(now, days)]
        rec = []
        if self.s.strategy.skip_hours_pt:
            rec.append(f"every day, markets starting {self.s.strategy.skip_hours_pt} PT")
        cal = getattr(self, "_cal_info", None) or {}
        return {"upcoming": up, "recurring": rec, "news_mode": self.s.risk.news_mode,
                "calendar_checked": f(cal["updated"]) if cal.get("updated") else None,
                "calendar_sources": cal.get("sources")}

    async def _update_calendar(self) -> None:
        from kalshibot import newscal
        p = self.risk.calendar_path() if hasattr(self.risk, "calendar_path") else self.data / self.s.risk.news_calendar
        self._cal_info = await asyncio.to_thread(newscal.update, p)

    def _check_shard_funds(self) -> None:
        """Kalshi holds money per exchange shard; BTC / gold markets are on shard 2 (crypto + commodities). An order is
        refused ('insufficient_shard_balance') if that shard has no money, even when the account has some elsewhere."""
        try:
            b = self.rest.request("GET", "/portfolio/balance", auth=True)
            xi = self.rest.exchange_index
            per = {int(x["exchange_index"]): float(x["balance"]) for x in b.get("balance_breakdown", [])}
            if per and per.get(xi, 0.0) <= 0:
                where = ", ".join(str(k) for k, v in per.items() if v > 0) or "none"
                msg = (f"no money on Kalshi exchange shard {xi} (where {self.s.strategy.series} trades); shards with money: "
                       f"{where}. Orders will be refused. Move funds to shard {xi} on kalshi.com (exchange indexes page) "
                       f"or set a target allocation there.")
                self.journal.event("error", "shard_funds", msg)
                self.alerts.error("shard_funds", msg)
                log.error(msg)
                self.shard_note = msg
        except Exception as e:                               # informational: never stops the bot
            log.warning("shard balance check: %s", e)

    def _hist_path(self):
        return self.data / "history_bars.npz"

    def _save_history(self) -> None:
        try:
            self.history.save(self._hist_path())
        except Exception as e:                               # a cache: never fatal
            log.warning("history cache: %s", e)

    async def _housekeeping(self) -> None:
        last_save = time.time()
        while not self.stopping:
            if time.time() - last_save > 300:
                await asyncio.to_thread(self._save_history)
                last_save = time.time()
            write_heartbeat(self.data)
            today = dt.datetime.now(dt.timezone.utc).date()
            if today != self.last_day:
                self.alerts.daily_summary(self.journal.daily_summary(self.last_day))
                self.last_day = today
                try:
                    await self._update_calendar()
                except Exception as e:
                    log.warning("news calendar update: %s", e)
            if hasattr(self.broker, "poll_fills"):
                try:
                    await self.broker.poll_fills()
                except Exception as e:
                    log.warning("poll_fills: %s", e)
            await asyncio.sleep(5)

    def _note_no_trade(self, run: MarketRun) -> None:
        """At the end of a market with no entries, save a short reason (shown as a skipped row on the dashboard)."""
        st = run.strat
        if st.lots or st.skipped:
            return
        if any(k.startswith("news") for k in run.blocks):
            reason = "news"
        elif run.blocks:
            reason = max(run.blocks, key=run.blocks.get).replace("_", " ")
        elif st.jump_blocks:
            reason = "jump pause"
        elif self.switch:
            reason = f"{self.switch}ed"
        elif st.price_blocks:
            reason = f"price > {self.s.strategy.max_price * 100:g}c"
        elif st.spread_blocks:
            reason = f"spread > {self.s.strategy.max_spread * 100:g}c"
        elif st.best:
            reason = "low moneyness"
        elif st.stale_blocks:
            reason = "no price feed" if self._feed_down() else "no price"
        else:
            reason = "late start"
        self.journal.mark_market_skipped(run.market.ticker, reason)

    def _rec_book(self, tk: str, b) -> None:
        """Record every book update (REST poll or websocket), at most one per 0.1 s per ticker."""
        if self.recorder is None:
            return
        now = time.time()
        if now - self._rec_last.get(tk, 0.0) < 0.1:
            return
        self._rec_last[tk] = now
        run = self.runs.get(tk)
        op = run.market.open_ts if run else self._open_of.get(tk)
        s = int(now - op) if op is not None else None
        self.recorder.write("book", {"t": tk, "s": s, "y": b.yes_bids[:10], "n": b.no_bids[:10]})

    async def _rec_px_loop(self, every: float = 0.1) -> None:
        """Every 100 ms: the composite BTC price, BRTI and each exchange's last trade price, as the bot sees them."""
        while not self.stopping:
            await asyncio.sleep(every - time.time() % every)
            now = time.time()
            b = self.btc
            try:
                self.recorder.write("px", {"c": b.raw_composite(now), "b": b.brti_px, "bts": b.brti_ts,
                                           **{s: p.price for s, p in b.px.items() if p.price is not None}}, ts=now)
            except Exception:  # noqa: BLE001
                log.exception("price recording failed")

    def _on_brti(self, ts: float, price: float) -> None:
        self.btc.push_brti(ts, price)
        if self.recorder is not None:
            self.recorder.write("brti", {"ts": ts, "p": price})

    def _rec_trade(self, tk: str, tr) -> None:
        """Record every trade as it arrives (any subscribed market, including the next one before the bot switches)."""
        if self.recorder is not None:
            self.recorder.write("trade", {"t": tk, "ts": tr.ts, "side": tr.taker_side, "p": tr.price, "q": tr.qty})
        run = self.current                         # fast stops: check at once on each print, not at the next 100 ms tick
        if (self.s.strategy.fast_stop_ms > 0 and run is not None and tk == run.market.ticker and not self._fast_busy
                and not self.stopping and run.strat.open_lots()):
            self._fast_busy = True
            self._fast_task = asyncio.get_running_loop().create_task(self._fast_on_trade())

    async def _fast_on_trade(self) -> None:
        try:
            if self._step_lock.locked():               # a step is submitting orders: skip; the 100 ms check follows
                return
            async with self._step_lock:
                await self._fast(time.time())
        except Exception as e:
            self.journal.event("error", "fast_stop", traceback.format_exc()[-800:])
            self.alerts.error("fast_stop", e)
        finally:
            self._fast_busy = False

    # ---------------------------------------------------------------- market chart (dashboard)
    def _chart_of(self, run: MarketRun) -> dict:
        m, st, c = run.market, run.strat, self.s.strategy
        lots = [{"id": L.lot_id, "side": L.side, "qty": L.qty, "buy": L.buy, "stop": L.stop, "how": L.how,
                 "in_s": round(L.entry_ts - m.open_ts, 1), "exit": L.exit, "exit_px": L.exit_px,
                 "out_s": None if L.exit_ts is None else round(L.exit_ts - m.open_ts, 1)} for L in st.lots]
        orders = [{"kind": o.kind, "side": o.side, "qty": o.qty, "price": o.price, "why": o.reason, "placed_s": o.placed_s,
                   "filled": o.filled} for o in st.orders.values() if o.active]
        bk = self.feed.book(m.ticker) if run is self.current and hasattr(self.feed, "book") else None
        book = None if bk is None else {"y": [list(x) for x in bk.yes_bids[:10]], "n": [list(x) for x in bk.no_bids[:10]]}
        return {"ticker": m.ticker, "open_ts": m.open_ts, "strike": m.strike, "side": st.side, "skipped": st.skipped,
                "orders": orders, "book": book,
                "settled": run.settled, "series": run.series, "checks": st.checks, "lots": lots,
                "cfg": {"first_check_s": c.first_check_s, "last_entry_s": c.last_entry_s, "max_price": c.max_price,
                        "take_profit": c.take_profit, "risk_max": c.risk_max, "check_every_s": c.check_every_s}}

    def _chart_dir(self):
        d = self.data / "markets"
        d.mkdir(exist_ok=True)
        return d

    def _save_market_chart(self, run: MarketRun) -> None:
        """Keep each finished market's chart (the dashboard's market dropdown); the newest 200 are kept."""
        import gzip
        import json
        try:
            d = self._chart_dir()
            (d / f"{run.market.ticker}.json.gz").write_bytes(gzip.compress(json.dumps(self._finite(self._chart_of(run))).encode()))
            for f in sorted(d.glob("*.json.gz"), key=lambda f: f.stat().st_mtime)[:-200]:
                f.unlink()
        except Exception as e:                               # a chart must never stop the bot
            log.warning("market chart save: %s", e)

    @staticmethod
    def _finite(o):
        """JSON for the browser: NaN / inf (no price yet) become null."""
        if isinstance(o, float):
            return o if math.isfinite(o) else None
        if isinstance(o, list):
            return [App._finite(x) for x in o]
        if isinstance(o, dict):
            return {k: App._finite(v) for k, v in o.items()}
        return o

    def market_chart(self, ticker: str | None = None) -> dict:
        import gzip
        import json
        run = self.runs.get(ticker) if ticker else self.current
        if run is not None:
            return self._finite(self._chart_of(run))
        f = self._chart_dir() / f"{ticker}.json.gz"
        if ticker and "/" not in ticker and f.exists():
            return json.loads(gzip.decompress(f.read_bytes()))
        return {"error": "no such market"}

    def market_chart_list(self) -> list[dict]:
        live = [{"ticker": r.market.ticker, "open_ts": r.market.open_ts, "now": r is self.current}
                for r in self.runs.values()]
        seen = {x["ticker"] for x in live}
        old = [{"ticker": f.name[:-8], "open_ts": f.stat().st_mtime - 960, "now": False}
               for f in self._chart_dir().glob("*.json.gz") if f.name[:-8] not in seen]
        return sorted(live + old, key=lambda x: -x["open_ts"])[:200]

    def _feed_down(self) -> bool:
        """The price feed reports an error (label skips with the cause, not the symptom)."""
        return bool(getattr(self.btc, "last_error", ""))

    @staticmethod
    def _selling(run: MarketRun) -> bool:
        return any(o.active and o.kind == "market_sell" for o in run.strat.orders.values())

    async def _begin_kill(self, source: str) -> None:
        """KILL: cancel everything now; the per-second loop then sells every open lot at market until flat."""
        self.switch = "kill"
        self.journal.event("error", "kill", f"KILL switch ({source}): cancelling all orders and selling every position")
        self.alerts.kill_switch(source)
        await self.broker.cancel_all(None)

    async def _begin_disarm(self) -> None:
        """DISARM: no new entries / markets; stops and the 99.7c sells keep working until positions are closed."""
        self.switch = "disarm"
        self.journal.event("warn", "disarm", "DISARM switch: no new entries; open positions close normally, then the bot stops")
        self.alerts.error("disarm", "disarmed: no new entries, finishing open positions")
        run = self.current
        if run is not None:
            for it in run.strat.stop_entries():
                await self._submit(run, it, None)

    def _on_signal(self, sig) -> None:
        log.warning("signal %s: cancelling entry orders and exiting (positions and 99.7c sells stay; resumed on restart)", getattr(sig, "name", sig))
        self.journal.event("warn", "signal", f"{getattr(sig, 'name', sig)}: shutting down, entry orders cancelled, positions and take-profit sells left (resumed on restart)")
        self.stopping = True

    def _finish(self) -> None:
        """Switch done: trade nothing (no new market is opened) until the dashboard's REARM."""
        if not self.idle:
            self.journal.event("info", "idle", f"{self.switch} switch done: bot is idle until Rearm")
            self.alerts.error("idle", f"{self.switch} done: no positions being traded; press Rearm on the dashboard to resume")
            log.info("idle after %s switch", self.switch)
        self.idle = True
        if self.current is not None and self.switch == "kill":
            self.current = None                         # the killed market is not traded again

    def rearm(self) -> tuple[bool, str]:
        """Dashboard REARM (runs on the server thread): only once idle; deletes the switch files."""
        if self.switch is None:
            return False, "already armed"
        if not self.idle:
            return False, "still " + self.state_name() + "; try again when it is idle"
        for p in (self.risk.kill_path, self.risk.disarm_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        self.switch, self.idle = None, False
        self.journal.event("warn", "rearm", "REARM from the dashboard: trading resumes from the next market")
        self.alerts.error("rearm", "re-armed: trading resumes from the next market")
        return True, "re-armed: trading resumes from the next market"

    # ---------------------------------------------------------------- main
    async def run(self) -> None:
        mode = self.s.risk.mode
        for name, present, path in (("kill", self.risk.kill_present(), self.risk.kill_path),
                                    ("disarm", self.risk.disarm_present(), self.risk.disarm_path)):
            if present:                                  # never re-arm by itself (e.g. after an automatic restart)
                self.switch, self.idle = name, True
                msg = f"{name.upper()} switch file is present ({path}): starting idle. Press Rearm on the dashboard to trade."
                log.warning(msg)
                self.journal.event("warn", "start_idle", msg)
                break
        self.journal.event("info", "start", f"kalshibot starting in {mode} mode, key={'yes' if self.rest.has_key else 'no (public data only)'}")
        if self.s.risk.mode in ("live", "demo"):
            await asyncio.to_thread(self._check_shard_funds)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):          # Ctrl+C / docker stop: cancel all orders, then exit
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except (NotImplementedError, RuntimeError):
                pass
        self.server.start()                                  # dashboard first: it answers while the history loads
        self.journal.event("info", "history", "loading price history")
        last = self.history.load(self._hist_path(), max_age_s=self.hist_days * 86400)
        thin = last is not None and len(self.history._bars) < 0.5 * self.hist_days * 1380   # e.g. a failed first start
        if thin:
            log.warning("history cache is thin (%d minute bars): reloading the full history", len(self.history._bars))
        gap_days = self.hist_days if last is None or thin else min(self.hist_days, (time.time() - last) / 86400 + 5 / 1440)
        n = await self.btc.aseed_history(self.history, days=gap_days)
        log.info("history: %s minute bars from the cache + %s fetched (%.2f days)",
                 0 if last is None else len(self.history._bars) - n, n, gap_days)
        self.journal.event("info", "history", f"price history ready ({len(self.history._bars)} minute bars)")
        self.alerts.started(mode)
        try:
            await self._update_calendar()
        except Exception as e:                               # calendar download is best effort
            log.warning("news calendar update: %s", e)
        tasks = [asyncio.create_task(self.btc.run()), asyncio.create_task(self.feed.run()),
                 asyncio.create_task(self._settle_loop()), asyncio.create_task(self._housekeeping())]
        if self.recorder is not None and isinstance(self.btc, BtcFeed):
            tasks.append(asyncio.create_task(self._rec_px_loop()))
        try:
            nxt = int(time.time()) + 1
            while not self.stopping:
                fast = max(0, self.s.strategy.fast_stop_ms) / 1000.0     # read each time: dashboard changes apply at once
                due = nxt + 0.05
                await asyncio.sleep(max(0.0, (min(due, time.time() + fast) if fast else due) - time.time()))
                now = time.time()
                if now < due:                                 # between seconds: fast stop check only
                    try:
                        async with self._step_lock:
                            await self._fast(now)
                    except Exception as e:
                        self.journal.event("error", "fast_stop", traceback.format_exc()[-800:])
                        self.alerts.error("fast_stop", e)
                    continue
                nxt = int(now) + 1
                try:
                    async with self._step_lock:
                        await self._second(now)
                except Exception as e:
                    self.journal.event("error", "tick", traceback.format_exc()[-800:])
                    self.alerts.error("tick", e)
        finally:
            self.stopping = True                         # no new trade-triggered stop checks from here on ...
            try:                                         # ... and one already running finishes its orders first
                if self._fast_task is not None and not self._fast_task.done():
                    await asyncio.wait_for(asyncio.shield(self._fast_task), 10)
                async with self._step_lock:
                    pass
            except Exception as e:
                log.warning("shutdown: waiting for a stop check: %s", e)
            try:                                         # resting buys only: the 99.7c sells keep working on Kalshi
                await self.broker.cancel_entries()
            except Exception:
                pass
            if hasattr(self.broker, "settle_inflight"):  # record market buys / stops that filled just before the exit
                try:
                    left = await self.broker.settle_inflight()
                    if left:
                        self.journal.event("warn", "shutdown", f"{left} immediate order(s) without a result at exit "
                                                               "(the restart reconcile checks Kalshi's position)")
                except Exception as e:
                    log.warning("settle_inflight: %s", e)
            self._save_history()
            for t in tasks:
                t.cancel()
            self.btc.stop()
            self.feed.stop()
            self.server.stop()
            if self.recorder:
                self.recorder.close()
            self.alerts.stopped("shutdown")
            self.alerts.close()


def run(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(App(settings).run())
