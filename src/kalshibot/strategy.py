"""The KXBTC15M working model (2026-10-19) as a pure, deterministic state machine.

Reference implementation: ``scripts/her_trades.py`` (run with defaults: TP=0.997, LIM=0.005, HYB=1, NEAR=off,
STEP=10, MAXR=0.031, MAXSPREAD=0.03, mixed sizing, filters on). ``tests/kalshibot/test_parity.py`` replays the
66-day research set through this class and the backtest broker and checks it market by market.

Calling protocol (one market at a time)::

    st.start_market(m)                       # 2h-range filter (may mark the market skipped)
    for every second s = 0..899:
        broker delivers fills of RESTING orders for second s  -> st.on_fill / st.on_order_done
        intents = st.step(view_s)            # history must already contain the BTC price of second s
        broker.submit(each intent, in order) # market orders may fill (-> on_fill) right away
    lots = st.end_market(yes_won)

How intents map to her_trades.py (per second u):
  A  lastp/TP     The strategy keeps one resting ``limit_sell`` at ``take_profit`` per open lot (emitted at the end of
                  the step in which the lot appears); the broker fills it when our side's taker VWAP >= TP.
  B  entry bid    A resting ``limit_buy`` at ``ask - limit_offset``; at ``s >= placed + check_every_s`` (unfilled)
                  the strategy emits ``cancel`` and, if ``limit_fallback``, a ``market_buy`` when ask < max_price and
                  the side rule allows (no threshold / filter / size re-check, same size: her behaviour).
  C  stops        ``cancel`` (the lot's TP) + ``market_sell`` for every open lot with mark <= lot.stop AND BTC lead
                  < stop_lead, provided ``view.bid[side]`` exists (her ``sell_fast`` returning a price).
  D  entries      see ``_entries``.

Market orders are booked *provisionally* inside ``step`` at the view's prices (buy at ``view.ask[side]``, sell at
``view.bid[side]``), exactly as her_trades.py books them at decision time, so the rest of the same step (stops of a
just-bought lot, the held count, the roll rule) sees them. ``on_fill`` later reconciles price/fee/time with the real
fill; ``on_order_done`` undoes the unfilled part (lot removed / stop re-opened and its TP re-placed). In the backtest
``view.ask`` = her ``buy_px(u+1)`` and ``view.bid`` = her ``sell_fast(u+1)``, so the provisional values are exact.

Quirks of her_trades.py kept on purpose (parity):
  * ``lastp`` (the stop mark) is only updated while a side is set, from seconds that have prints, using the side as
    it was BEFORE this second's fills. After a roll bid is placed it is reset to None, but until the roll entry
    fills it keeps updating with the OLD side's price, so a freshly rolled lot can be compared with the old side's
    (low) mark in its first second(s) -> it may stop out immediately if BTC is also within stop_lead.
  * The sweep filters run at EVERY entry check until the first entry fills (``side is None``), even while an entry
    bid is resting; a hit skips the rest of the market and drops the bid.
  * The spread check happens before the "bid already resting" check and only when ``view.book`` is not None.
  * The threshold is ``mny_start + (mny_floor - mny_start) * (s - first) / (end - first)`` evaluated in that order.
  * Fees use the research rounding (fees.py, ``ceil(x*100 - 1e-9)/100``).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from kalshibot.config import StrategyCfg
from kalshibot.fees import maker_fee, taker_fee
from kalshibot.history import BtcHistory
from kalshibot.types import Fill, Lot, MarketInfo, OrderIntent, SecondView, Side

_QTY_EPS = 0.005                         # quantities are in contract hundredths
_EPS = 1e-9


def tick_floor(p: float) -> float:
    """Round a bid down to a price Kalshi accepts. KXBTC15M / KXGOLD15M are "tapered_deci_cent": 0.1c steps below
    10c and above 90c, whole cents in between (a 84.5c order is refused as invalid_price). Same for YES and NO."""
    import math
    step = 0.01 if 0.10 < p < 0.90 else 0.001
    return round(math.floor(p / step + 1e-6) * step, 4)


@dataclass
class _Order:
    client_id: str
    kind: str
    side: str
    qty: int
    price: float | None
    lot_id: str | None
    reason: str
    placed_s: int
    expires_at: int | None = None
    filled: int = 0
    cost: float = 0.0
    fee: float = 0.0
    first_ts: float | None = None
    active: bool = True
    prev_side: str | None = None          # market_buy: the strategy side before this entry (undo if unfilled)


class Strategy:
    def __init__(self, cfg: StrategyCfg, history: BtcHistory, id_prefix: str = "kb"):
        self.cfg = cfg
        self.history = history
        self.id_prefix = id_prefix
        self._seq = 0
        self.market: MarketInfo | None = None
        self.lots: list[Lot] = []
        self._reset()

    # ------------------------------------------------------------------ lifecycle
    def _reset(self) -> None:
        self.side: Side | None = None          # her `side`: side of the latest entry (persists after stop-outs)
        self.lastp: float | None = None        # her `lastp`: stop mark
        self._new_side: Side | None = None     # side change from a resting fill, applied after this second's mark update
        self.n_entries = 0
        self.skipped: str | None = None
        self.range2h: float | None = None
        self.pending: str | None = None        # client_id of the resting entry bid
        self.orders: dict[str, _Order] = {}
        self.tp_of: dict[str, str] = {}        # lot_id -> client_id of its active TP order
        self._queued: list[OrderIntent] = []
        self._split = 0
        self._lot_seq = 0
        self.no_entries = False                # disarmed: stops / TPs keep working, no new entries
        self.jump_blocks = 0                   # entry checks skipped by the sharp-move pause (for the dashboard)
        self.stale_blocks = 0                  # entry checks skipped because the price feed was missing / stale
        self.best = None                       # (lead/needed, lead, needed, s) at the closest entry check (dashboard)
        self.price_blocks = 0                  # checks where the lead was enough but the ask was >= max_price
        self.spread_blocks = 0                 # ... or the spread was too wide
        self.risk_blocks = 0                   # checks where the risk number was above risk_max
        self.fast_stop_n = 0                   # stops triggered between seconds (fast_stops)
        self.checks: list[dict] = []           # every entry check of this market (dashboard: checks by minute)
        self._why = ""                         # why the current entry check did not buy (or what it did)
        self._rz: tuple | None = None          # (ts, risk, z, sd $/min, minutes left) of the last risk() call
        self.stop_wait: dict[str, tuple[int, int]] = {}   # lot_id -> (market second it may retry, failed tries): a stop
        #   sale that found no buyers waits 1, 2, then 4 s before the next try (no order storm on an empty book)

    def start_market(self, m: MarketInfo) -> None:
        self._reset()
        self.market = m
        self.lots = []
        self.sigma = self._sigma(m.open_ts) if self.cfg.stop_fair else None
        r = self.history.range_2h(m.open_ts)
        self.range2h = r
        if r is None or r != r:
            self.skipped = "no price history"
        elif r > self.cfg.max_range_2h:
            self.skipped = "2h range"
        if not self.skipped and self.cfg.skip_hours_pt:
            import datetime as _dt
            from zoneinfo import ZoneInfo
            h = _dt.datetime.fromtimestamp(m.open_ts, ZoneInfo("America/Los_Angeles")).hour
            for part in self.cfg.skip_hours_pt.split(","):
                a, _, b = part.strip().partition("-")
                if a and b and int(a) <= h < int(b):
                    self.skipped = f"{int(a)}-{int(b)} PT"
                    break

    def resume(self, lots: list[Lot], tps: dict[str, tuple[str, int, float]] | None = None,
               no_entries: bool = False) -> None:
        """Restart in the middle of a market: take over this market's lots (open and closed, from the journal or
        the exchange) after start_market(). tps = lot_id -> (client_id, qty, price) of take-profit sells that are
        still resting on the exchange (they are adopted, not re-placed). Open lots get their stops back at once
        (the stop mark comes from the next traded second). no_entries = manage what is held, open nothing new."""
        self.lots = sorted(lots, key=lambda L: L.entry_ts or 0)
        base = {L.lot_id.split(".")[0] for L in self.lots}
        self.n_entries = len(base)
        for b in base:
            tail = b.rsplit("-L", 1)[-1]
            if tail.isdigit():
                self._lot_seq = max(self._lot_seq, int(tail))
        if self.lots:
            self.side = self.lots[-1].side
        if self.open_lots():
            self.skipped = None                    # it traded before the restart: keep managing it
        for lot_id, (cid, qty, px) in (tps or {}).items():
            L = self._lot(lot_id)
            if L is None or not L.open:
                continue
            self.orders[cid] = _Order(cid, "limit_sell", L.side, qty, px, lot_id, "tp", 0)
            self.tp_of[lot_id] = cid
        self.no_entries = self.no_entries or no_entries

    # ------------------------------------------------------------------ fair value (stop_fair)
    def _sigma(self, open_ts: float) -> float | None:
        """Realized 1-second log-return volatility over the 30 minutes before the open (backtest her_trades.sig_at);
        from 1-minute closes / sqrt(60) when per-second prices are missing."""
        import numpy as np
        a, b = int(open_ts) - 1800, int(open_ts)
        seg = self.history._sec_slice(a, b)
        if len(seg) and np.isfinite(seg).sum() > 600:
            with np.errstate(all="ignore"):
                r = np.diff(np.log(seg))
            v = float(np.nanstd(r))
            if v == v and v > 0:
                return v
        closes = [self.history.price_at(t, max_age=120) for t in range(a, b + 1, 60)]
        closes = [c for c in closes if c]
        if len(closes) >= 10:
            r = np.diff(np.log(closes))
            return float(np.std(r)) / math.sqrt(60) or None
        return None

    def fair_yes(self, ts: float, price: float) -> float | None:
        """P(final 60-second average >= strike) for a random walk with self.sigma; inside the last minute the
        seconds already observed are locked into the average."""
        m, sg = self.market, getattr(self, "sigma", None)
        if m is None or not sg or price != price:
            return None
        u = int(ts - m.open_ts)
        left = 899 - u
        w60 = sum((k / 60) ** 2 for k in range(1, 61))
        if left >= 60:
            mu, var = price, (left - 59) + w60
        else:
            seen = [self.history.price_at(m.open_ts + s, max_age=2) for s in range(840, u + 1)]
            seen = [p for p in seen if p is not None]
            mu = (sum(seen) + price * (60 - len(seen))) / 60
            var = sum((k / 60) ** 2 for k in range(1, max(left, 0) + 1))
        sd = sg * price * math.sqrt(max(var, 1e-9))
        return 0.5 * (1 + math.erf((mu - m.strike) / (sd * math.sqrt(2))))

    # ------------------------------------------------------------------ her risk check (risk_max)
    def risk(self, ts: float, price: float) -> float | None:
        """Chance BTC finishes on the wrong side of the strike (her PDF, backtest her_trades.risk_at): SD of the $ change
        between samples risk_sample_s apart over the last risk_short_s and risk_long_s seconds (the higher one, per
        minute); Z = |price - strike| / (SD x sqrt(minutes left - risk_clock_adj_s)); risk = 1 - Phi(Z)."""
        import numpy as np
        c, m = self.cfg, self.market
        if m is None or price != price:
            return None
        t = int(ts)
        sds = []
        for w in (c.risk_short_s, c.risk_long_s):
            seg = self.history._sec_slice(t - w, t + 1)[::c.risk_sample_s]
            with np.errstate(all="ignore"):
                d = np.diff(seg)
            d = d[np.isfinite(d)]
            if len(d) < 0.8 * (w // c.risk_sample_s):   # too little per-second history (just restarted, feed gap):
                return None                              # unknown risk -> no entry (a few seconds would look calm)
            sds.append(float(np.std(d)) * math.sqrt(60 / c.risk_sample_s))
        if not sds or max(sds) <= 0:
            return None
        left = max(900 - int(ts - m.open_ts) - c.risk_clock_adj_s, 1) / 60
        z = abs(price - m.strike) / (max(sds) * math.sqrt(left))
        rk = 0.5 * math.erfc(z / math.sqrt(2))
        self._rz = (ts, rk, z, max(sds), left)
        return rk

    def end_market(self, yes_won: bool) -> list[Lot]:
        """Settle every open lot at $1/$0 (no fee). Resting orders are the broker's to cancel."""
        m = self.market
        close_ts = m.close_ts if m else None
        for L in self.lots:
            if L.open:
                win = yes_won if L.side == "y" else not yes_won
                L.exit = "settle_win" if win else "settle_loss"
                L.exit_px = 1.0 if win else 0.0
                L.exit_ts = close_ts
                L.fee_out = 0.0
        for o in self.orders.values():
            o.active = False
        self.pending = None
        self.tp_of.clear()
        self._queued.clear()
        return list(self.lots)

    # ------------------------------------------------------------------ helpers
    def _cid(self) -> str:
        self._seq += 1
        t = self.market.ticker if self.market else "x"
        return f"{self.id_prefix}-{t}-{self._seq:05d}"

    def open_lots(self) -> list[Lot]:
        return [L for L in self.lots if L.open]

    def _lot(self, lot_id: str | None) -> Lot | None:
        for L in self.lots:
            if L.lot_id == lot_id:
                return L
        return None

    def _intent(self, kind: str, side: str, qty: int, price: float | None, lot_id: str | None, reason: str,
                s: int, expires_s: int | None = None) -> OrderIntent:
        cid = self._cid()
        self.orders[cid] = _Order(cid, kind, side, qty, price, lot_id, reason, s,
                                  None if expires_s is None else s + expires_s)
        return OrderIntent(kind=kind, side=side, qty=qty, price=price, lot_id=lot_id, client_id=cid,
                           expires_s=expires_s, reason=reason)

    def _cancel(self, cid: str, reason: str) -> OrderIntent:
        o = self.orders[cid]
        o.active = False
        if self.tp_of.get(o.lot_id or "") == cid:
            del self.tp_of[o.lot_id]
        return OrderIntent(kind="cancel", side=o.side, qty=0, lot_id=o.lot_id, client_id=cid, reason=reason)

    def _new_lot_id(self) -> str:
        self._lot_seq += 1
        t = self.market.ticker if self.market else "x"
        return f"{t}-L{self._lot_seq:02d}"

    def _size(self, s: int) -> int:
        c = self.cfg
        if c.sizing == "mixed":
            return c.size_early if s < c.early_until_s else c.size_late
        return c.size_late

    def threshold(self, s: int) -> float:
        c = self.cfg
        span = c.mny_end_s - c.first_check_s
        return c.mny_start + (c.mny_floor - c.mny_start) * min(s - c.first_check_s, span) / span

    def skip_reason(self, ts: float, side: Side, price: float) -> str | None:
        """her_trades.skip_market: sweep filters for an entry on `side` at second ts (BTC = price)."""
        c, h = self.cfg, self.history
        if price != price:
            return None
        t = int(math.floor(ts))
        d = 1 if side == "y" else -1
        lb = c.sweep_lookback_s
        k2 = int(round(c.sweep_days * 86400))
        if h.swept(t, d, k2, lb):
            mx = int(round(c.sweep_max_days * 86400))
            if not (mx and h.swept(t, d, mx, lb)):            # sweep_max_days: a break of an even longer level is not skipped
                for n in (30, 14, 7, 5, 3):
                    if n * 86400 >= k2 and (not mx or n * 86400 < mx) and h.swept(t, d, n * 86400, lb):
                        return f"{n}-day sweep"
                return f"{c.sweep_days:g}-day sweep"
        if c.sweep_4h_m60 > 0 and h.swept(t, d, 14400, lb) and h.covers(3600, at=t):
            p1 = h.price_at(t - 3600, max_age=0)
            if p1 is not None and d * (price - p1) / price * 100 >= c.sweep_4h_m60 * 100:
                return "4h sweep + stretched 1h"
        return None

    def _book_market_entry(self, v: SecondView, side: Side, qty: int, ask: float, reason: str,
                           reset_lastp: bool) -> list[OrderIntent]:
        if reset_lastp and self.side and side != self.side:
            self.lastp = None
        prev = self.side
        self.side = side
        self.n_entries += 1
        lot = Lot(lot_id=self._new_lot_id(), ticker=self.market.ticker, side=side, qty=qty, buy=ask,
                  fee_in=taker_fee(ask, qty), stop=round(ask * (1 - self.cfg.stop_pct), 4), entry_ts=v.ts + 1,
                  how="market")
        self.lots.append(lot)
        it = self._intent("market_buy", side, qty, self.cfg.max_price, lot.lot_id, reason, v.s)
        self.orders[it.client_id].prev_side = prev
        return [it]

    # ------------------------------------------------------------------ main step
    def step(self, v: SecondView) -> list[OrderIntent]:
        out: list[OrderIntent] = list(self._queued)
        self._queued.clear()
        if self.market is None or self.skipped:
            return out
        c, s = self.cfg, v.s

        # A. stop mark (her lastp), with the side as it was before this second's resting fills
        if self.side is not None and v.trades:
            mk = v.mark.get(self.side)
            if mk is not None:
                self.lastp = mk
        if self._new_side is not None:
            self.side = self._new_side
            self._new_side = None

        # B. resting entry bid expired -> cancel (+ market fallback)
        if self.pending is not None:
            o = self.orders[self.pending]
            if o.expires_at is not None and s >= o.expires_at:
                out.append(self._cancel(o.client_id, "entry_expired"))
                self.pending = None
                left = o.qty - o.filled
                if c.limit_fallback and left > 0 and not self.jumped(v.ts):
                    e = v.ask.get(o.side)
                    if e is not None and e < c.max_price and (self.side is None or o.side == self.side
                                                               or not self.open_lots()) and self._still_safe(v, o.side):
                        out += self._book_market_entry(v, o.side, left, e, "entry_market_fallback", False)

        # C. stops
        out += self._stop_intents(v, self.lastp, v.ts + 1)

        # D. entries
        check = (not self.no_entries and c.first_check_s <= s <= c.last_entry_s and s % c.check_every_s == 0)
        self._why = ("disarmed: no new entries" if self.no_entries else "outside entry window") if not check else ""
        if check and (v.btc is None or v.btc.stale):
            self.stale_blocks += 1
            check, self._why = False, "price feed stale"
        if check and self.jumped(v.ts):
            self.jump_blocks += 1
            check, self._why = False, "sharp-move pause"
        if check:
            r = self._entries(v)
            if s % c.check_every_s == 0:
                self._log_check(v)
            if r is None:                       # market skipped by the sweep filters
                return out + self._skip_cancels()
            out += r
        elif s % c.check_every_s == 0:
            self._log_check(v)

        # TP: one resting sell per open lot, once its buy has filled (live: Kalshi confirms a market buy's fill a moment
        # later; a sell placed before that is refused by the broker's reduce-only check)
        unfilled = {o.lot_id for o in self.orders.values() if o.kind == "market_buy" and o.active and o.filled == 0}
        for L in self.open_lots():
            if L.lot_id not in self.tp_of and L.lot_id not in unfilled:
                it = self._intent("limit_sell", L.side, L.qty, c.take_profit, L.lot_id, "tp", s)
                self.tp_of[L.lot_id] = it.client_id
                out.append(it)
        return out

    def _stop_intents(self, v: SecondView, mark: float | None, exit_ts: float) -> list[OrderIntent]:
        """Stops for every open lot whose mark (or fair value) is <= its stop while BTC is within stop_lead of the
        strike; with stop_recheck only lots whose bid is still <= their stop. Sells each such lot at market."""
        out: list[OrderIntent] = []
        lots = self.open_lots()
        if self.side and lots and mark is not None and v.btc is not None:
            K = self.market.strike
            lead = (v.btc.price - K) / K * (1 if self.side == "y" else -1)
            fv = None
            if self.cfg.stop_fair:
                fy = self.fair_yes(v.ts, v.btc.price)
                fv = None if fy is None else (fy if self.side == "y" else 1 - fy)
            hit = [L for L in lots if (mark <= L.stop or (fv is not None and fv <= L.stop)) and lead < self.cfg.stop_lead
                   and v.s >= self.stop_wait.get(L.lot_id, (0, 0))[0]]
            if hit:
                b = v.bid.get(self.side)
                if b is not None and self.cfg.stop_recheck:           # re-check: skip lots whose bid recovered above their stop
                    hit = [L for L in hit if b <= L.stop + _EPS]     # fair-value triggers too, as in the backtest
                    #   (her_trades CONFIRM=bid): selling FV stops with the bid still above the stop cost -$833 on
                    #   53 paper lots Oct 21-22 that the backtest held to 99.7c
                if b is not None and hit:
                    for L in hit:
                        tp = self.tp_of.get(L.lot_id)
                        if tp is not None:
                            out.append(self._cancel(tp, "stop"))
                        out.append(self._intent("market_sell", L.side, L.qty, None, L.lot_id, "stop", v.s))
                        L.exit, L.exit_px, L.exit_ts, L.fee_out = "stop", b, exit_ts, taker_fee(b, L.qty)

        return out

    def fast_stops(self, v: SecondView) -> list[OrderIntent]:
        """Between whole seconds (fast_stop_ms): the same stop rule on the prints so far in the current second, so a
        stop sells within ~100 ms of the print instead of at the next second. Nothing else (entries, TPs) runs here."""
        if self.market is None or self.skipped or not self.side or not self.open_lots() or not v.trades:
            return []
        mk = v.mark.get(self.side)
        if mk is None or not any(mk <= L.stop for L in self.open_lots()):
            return []
        self.fast_stop_n += 1
        return self._stop_intents(v, mk, v.ts)

    def _skip_cancels(self) -> list[OrderIntent]:
        out = []
        if self.pending is not None:
            out.append(self._cancel(self.pending, "skip"))
            self.pending = None
        return out

    def _entries(self, v: SecondView) -> list[OrderIntent] | None:
        c, s = self.cfg, v.s
        lots = self.open_lots()
        held = sum(L.qty for L in lots)
        if held >= c.max_held:
            self._why = "max held"
            return []
        K = self.market.strike
        p = v.btc.price
        th = self.threshold(s)
        m = (p - K) / K
        if th > 0 and (self.best is None or abs(m) / th > self.best[0]):
            self.best = (abs(m) / th, abs(m), th, s)
        s2: Side | None = "y" if m >= th else ("n" if -m >= th else None)
        if not s2:
            self._why = "moneyness"
            return []
        if not (self.side is None or s2 == self.side or not lots):
            self._why = "holding the other side"
            return []
        if c.risk_max > 0:
            rk = self.risk(v.ts, p)
            if rk is None or rk > c.risk_max:
                self.risk_blocks += 1
                self._why = "risk"
                return []
        if c.filters and self.side is None:
            why = self.skip_reason(v.ts, s2, p)
            if why:
                self.skipped = why
                self._why = f"market skipped: {why}"
                return None
        if v.book is not None:
            sp = v.book.spread(s2)
            if sp is not None and sp > c.max_spread + _EPS:
                self.spread_blocks += 1
                self._why = "spread too wide"
                return []
        e = v.ask.get(s2)
        if e is None or not e < c.max_price:
            self.price_blocks += 1
            self._why = "price at/above ceiling" if e is not None else "no ask"
            return []
        sz = min(self._size(s), c.max_held - held)
        if c.limit_offset > 0:
            if self.pending is not None:
                self._why = "bid still waiting"
                return []
            if self.side and s2 != self.side:
                self.lastp = None
            it = self._intent("limit_buy", s2, sz, tick_floor(e - c.limit_offset) if c.tick_prices else round(e - c.limit_offset, 4), None, "entry", s,
                              expires_s=c.entry_wait_s or c.check_every_s)
            self.pending = it.client_id
            self._why = f"BUY: bid {it.price * 100:g}c x {sz}"
            return [it]
        self._why = f"BUY: market {e * 100:g}c x {sz}"
        return self._book_market_entry(v, s2, sz, e, "entry", True)

    def _still_safe(self, v: SecondView, side: Side) -> bool:
        """Before the market buy that follows an unfilled bid: with the risk check on, BTC must still be on that side
        of the strike with risk <= risk_max (backtest FALLRISK=1: same P&L, ~0.7% of these buys skipped)."""
        c, m = self.cfg, self.market
        if c.risk_max <= 0 or m is None:
            return True
        if v.btc is None or v.btc.stale or not math.isfinite(v.btc.price):
            return False
        if (v.btc.price - m.strike) * (1 if side == "y" else -1) < 0:
            return False
        rk = self.risk(v.ts, v.btc.price)
        return rk is not None and rk <= c.risk_max

    def _log_check(self, v: SecondView) -> None:
        """One row per entry check (dashboard): BTC, the side it leans to, its ask, distance to the strike, the distance
        needed (moneyness line and/or the distance where risk = risk_max), Z, loss chance and the decision."""
        c, m = self.cfg, self.market
        if m is None:
            return
        p = v.btc.price if v.btc is not None and math.isfinite(v.btc.price) else None
        row = {"s": v.s, "btc": None if p is None else round(p, 2), "why": self._why or "no signal"}
        if p is not None:
            mny = (p - m.strike) / m.strike
            side = "y" if mny >= 0 else "n"
            th = self.threshold(v.s) if c.mny_start > 0 else 0.0
            need = th * m.strike
            if self._rz is None or self._rz[0] != v.ts:
                self.risk(v.ts, p)
            if self._rz is not None and self._rz[0] == v.ts:
                _, rk, z, sd, left = self._rz
                row.update(z=round(z, 2), risk=round(rk, 4))
                if c.risk_max > 0:                 # distance where risk = risk_max
                    from statistics import NormalDist
                    need = max(need, NormalDist().inv_cdf(1 - c.risk_max) * sd * math.sqrt(left))
            row.update(side=side, px=v.ask.get(side), dist=round(abs(mny) * 100, 4),
                       need_usd=round(need, 2), need=round(need / m.strike * 100, 4))
        self.checks.append(row)

    # ------------------------------------------------------------------ broker callbacks
    def on_fill(self, f: Fill) -> None:
        o = self.orders.get(f.client_id)
        if o is None:
            return
        first = o.filled == 0
        o.filled = round(o.filled + f.qty, 2)
        o.cost += f.price * f.qty
        o.fee += f.fee
        if o.first_ts is None:
            o.first_ts = f.ts
        avg = f.price if first else o.cost / o.filled
        if o.kind == "limit_buy":
            L = self._lot(o.lot_id) if o.lot_id else None
            if L is None:
                self.n_entries += 1
                L = Lot(lot_id=self._new_lot_id(), ticker=self.market.ticker if self.market else "", side=o.side,
                        qty=f.qty, buy=f.price, fee_in=f.fee, stop=round(f.price * (1 - self.cfg.stop_pct), 4),
                        entry_ts=f.ts, how="limit")
                o.lot_id = L.lot_id
                self.lots.append(L)
                if self.side != o.side:
                    self._new_side = o.side
            else:                                   # partial fills of the same bid: one lot
                L.qty, L.buy, L.fee_in = o.filled, avg, o.fee
                L.stop = round(avg * (1 - self.cfg.stop_pct), 4)
                if L.lot_id in self.tp_of:          # TP size no longer matches: replace it next step
                    self._queued.append(self._cancel(self.tp_of[L.lot_id], "resize"))
            if o.filled >= o.qty - _QTY_EPS:
                o.active = False
                if self.pending == o.client_id:
                    self.pending = None
        elif o.kind == "market_buy":
            L = self._lot(o.lot_id)
            if L is not None:
                L.buy, L.fee_in, L.entry_ts = avg, o.fee, o.first_ts
                L.stop = round(avg * (1 - self.cfg.stop_pct), 4)
            if o.filled >= o.qty - _QTY_EPS:
                o.active = False
        elif o.kind == "limit_sell":
            L = self._lot(o.lot_id)
            if L is None:
                return
            if f.qty < L.qty and L.open:            # partial TP: split off the sold part
                part = self._split_lot(L, f.qty)
                part.exit, part.exit_px, part.exit_ts, part.fee_out = "tp", f.price, f.ts, f.fee
            else:
                L.exit, L.exit_px, L.exit_ts, L.fee_out = "tp", f.price, f.ts, f.fee
                o.active = False
                self.tp_of.pop(L.lot_id, None)
        elif o.kind == "market_sell":
            L = self._lot(o.lot_id)
            if L is not None and L.exit in ("stop", "kill"):
                L.exit_px, L.fee_out, L.exit_ts = avg, o.fee, o.first_ts
            if o.filled >= o.qty - _QTY_EPS:
                o.active = False

    # ------------------------------------------------------------------ switches
    def stop_entries(self) -> list[OrderIntent]:
        """Disarm switch: no new entries from now on; cancels a resting entry bid. Stops and TPs keep working."""
        self.no_entries = True
        out = [self._cancel(cid, "disarm") for cid, o in list(self.orders.items()) if o.active and o.kind == "limit_buy"]
        self.pending = None
        return out

    def flatten(self, v: SecondView) -> list[OrderIntent]:
        """Kill switch: cancel every resting order and sell every open lot at market. Call every second until
        ``open_lots()`` is empty: a sell that does not fill re-opens its lot (on_order_done) and is sent again."""
        self.no_entries = True
        out = [self._cancel(cid, "kill") for cid, o in list(self.orders.items())
               if o.active and o.kind in ("limit_buy", "limit_sell")]
        self.pending = None
        selling = {o.lot_id for o in self.orders.values() if o.active and o.kind == "market_sell"}
        for L in self.open_lots():
            if L.lot_id in selling:
                continue
            b = v.bid.get(L.side)
            out.append(self._intent("market_sell", L.side, L.qty, None, L.lot_id, "kill", v.s))
            L.exit, L.exit_px, L.exit_ts = "kill", b, v.ts + 1
            L.fee_out = taker_fee(b, L.qty) if b is not None else 0.0
        return out

    def jumped(self, ts: float) -> bool:
        """Sharp-move pause: True if any of the last 15 completed clock minutes moved more than cfg.jump_pause
        (close to close). Missing prices count as no move."""
        thr = self.cfg.jump_pause
        if thr <= 0:
            return False
        end = int(ts) // 60 * 60 - 1                 # last second of the last completed minute
        closes = [self.history.price_at(end - 60 * k, max_age=120) for k in range(16)]
        for a, b in zip(closes[:-1], closes[1:]):
            if a is not None and b is not None and b > 0 and abs(a / b - 1) > thr:
                return True
        return False

    def _split_lot(self, L: Lot, qty: int) -> Lot:
        """Split `qty` contracts off lot L into a new lot (same entry); returns the new lot."""
        self._split += 1
        qty = round(qty, 2)                    # contract hundredths (fractional fills: 1 - 0.57 = 0.43000000000000005)
        frac = qty / L.qty
        part = Lot(lot_id=f"{L.lot_id}.{self._split}", ticker=L.ticker, side=L.side, qty=qty, buy=L.buy,
                   fee_in=round(L.fee_in * frac, 6), stop=L.stop, entry_ts=L.entry_ts, how=L.how)
        L.qty = round(L.qty - qty, 2)
        L.fee_in = round(L.fee_in - part.fee_in, 6)
        self.lots.append(part)
        return part

    def on_order_done(self, client_id: str, status: str) -> None:
        o = self.orders.get(client_id)
        if o is None:
            return
        o.active = False
        if self.pending == client_id:
            self.pending = None
        if self.tp_of.get(o.lot_id or "") == client_id:
            del self.tp_of[o.lot_id]               # TP gone (not by us): re-placed next step if the lot is open
        L = self._lot(o.lot_id)
        if L is None:
            return
        if o.kind == "market_buy" and o.filled < o.qty - _QTY_EPS:
            if L.lot_id in self.tp_of:
                self._queued.append(self._cancel(self.tp_of[L.lot_id], "entry_unfilled"))
            if o.filled == 0:
                self.lots.remove(L)
                self.n_entries -= 1
                if not self.lots:
                    self.side = o.prev_side
            else:
                L.qty = o.filled
        elif o.kind == "market_sell" and o.filled < o.qty - _QTY_EPS and L.exit in ("stop", "kill"):
            stop = L.exit == "stop"
            if o.filled == 0:                      # stop not executed: lot open again (TP re-placed next step)
                L.exit = L.exit_px = L.exit_ts = None
                L.fee_out = 0.0
                rest = L
            else:
                rest = self._split_lot(L, o.qty - o.filled)
                rest.exit = None
                L.exit_px, L.fee_out, L.exit_ts = o.cost / o.filled, o.fee, o.first_ts
            if stop:
                n = self.stop_wait.get(L.lot_id, (0, 0))[1] + 1
                self.stop_wait[rest.lot_id] = (o.placed_s + min(2 ** (n - 1), 4), n)

    # ------------------------------------------------------------------ persistence
    def state(self) -> dict[str, Any]:
        return dict(
            seq=self._seq, split=self._split, lot_seq=self._lot_seq,
            market=asdict(self.market) if self.market else None,
            lots=[asdict(L) for L in self.lots],
            orders={k: asdict(o) for k, o in self.orders.items()},
            side=self.side, lastp=self.lastp, new_side=self._new_side, n_entries=self.n_entries, no_entries=self.no_entries,
            skipped=self.skipped, range2h=self.range2h, pending=self.pending, tp_of=dict(self.tp_of),
            queued=[asdict(i) for i in self._queued],
        )

    def load_state(self, d: dict[str, Any]) -> None:
        self._reset()
        self._seq = int(d.get("seq", 0))
        self._split = int(d.get("split", 0))
        self._lot_seq = int(d.get("lot_seq", 0))
        self.market = MarketInfo(**d["market"]) if d.get("market") else None
        self.lots = [Lot(**x) for x in d.get("lots", [])]
        self.orders = {k: _Order(**o) for k, o in d.get("orders", {}).items()}
        self.side, self.lastp, self._new_side = d.get("side"), d.get("lastp"), d.get("new_side")
        self.n_entries = int(d.get("n_entries", 0))
        self.no_entries = bool(d.get("no_entries", False))
        self.skipped, self.range2h, self.pending = d.get("skipped"), d.get("range2h"), d.get("pending")
        self.tp_of = dict(d.get("tp_of", {}))
        self._queued = [OrderIntent(**i) for i in d.get("queued", [])]
