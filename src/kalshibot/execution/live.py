"""LiveBroker: real orders through an injected KalshiRest (kalshibot.marketdata.kalshi_rest). Refuses to exist unless
live mode is armed (risk.mode == "live" and risk.live_confirm == config.LIVE_CONFIRM_PHRASE).

Orders go out as `rest.create_order(ticker, side, action, qty, price, time_in_force=..., post_only=...,
reduce_only=..., client_order_id=..., exchange_index=...)`; KalshiRest's yes_book_order/build_order_body does the
YES-book mapping (buy NO at q == YES ask at 1-q) — the single mapping helper. Per intent kind:
    entry    (limit_buy)  : good_till_canceled, post_only=True,  reduce_only=False, price = bid
    TP       (limit_sell) : good_till_canceled, post_only=False, reduce_only=False (Kalshi rejects reduce_only on
                            non-IOC orders), so reduce-only is enforced LOCALLY: qty is clipped to the tracked position
                            minus other working sells of that side; nothing left -> rejected "reduce_only_local".
    fallback (market_buy) : immediate_or_cancel, price = cap (default 99c)
    stop     (market_sell): immediate_or_cancel, price = floor (default 1c), reduce_only=True
client_order_id = the strategy's OrderIntent.client_id (idempotent: a client_id already known is never re-sent).

Fills come from `ingest_fill(raw)` (WS `fill` messages or REST `/portfolio/fills` rows; dedup by trade/fill id) and
`await poll_fills()`. IOC orders end (on_done "cancelled" for any unfilled remainder) once their executed count — from
the create-order reply, or the next poll when the reply has no counts — has been ingested.

Client interface used (all synchronous; LiveBroker runs them in a worker thread unless inline=True):
    create_order(ticker, side, action, qty, price, **kw) -> dict   (the order, or {"order": {...}})
    cancel_order(order_id) ; batch_cancel(order_ids) ; orders(status) ; positions() ; fills(min_ts)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Protocol

from kalshibot.config import RiskCfg, Settings
from kalshibot.risk import LiveModeRefused, live_mode_ok
from kalshibot.types import MarketInfo, OrderIntent, Side

from .base import BrokerBase, DoneCb, FillCb, OrderState, order_fee, ticker_of

try:
    from kalshibot.marketdata.kalshi_rest import yes_book_order as _md_yes_book_order
except Exception:  # pragma: no cover - marketdata not importable
    _md_yes_book_order = None

log = logging.getLogger("kalshibot.live")

DEFAULT_FLOOR = 0.01
DEFAULT_CAP = 0.99


class RestLike(Protocol):
    def create_order(self, ticker: str, side: Side, action: str, qty: float, price: float, **kw: Any) -> dict: ...
    def cancel_order(self, order_id: str) -> Any: ...
    def batch_cancel(self, ids: list[str]) -> Any: ...
    def orders(self, status: str | None = "resting") -> Any: ...
    def positions(self) -> Any: ...
    def fills(self, min_ts: float | None = None) -> Any: ...


# --------------------------------------------------------------------------------------------- YES-book mapping
def to_yes_book(side: Side, action: str, price: float) -> tuple[str, float]:
    """(our side, buy/sell, our price) -> (YES-book side "bid"/"ask", YES-book price). Delegates to KalshiRest's
    helper when available so there is one mapping."""
    if _md_yes_book_order is not None:
        o = _md_yes_book_order(side, action, price)
        return o.book_side, o.yes_price
    if side == "y":
        return ("bid" if action == "buy" else "ask"), round(price, 4)
    return ("ask" if action == "buy" else "bid"), round(1.0 - price, 4)


def classify_yes_book(book_side: str, yes_price: float, held: Side | None) -> tuple[Side, str, float]:
    """Interpret a resting YES-book order found at reconcile as (our side, buy/sell, our price). Our only resting
    sells are TPs on the held side, so: if it is a sell when read as the held side, it is that TP; otherwise it is an
    entry bid (bid -> buy YES, ask -> buy NO)."""
    if held is not None:
        sell = (book_side == "ask") if held == "y" else (book_side == "bid")
        if sell:
            return held, "sell", round(yes_price if held == "y" else 1.0 - yes_price, 4)
    if book_side == "bid":
        return "y", "buy", round(yes_price, 4)
    return "n", "buy", round(1.0 - yes_price, 4)


def order_args(st: OrderState, exchange_index: int = 2) -> dict:
    """Keyword arguments for rest.create_order for this order (validated)."""
    if st.kind == "market_buy":
        px = st.price if st.price is not None else DEFAULT_CAP
    elif st.kind == "market_sell":
        px = st.price if st.price is not None else DEFAULT_FLOOR
    else:
        px = st.price
    if px is None or not 0.0 < px < 1.0:
        raise ValueError(f"bad price {px!r} for {st.kind}")
    if st.qty <= 0:
        raise ValueError("qty must be > 0")
    ioc = st.tif == "ioc"
    return dict(ticker=st.ticker, side=st.side, action=st.action, qty=round(st.qty, 2), price=round(px, 4),
                time_in_force="immediate_or_cancel" if ioc else "good_till_canceled",
                post_only=bool(st.post_only) and not ioc, reduce_only=bool(st.reduce_only) and ioc,
                client_order_id=st.client_id, exchange_index=exchange_index)


# -------------------------------------------------------------------------------------------- raw field parsing
def _rows(x: Any, *keys: str) -> list[dict]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    for k in keys:
        if isinstance(x.get(k), list):
            return x[k]
    return []


def _num(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _dollars(d: dict, dollar_keys: tuple[str, ...], cent_keys: tuple[str, ...]) -> float | None:
    v = _num(d, *dollar_keys)
    if v is not None:
        return v
    v = _num(d, *cent_keys)                       # *_price / market_exposure / fees_paid: always cents
    return None if v is None else v / 100.0


def _ts(d: dict) -> float | None:
    for k in ("ts", "created_ts"):
        v = _num(d, k)
        if v is not None:
            return v / 1000.0 if v > 1e11 else v
    for k in ("created_time", "time"):
        v = d.get(k)
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None


def yes_price_of(d: dict) -> float | None:
    """YES-book price of a raw order/fill row (dollars)."""
    y = _dollars(d, ("yes_price_dollars", "yes_price_fixed"), ("yes_price",))
    if y is not None:
        return y
    n = _dollars(d, ("no_price_dollars", "no_price_fixed"), ("no_price",))
    if n is not None:
        return round(1.0 - n, 6)
    return _dollars(d, ("price_dollars", "price"), ())       # V2 events API: YES-book price in dollars


# ------------------------------------------------------------------------------------------------------- broker
class LiveBroker(BrokerBase):
    def __init__(self, rest: RestLike, settings: Settings | RiskCfg, on_fill: FillCb | None = None,
                 on_done: DoneCb | None = None, *, exchange_index: int = 2, expiry_grace_s: float = 2.0,
                 inline: bool = False, done_on_fill: bool = False, id_prefix: str = "kbl", clock=None):
        risk = settings.risk if isinstance(settings, Settings) else settings
        ok, why = live_mode_ok(risk)
        if not ok:
            raise LiveModeRefused(why)
        super().__init__(on_fill, on_done, done_on_fill=done_on_fill, id_prefix=id_prefix)
        self.rest = rest
        self.exchange_index = exchange_index
        self.expiry_grace_s = expiry_grace_s
        self.inline = inline
        import time as _time
        self.clock = clock or _time.time
        self.by_exchange_id: dict[str, str] = {}
        self._seen_fills: set[str] = set()
        self._pending_raw: dict[str, list[dict]] = {}    # fills that arrived before their order_id was known
        self.unknown_fills: list[dict] = []
        self.last_fill_ts: float | None = None
        self.errors: list[str] = []
        self.on_event: Callable[[str, dict], Any] | None = None   # order timing log (send / ack / err / cancel / fill)

    def _ev(self, kind: str, **d: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, d)
            except Exception:                               # noqa: BLE001 - logging must never block an order
                log.exception("order event log")

    async def _call(self, fn, *a):
        if self.inline:
            return fn(*a)
        return await asyncio.to_thread(fn, *a)

    # ---------------------------------------------------------------------------------------------- submit
    async def submit(self, intent: OrderIntent, market: MarketInfo | str) -> None:
        ticker = ticker_of(market) or ""
        if isinstance(market, MarketInfo):
            xi = market.exchange_index
        else:
            xi = self.exchange_index
        if intent.kind == "cancel":
            for st in self._targets_for_cancel(intent):
                await self.cancel(st.client_id)
            return
        if intent.client_id and intent.client_id in self.orders:
            return                                        # idempotent
        st = self._make_state(intent, ticker, self.clock())
        self.orders[st.client_id] = st
        try:
            if st.kind == "limit_sell":
                self._clip_reduce_only(st)
            args = order_args(st, xi)
        except ValueError as e:
            st.meta["reject_reason"] = str(e)
            self._finish(st, "rejected")
            return
        st.meta["args"] = args
        t0 = self.clock()
        self._ev("send", cid=st.client_id, t=t0, args=args)
        try:
            kw = dict(args)
            reply = await self._call(lambda: self.rest.create_order(
                kw.pop("ticker"), kw.pop("side"), kw.pop("action"), kw.pop("qty"), kw.pop("price"), **kw))
        except Exception as e:                            # noqa: BLE001 - any transport/API error
            t1 = self.clock()
            self._ev("err", cid=st.client_id, t=t1, ms=round((t1 - t0) * 1000, 1), msg=str(e)[:300])
            await self._after_create_error(st, e)         # may find the order on Kalshi (timeout) and attach its id
            await self._late_cancel(st)
            return
        t1 = self.clock()
        self._ev("ack", cid=st.client_id, t=t1, ms=round((t1 - t0) * 1000, 1), reply=reply)
        self._on_create_reply(st, reply)
        await self._late_cancel(st)

    async def _late_cancel(self, st: OrderState, tries: int = 3) -> None:
        """An order cancelled while its create was in flight: cancel it on Kalshi once it has an id (normal reply or
        timeout recovery). Retries; if Kalshi keeps refusing, logs an error (the order may still rest there)."""
        if not (st.meta.get("cancel_after_create") and st.exchange_id) or st.meta.get("late_cancel_done"):
            return
        for k in range(tries):
            t0 = self.clock()
            try:
                await self._call(self.rest.cancel_order, st.exchange_id)
                st.meta["late_cancel_done"] = True
                self._ev("cancel", cid=st.client_id, t=t0, ms=round((self.clock() - t0) * 1000, 1), err=None, late=True)
                return
            except Exception as e:                        # noqa: BLE001 - e.g. a network error: try again
                self.errors.append(f"cancel {st.client_id}: {e}")
                self._ev("cancel", cid=st.client_id, t=t0, ms=round((self.clock() - t0) * 1000, 1), err=str(e)[:300],
                         late=True)
                if k + 1 < tries and not self.inline:
                    await asyncio.sleep(0.5 * 2 ** k)
        log.error("order %s (%s) was cancelled while being placed but Kalshi refused the cancel %d times; it may still "
                  "rest on the exchange", st.client_id, st.exchange_id, tries)

    def _clip_reduce_only(self, st: OrderState) -> None:
        """Local reduce-only for resting sells (Kalshi only honours reduce_only on IOC orders)."""
        working = sum(o.remaining for o in self.open_orders(st.ticker)
                      if o is not st and o.side == st.side and o.action == "sell")
        avail = self.position(st.ticker, st.side) - working
        if avail <= 0.005:
            raise ValueError("reduce_only_local: no unreserved position to sell")
        if st.qty > avail:
            st.meta["clipped_from"] = st.qty
            st.qty = avail

    def _on_create_reply(self, st: OrderState, reply: Any) -> None:
        o = (reply or {}).get("order", reply) if isinstance(reply, dict) else {}
        oid = o.get("order_id") or o.get("id")
        if oid:
            st.exchange_id = str(oid)
            self.by_exchange_id[st.exchange_id] = st.client_id
        status = str(o.get("status") or "").lower()
        if st.tif == "ioc":
            fc = _num(o, "fill_count_fp", "fill_count")
            if fc is None:
                rc = _num(o, "remaining_count_fp", "remaining_count")
                if rc is not None and status in ("canceled", "cancelled", "executed"):
                    fc = st.qty - rc
            if fc is not None:
                st.meta["ioc_fill_count"] = round(fc, 2)
            st.meta["ioc_sent"] = True
            if st.status == "pending":
                st.status = "resting"                     # transient: settles via _settle_ioc
        else:
            if status in ("canceled", "cancelled"):
                self._finish(st, "cancelled")
            elif st.status == "pending":
                st.status = "resting"
        for raw in self._pending_raw.pop(st.exchange_id or "", []):
            self.ingest_fill(raw)
        self._settle_ioc(st)

    async def _after_create_error(self, st: OrderState, e: Exception) -> None:
        msg = str(e)
        self.errors.append(f"create {st.client_id}: {msg}")
        log.warning("create_order %s failed: %s", st.client_id, msg)
        # the request may still have reached the exchange (timeout, duplicate client id): look before giving up
        found = None
        try:
            for o in _rows(await self._call(self.rest.orders, "resting"), "orders"):
                if o.get("client_order_id") == st.client_id:
                    found = o
                    break
        except Exception as e2:                           # noqa: BLE001
            self.errors.append(f"lookup {st.client_id}: {e2}")
        if found is not None:
            self._on_create_reply(st, {"order": found})
            return
        st.meta["reject_reason"] = msg
        self._finish(st, "rejected")

    # ---------------------------------------------------------------------------------------------- cancel
    async def cancel(self, client_id: str) -> bool:
        st = self.orders.get(client_id)
        if st is None or not st.active:
            return False
        if st.tif == "ioc":                               # immediate orders end by themselves (filled or not): marking
            return False                                  # one "cancelled" here would lose a fill still on its way
        if not st.exchange_id and st.status == "pending" and "args" in st.meta:
            st.meta["cancel_after_create"] = True         # create still in flight: submit() cancels it on the exchange
        if st.exchange_id:
            t0 = self.clock()
            err = None
            try:
                await self._call(self.rest.cancel_order, st.exchange_id)
            except Exception as e:                        # noqa: BLE001 - e.g. already filled / gone
                self.errors.append(f"cancel {client_id}: {e}")
                err = str(e)[:300]
            self._ev("cancel", cid=client_id, t=t0, ms=round((self.clock() - t0) * 1000, 1), err=err)
        return self._finish(st, "cancelled")

    async def cancel_all(self, ticker: str | None = None) -> int:
        """Cancel every active order we track for `ticker` (all when None) AND any other resting order the exchange
        reports for it (left over from an earlier run). Used at market close, shutdown and on disconnect."""
        mine = self.open_orders(ticker)
        ids = {o.exchange_id for o in mine if o.exchange_id}
        try:
            for o in _rows(await self._call(self.rest.orders, "resting"), "orders"):
                if ticker is None or o.get("ticker") == ticker:
                    oid = o.get("order_id") or o.get("id")
                    if oid:
                        ids.add(str(oid))
        except Exception as e:                            # noqa: BLE001
            self.errors.append(f"cancel_all list: {e}")
        if ids:
            try:
                await self._call(self.rest.batch_cancel, sorted(ids))
            except Exception as e:                        # noqa: BLE001 - fall back to one by one
                self.errors.append(f"batch_cancel: {e}")
                for oid in sorted(ids):
                    try:
                        await self._call(self.rest.cancel_order, oid)
                    except Exception as e2:               # noqa: BLE001
                        self.errors.append(f"cancel {oid}: {e2}")
        return sum(self._finish(o, "cancelled") for o in mine)

    async def cancel_entries(self) -> int:
        """Shutdown: cancel our resting buys AND any resting buy the exchange still lists (an earlier run's);
        resting sells (take-profits) are left working."""
        n = await super().cancel_entries()
        try:
            held: dict[str, Side] = {}                    # ticker -> side held (a resting order is read against it)
            for p in _rows(await self._call(self.rest.positions), "market_positions", "positions"):
                pos = _num(p, "position_fp", "position")
                if pos:
                    held[p.get("ticker") or p.get("market_ticker") or ""] = "y" if pos > 0 else "n"
            for o in _rows(await self._call(self.rest.orders, "resting"), "orders"):
                tk = o.get("ticker") or o.get("market_ticker") or ""
                st = self._adopt_order(o, tk, held.get(tk))
                if st is not None and st.action == "buy" and await self.cancel(st.client_id):
                    n += 1
        except Exception as e:                            # noqa: BLE001
            self.errors.append(f"cancel_entries list: {e}")
        return n

    async def on_disconnect(self) -> int:
        return await self.cancel_all()

    async def shutdown(self) -> int:
        return await self.cancel_all()

    async def expire_stale(self, now: float | None = None) -> list[str]:
        """Backstop: cancel entry bids older than expires_s + grace (the strategy normally cancels them itself)."""
        now = self.clock() if now is None else now
        out = []
        for st in self.open_orders():
            if st.kind == "limit_buy" and st.expires_ts is not None and now >= st.expires_ts + self.expiry_grace_s:
                if st.exchange_id:
                    try:
                        await self._call(self.rest.cancel_order, st.exchange_id)
                    except Exception as e:                # noqa: BLE001
                        self.errors.append(f"expire {st.client_id}: {e}")
                if self._finish(st, "expired"):
                    out.append(st.client_id)
        return out

    def on_second(self, view, ticker: str | None = None) -> None:   # live fills come from the exchange
        return None

    # ---------------------------------------------------------------------------------------------- fills
    def ingest_fill(self, raw: dict) -> bool:
        """Apply one fill row (REST /portfolio/fills or WS `fill` msg, with or without the {"msg": ...} envelope).
        Returns True when it was applied to one of our orders."""
        if isinstance(raw.get("msg"), dict):
            raw = raw["msg"]
        fid = raw.get("trade_id") or raw.get("fill_id") or raw.get("id")
        oid = str(raw.get("order_id") or "")
        qty = _num(raw, "count_fp", "count", "quantity")
        key = str(fid) if fid else f"{oid}|{_ts(raw)}|{qty}|{yes_price_of(raw)}"
        if key in self._seen_fills:
            return False
        self._ev("fill", t=self.clock(), raw=raw)
        cid = raw.get("client_order_id")
        st = self.orders.get(cid) if cid else None
        if st is None and oid:
            st = self.orders.get(self.by_exchange_id.get(oid, ""))
        if st is None:
            if oid and any(o.status == "pending" or (o.exchange_id is None and o.active) for o in self.orders.values()):
                self._pending_raw.setdefault(oid, []).append(raw)   # reply not back yet: replay later
            else:
                self.unknown_fills.append(raw)
            return False
        if qty is None or qty <= 0:
            return False
        self._seen_fills.add(key)
        yp = yes_price_of(raw)
        if yp is None:
            px = st.price or 0.0
        else:
            # rows carrying side=yes/no + action report that side's price only via yes/no_price; our price is the
            # YES price for YES orders and 1 - YES for NO orders either way.
            px = yp if st.side == "y" else round(1.0 - yp, 6)
        is_taker = raw.get("is_taker")
        maker = (not bool(is_taker)) if is_taker is not None else st.kind in ("limit_buy", "limit_sell")
        fee = _dollars(raw, ("fee_cost", "fee_dollars", "fee"), ())
        if fee is None:
            fee = order_fee([(px, qty)], maker=maker)
        ts = _ts(raw) or self.clock()
        if not st.active:
            log.warning("fill for terminal order %s (%s): applying anyway", st.client_id, st.status)
        self._apply_fill(st, px, round(qty, 2), fee, ts, maker, force=True)   # fractions are real
        self.last_fill_ts = max(self.last_fill_ts or ts, ts)
        self._settle_ioc(st)
        return True

    async def poll_fills(self, overlap_s: float = 5.0) -> int:
        min_ts = None if self.last_fill_ts is None else self.last_fill_ts - overlap_s
        rows = _rows(await self._call(self.rest.fills, min_ts), "fills")
        n = sum(self.ingest_fill(r) for r in sorted(rows, key=lambda r: _ts(r) or 0.0))
        for st in list(self.orders.values()):           # IOCs whose reply had no counts: settle after a poll
            if st.active and st.tif == "ioc" and st.meta.get("ioc_sent") and "ioc_fill_count" not in st.meta:
                st.meta["ioc_fill_count"] = st.filled
                self._settle_ioc(st)
        return n

    async def settle_inflight(self, timeout_s: float = 5.0, every_s: float = 0.5) -> int:
        """Shutdown: wait (polling fills) until every immediate order sent has its result, so a market buy or stop
        that filled just before the exit is recorded. Returns the number still unsettled after timeout_s."""
        import time as _time
        t0 = _time.time()
        while True:
            left = [o for o in self.open_orders() if o.tif == "ioc"]
            if not left or _time.time() - t0 >= timeout_s:
                return len(left)
            try:
                await self.poll_fills()
            except Exception as e:                        # noqa: BLE001
                self.errors.append(f"settle_inflight: {e}")
            if any(o.active for o in left):
                await asyncio.sleep(every_s)

    def _settle_ioc(self, st: OrderState) -> None:
        if st.tif != "ioc" or not st.active or "ioc_fill_count" not in st.meta:
            return
        if st.filled >= st.meta["ioc_fill_count"]:
            self._finish(st, "cancelled")               # no-op (-> "filled") when fully filled

    # ---------------------------------------------------------------------------------------------- reconcile
    async def reconcile(self, market: MarketInfo | str) -> list[dict]:
        """Read positions + resting orders for this market. Resting orders are adopted into self.orders (so they can
        be cancelled / filled); returns one lot-like dict per held side for the strategy to load:
        {lot_id, ticker, side, qty, buy, fee_in, stop(None: strategy recomputes), entry_ts, how="reconciled",
         tp_client_id}."""
        ticker = ticker_of(market) or ""
        held: dict[str, dict] = {}
        for p in _rows(await self._call(self.rest.positions), "market_positions", "positions"):
            if (p.get("ticker") or p.get("market_ticker")) != ticker:
                continue
            pos = _num(p, "position_fp", "position")
            if not pos:
                continue
            side: Side = "y" if pos > 0 else "n"
            qty = round(abs(pos), 2)
            exposure = _dollars(p, ("market_exposure_dollars",), ("market_exposure",))
            buy = round(exposure / qty, 4) if exposure and qty else None
            fee_in = _dollars(p, ("fees_paid_dollars",), ("fees_paid",)) or 0.0
            self.positions[(ticker, side)] = qty
            held[side] = dict(lot_id=f"recon-{ticker}-{side}", ticker=ticker, side=side, qty=qty, buy=buy,
                              fee_in=round(fee_in, 4), stop=None, entry_ts=None, how="reconciled", tp_client_id=None)
        held_side = next(iter(held), None)
        for o in _rows(await self._call(self.rest.orders, "resting"), "orders"):
            if (o.get("ticker") or o.get("market_ticker")) == ticker:
                self._adopt_order(o, ticker, held_side)
        for side, lot in held.items():
            lot["tp_client_id"] = next((o.client_id for o in self.open_orders(ticker)
                                        if o.kind == "limit_sell" and o.side == side), None)
        return list(held.values())

    def _adopt_order(self, o: dict, ticker: str, held: Side | None = None) -> OrderState | None:
        oid = str(o.get("order_id") or o.get("id") or "")
        cid = o.get("client_order_id") or f"ext-{oid}"
        if cid in self.orders:
            return self.orders[cid]
        raw_side = str(o.get("side") or "").lower()
        yp = yes_price_of(o)
        if raw_side in ("bid", "ask") and yp is not None:
            side, action, px = classify_yes_book(raw_side, yp, held)
        elif raw_side in ("yes", "no") and yp is not None:
            # Kalshi lists orders on the YES book ("sell NO at 99.7c" comes back as "buy YES at 0.3c"), so map to
            # bid/ask and read it against the held side like any YES-book order
            act = str(o.get("action") or "buy").lower()
            book_side = "bid" if (raw_side == "yes") == (act == "buy") else "ask"
            side, action, px = classify_yes_book(book_side, yp, held)
        else:
            return None
        rem = _num(o, "remaining_count_fp", "remaining_count")
        qty = round(rem if rem is not None else (_num(o, "count_fp", "count") or 0), 2)
        st = OrderState(client_id=cid, ticker=ticker, kind="limit_buy" if action == "buy" else "limit_sell",
                        side=side, qty=qty, price=px, status="resting", exchange_id=oid or None, tif="gtc",
                        post_only=action == "buy", reduce_only=False, reason="reconciled")
        self.orders[cid] = st
        if oid:
            self.by_exchange_id[oid] = cid
        return st
