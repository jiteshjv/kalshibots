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
import signal
import time
import traceback
from dataclasses import dataclass, field

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
from kalshibot.types import Fill, MarketInfo, OrderIntent

log = logging.getLogger("kalshibot")
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


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.data = settings.data_path
        self.journal = Journal(self.data)
        self.alerts = Alerter.from_env(settings.io.telegram_token_env, settings.io.telegram_chat_env)
        key_id, key_path = kalshi_credentials()
        self.rest = KalshiRest(key_id, key_path) if key_id and key_path else KalshiRest()
        sc = settings.strategy                   # history long enough for the longest sweep level checked (3 days minimum)
        self.hist_days = max(3.0, sc.sweep_days + 1, (sc.sweep_max_days + 1) if sc.sweep_max_days > 0 else 0)
        self.history = BtcHistory(max_days=self.hist_days + 1)
        if settings.strategy.series.upper().startswith("KXGOLD"):
            from kalshibot.marketdata.gold import GoldFeed      # Pyth with PYTH_API_KEY, else Hyperliquid oracle; Dukascopy history seed
            self.btc = GoldFeed(self.history, rest=self.rest, data_dir=self.data)
        else:
            self.btc = BtcFeed(rest=self.rest if self.rest.has_key else None, history=self.history)
        self.feed = (KalshiWs(self.rest, on_book=self._rec_book) if self.rest.has_key
                     else PollingFeed(self.rest, book_interval=settings.io.book_poll_s, on_book=self._rec_book))
        self._rec_last: dict[str, float] = {}
        self.views = ViewBuilder(self.feed, self.btc)
        if settings.risk.mode == "live":
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
        self.recorder = None
        if settings.io.record:
            from kalshibot.recorder import Recorder
            self.recorder = Recorder(self.data, settings.io.s3_bucket)
        self.runs: dict[str, MarketRun] = {}       # ticker -> run (current + those awaiting settlement)
        self.cid_ticker: dict[str, str] = {}
        self.current: MarketRun | None = None
        self.stopping = False
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
        return {name: f"{p:,.2f}", "strike": f"{m.strike:,.2f}", "minute": f"{sec // 60}:{sec % 60:02d}",
                "lead": f"{abs(lead) * 100:.3f}% {side}", "needed": f"{need * 100:.3f}%"
                + ("" if self.s.strategy.first_check_s <= sec <= self.s.strategy.last_entry_s else " (no entries now)")}

    def meta(self) -> dict:
        err = getattr(self.btc, "last_error", "")
        feed = {"price_feed": err} if err else {}
        return {**feed, "mode": self.s.risk.mode, "state": self.state_name(), **self.live_line(), "series": self.s.strategy.series, "market": self.current.market.ticker if self.current else None,
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
        self._sync_lots(run)

    def on_done(self, client_id: str, status: str) -> None:
        t = self.cid_ticker.get(client_id)
        run = self.runs.get(t) if t else None
        if run is not None:
            run.strat.on_order_done(client_id, status)
            self._sync_lots(run)
        self.journal.update_order(client_id, status=status)

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
        st = Strategy(self.s.strategy, self.history, id_prefix=f"kb{int(now) % 100000}")
        st.start_market(m)
        if st.skipped == "no price history" and self._feed_down():
            st.skipped = "no price feed"
        run = MarketRun(m, st)
        self.runs[m.ticker] = run
        self.current = run
        self.journal.record_market_open(m, getattr(st, "range2h", None), skipped=getattr(st, "skipped", None) or None)
        await self.feed.set_tickers([m.ticker])
        self.views.reset(m.ticker)
        log.info("market %s strike %.2f", m.ticker, m.strike)

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
        if self.recorder and view.book is not None:
            for tr in view.trades:
                self.recorder.write("trade", {"t": m.ticker, "ts": tr.ts, "side": tr.taker_side, "p": tr.price, "q": tr.qty})
        self.broker.on_second(view, m.ticker)
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
                self.runs.pop(t, None)
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

    async def _housekeeping(self) -> None:
        while not self.stopping:
            write_heartbeat(self.data)
            today = dt.datetime.now(dt.timezone.utc).date()
            if today != self.last_day:
                self.alerts.daily_summary(self.journal.daily_summary(self.last_day))
                self.last_day = today
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
        s = int(now - run.market.open_ts) if run else None
        self.recorder.write("book", {"t": tk, "s": s, "y": b.yes_bids[:10], "n": b.no_bids[:10]})

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
        log.warning("signal %s: cancelling all orders and exiting (positions stay open)", getattr(sig, "name", sig))
        self.journal.event("warn", "signal", f"{getattr(sig, 'name', sig)}: shutting down, orders cancelled, positions left open")
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
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):          # Ctrl+C / docker stop: cancel all orders, then exit
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except (NotImplementedError, RuntimeError):
                pass
        n = await self.btc.aseed_history(self.history, days=self.hist_days)
        log.info("seeded %s minute bars of BTC history", n)
        self.server.start()
        self.alerts.started(mode)
        tasks = [asyncio.create_task(self.btc.run()), asyncio.create_task(self.feed.run()),
                 asyncio.create_task(self._settle_loop()), asyncio.create_task(self._housekeeping())]
        try:
            nxt = int(time.time()) + 1
            while not self.stopping:
                await asyncio.sleep(max(0.0, nxt - time.time() + 0.05))
                now = time.time()
                nxt = int(now) + 1
                try:
                    await self._second(now)
                except Exception as e:
                    self.journal.event("error", "tick", traceback.format_exc()[-800:])
                    self.alerts.error("tick", e)
        finally:
            self.stopping = True
            try:
                await self.broker.cancel_all(None)
            except Exception:
                pass
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
