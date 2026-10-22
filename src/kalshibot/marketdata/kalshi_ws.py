"""Live Kalshi order books + trades: authenticated WebSocket client (`KalshiWs`) and a key-less REST fallback
(`PollingFeed`). Both expose the same read side used by `ViewBuilder` and the paper broker:

    feed.book(ticker) -> Book | None          # Book.ts = time the book is known valid as of
    feed.trades(ticker, t0, t1) -> [KTrade]   # trades with t0 <= ts < t1, oldest first
    await feed.set_tickers([...])             # follow the current market(s)
    await feed.run() / feed.stop()

WebSocket protocol (docs.kalshi.com, checked 2026-10-19):
  * URL ``wss://api.elections.kalshi.com/trade-api/ws/v2`` (``external-api-ws.kalshi.com`` also works). The
    handshake carries the same KALSHI-ACCESS-* headers as REST, signed over ``<ts_ms>GET/trade-api/ws/v2``. The
    connection itself requires auth, and ``orderbook_delta`` / ``fill`` are private channels - so without a key use
    PollingFeed.
  * ``{"id":n,"cmd":"subscribe","params":{"channels":[...],"market_tickers":[...]}}`` -> ``subscribed`` with a
    ``sid``. ``orderbook_delta`` first sends ``orderbook_snapshot`` (``yes_dollars_fp``/``no_dollars_fp`` =
    [[price, qty], ...]) then ``orderbook_delta`` (``price_dollars``, ``delta_fp``, ``side``). ``seq`` increases by
    exactly 1 per message of a sid; a gap means we missed something.
  * We subscribe with ``use_yes_price: true`` (Kalshi will make that the only behaviour): no-side levels then come
    in YES-leg prices and are converted back to NO prices (1 - p) here, because types.Book keeps each side's bids in
    its own leg's prices (same as the REST book).
  * Gap -> mark the sid's books dirty, re-request snapshots (``update_subscription`` / ``get_snapshot``) and, in
    parallel, refetch them over REST so the book is usable again within ~1 request. The WS snapshot, when it arrives,
    replaces the REST copy.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
import time
from collections import deque
from typing import Any, Awaitable, Callable, Iterable

from ..types import Book, KTrade, Side
from .kalshi_rest import CA_FILE, KalshiError, KalshiRest, auth_headers, parse_levels, parse_trade

log = logging.getLogger("kalshibot.marketdata.ws")

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_SIGN_PATH = "/trade-api/ws/v2"


def ssl_context() -> ssl.SSLContext:
    try:
        return ssl.create_default_context(cafile=CA_FILE)
    except (FileNotFoundError, ssl.SSLError):
        return ssl.create_default_context()


async def _call(cb: Callable | None, *args: Any) -> None:
    """Invoke a sync or async callback; never let a callback error kill the feed."""
    if cb is None:
        return
    try:
        r = cb(*args)
        if inspect.isawaitable(r):
            await r
    except Exception:  # noqa: BLE001
        log.exception("callback %s failed", getattr(cb, "__name__", cb))


# ----------------------------------------------------------------------------------------------------------------
# Local state
# ----------------------------------------------------------------------------------------------------------------

class LiveBook:
    """Mutable book: {price: qty} per side, each side's bids in its OWN leg's prices (NO bids in NO prices)."""

    def __init__(self) -> None:
        self.yes: dict[float, float] = {}
        self.no: dict[float, float] = {}
        self.updated = 0.0          # local time of the last snapshot/delta
        self.dirty = True           # True until a snapshot (WS or REST) has been applied

    def load(self, yes: Iterable[tuple[float, float]], no: Iterable[tuple[float, float]], ts: float) -> None:
        self.yes = {round(p, 4): q for p, q in yes if q > 0}
        self.no = {round(p, 4): q for p, q in no if q > 0}
        self.updated, self.dirty = ts, False

    def apply(self, side: Side, price: float, delta: float, ts: float) -> None:
        lv = self.yes if side == "y" else self.no
        p = round(price, 4)
        q = lv.get(p, 0.0) + delta
        if q > 1e-9:
            lv[p] = round(q, 4)
        else:
            if q < -1e-6:
                log.warning("book level went negative (%s %.4f -> %.2f); dropping", side, p, q)
            lv.pop(p, None)
        self.updated = ts

    def snapshot(self, ts: float) -> Book:
        return Book(ts=ts, yes_bids=sorted(self.yes.items(), reverse=True),
                    no_bids=sorted(self.no.items(), reverse=True))


class TradeTape:
    """Per-ticker trade store with de-duplication by trade id (REST polling re-reads overlapping pages)."""

    def __init__(self, keep_s: float = 3600.0, maxlen: int = 200_000):
        self.keep_s = keep_s
        self.maxlen = maxlen
        self.trades: dict[str, deque[KTrade]] = {}
        self.seen: dict[str, dict[str, float]] = {}

    def add(self, ticker: str, trade_id: str | None, t: KTrade) -> bool:
        """Store a trade; False if it was a duplicate. Out-of-order trades are inserted in place."""
        key = trade_id or f"{t.ts:.3f}|{t.taker_side}|{t.price}|{t.qty}"
        seen = self.seen.setdefault(ticker, {})
        if key in seen:
            return False
        seen[key] = t.ts
        dq = self.trades.setdefault(ticker, deque(maxlen=self.maxlen))
        if not dq or dq[-1].ts <= t.ts:
            dq.append(t)
        else:
            items = list(dq)
            i = len(items)
            while i > 0 and items[i - 1].ts > t.ts:
                i -= 1
            items.insert(i, t)
            dq.clear()
            dq.extend(items)
        self._prune(ticker, t.ts)
        return True

    def _prune(self, ticker: str, now: float) -> None:
        dq = self.trades.get(ticker)
        cut = now - self.keep_s
        while dq and dq[0].ts < cut:
            dq.popleft()
        seen = self.seen.get(ticker)
        if seen and len(seen) > 2 * self.maxlen // 10 + 1000:
            for k in [k for k, ts in seen.items() if ts < cut]:
                del seen[k]

    def between(self, ticker: str, t0: float, t1: float) -> list[KTrade]:
        dq = self.trades.get(ticker)
        if not dq:
            return []
        out = []
        for t in reversed(dq):          # newest first; views ask for the latest second
            if t.ts < t0:
                break
            if t.ts < t1:
                out.append(t)
        out.reverse()
        return out

    def last_ts(self, ticker: str) -> float | None:
        dq = self.trades.get(ticker)
        return dq[-1].ts if dq else None

    def drop(self, ticker: str) -> None:
        self.trades.pop(ticker, None)
        self.seen.pop(ticker, None)


def parse_fill(msg: dict) -> dict:
    """Normalise a WS `fill` msg (or REST /portfolio/fills item) to the fields execution needs.

    book_side bid == long YES direction (buy YES / sell NO), ask == long NO direction. yes_price is the YES-leg
    price; use kalshi_rest.from_yes_book(book_side, yes_price, our_side) for (action, our price)."""
    book_side = msg.get("book_side")
    if book_side is None:   # legacy fields: (buy, yes) / (sell, no) -> bid
        s, a = msg.get("side"), msg.get("action")
        book_side = "bid" if (s == "yes") == (a == "buy") else "ask"
    if "yes_price_dollars" in msg:
        yp = float(msg["yes_price_dollars"])
    else:
        yp = 1.0 - float(msg["no_price_dollars"])
    ts = msg["ts_ms"] / 1000.0 if "ts_ms" in msg else (float(msg["ts"]) if "ts" in msg else time.time())
    return {"order_id": msg.get("order_id"), "client_order_id": msg.get("client_order_id"),
            "trade_id": msg.get("trade_id"), "ticker": msg.get("market_ticker") or msg.get("ticker"),
            "book_side": book_side, "yes_price": round(yp, 4), "qty": float(msg.get("count_fp", msg.get("count", 0))),
            "fee": float(msg.get("fee_cost", 0) or 0), "is_taker": bool(msg.get("is_taker")), "ts": ts}


# ----------------------------------------------------------------------------------------------------------------
# WebSocket client
# ----------------------------------------------------------------------------------------------------------------

class KalshiWs:
    """Authenticated Kalshi WebSocket feed for a list of market tickers.

    Callbacks (sync or async): on_book(ticker, Book), on_trade(ticker, KTrade), on_fill(dict from parse_fill,
    raw msg), on_ticker(raw msg), on_brti(ts, price), on_status(connected: bool), on_raw(raw msg) for every
    orderbook_snapshot / orderbook_delta as received (recorder: lossless book history)."""

    BOOK_CH, TRADE_CH, TICKER_CH, FILL_CH, BRTI_CH = "orderbook_delta", "trade", "ticker", "fill", "cfbenchmarks_value"

    def __init__(self, rest: KalshiRest, tickers: Iterable[str] = (), *, url: str = WS_URL,
                 channels: Iterable[str] = ("orderbook_delta", "trade", "ticker", "fill"), brti: bool = False,
                 use_yes_price: bool = True, on_book: Callable | None = None, on_trade: Callable | None = None,
                 on_fill: Callable | None = None, on_ticker: Callable | None = None,
                 on_brti: Callable | None = None, on_status: Callable | None = None,
                 connect: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time,
                 max_backoff: float = 30.0, on_raw: Callable | None = None):
        self.rest, self.url = rest, url
        self.tickers: list[str] = list(dict.fromkeys(tickers))
        self.channels = list(channels) + ([self.BRTI_CH] if brti else [])
        self.use_yes_price = use_yes_price
        self.on_book, self.on_trade, self.on_fill = on_book, on_trade, on_fill
        self.on_ticker, self.on_brti, self.on_status = on_ticker, on_brti, on_status
        self.on_raw = on_raw
        self._connect = connect
        self.clock = clock
        self.max_backoff = max_backoff
        self.books: dict[str, LiveBook] = {}
        self.tape = TradeTape()
        self.ws: Any = None
        self.connected = False
        self._next_id = 1
        self._pending: dict[int, str] = {}          # command id -> channel
        self.sids: dict[str, int] = {}               # channel -> sid
        self.seq: dict[int, int] = {}                # sid -> last seq
        self.gaps = 0
        self.resyncs = 0
        self._stop = False
        self._tasks: set[asyncio.Task] = set()

    # -- read side ---------------------------------------------------------------------------------------------
    def book(self, ticker: str) -> Book | None:
        lb = self.books.get(ticker)
        if lb is None or (lb.dirty and lb.updated == 0.0):
            return None                     # never loaded
        ts = self.clock() if (self.connected and not lb.dirty) else lb.updated
        return lb.snapshot(ts)

    def trades(self, ticker: str, t0: float, t1: float) -> list[KTrade]:
        return self.tape.between(ticker, t0, t1)

    # -- control -----------------------------------------------------------------------------------------------
    def stop(self) -> None:
        self._stop = True
        if self.ws is not None:
            self._spawn(self.ws.close())

    async def set_tickers(self, tickers: Iterable[str]) -> None:
        new = list(dict.fromkeys(tickers))
        add = [t for t in new if t not in self.tickers]
        rem = [t for t in self.tickers if t not in new]
        self.tickers = new
        for t in rem:
            self.books.pop(t, None)
            self.tape.drop(t)
        if not self.connected:
            return
        for ch in (self.BOOK_CH, self.TRADE_CH, self.TICKER_CH):
            sid = self.sids.get(ch)
            if sid is None:
                if add and ch in self.channels:
                    await self._subscribe(ch)
                continue
            if add:
                await self._send({"cmd": "update_subscription",
                                  "params": {"sids": [sid], "market_tickers": add, "action": "add_markets"}})
            if rem:
                await self._send({"cmd": "update_subscription",
                                  "params": {"sids": [sid], "market_tickers": rem, "action": "delete_markets"}})
        for t in add:
            self.books.setdefault(t, LiveBook())

    async def run(self) -> None:
        """Connect, subscribe, process messages; reconnect with exponential backoff until stop()."""
        if not self.rest.has_key:
            raise KalshiError("Kalshi WebSocket needs an API key; use PollingFeed without one")
        back = 1.0
        while not self._stop:
            try:
                await self._session()
                back = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("kalshi ws disconnected: %s: %s", type(e).__name__, str(e)[:200])
            await self._set_connected(False)
            if self._stop:
                break
            await asyncio.sleep(back)
            back = min(back * 2, self.max_backoff)

    async def _session(self) -> None:
        headers = auth_headers(self.rest.key_id, self.rest.key_path, "GET", WS_SIGN_PATH)
        if self._connect is None:
            import websockets
            conn = websockets.connect(self.url, additional_headers=headers, ssl=ssl_context(), open_timeout=10,
                                      ping_interval=20, ping_timeout=20, max_size=2 ** 23)
        else:
            conn = self._connect(self.url, additional_headers=headers)
        async with conn as ws:
            self.ws = ws
            self.sids.clear()
            self.seq.clear()
            self._pending.clear()
            for lb in self.books.values():
                lb.dirty = True
            await self._set_connected(True)
            for ch in self.channels:
                await self._subscribe(ch)
            async for raw in ws:
                await self.handle(json.loads(raw))
                if self._stop:
                    break
        self.ws = None

    async def _set_connected(self, v: bool) -> None:
        if v != self.connected:
            self.connected = v
            if not v:
                for lb in self.books.values():
                    lb.dirty = True
            await _call(self.on_status, v)

    async def _send(self, cmd: dict) -> int:
        cid = self._next_id
        self._next_id += 1
        cmd = {"id": cid, **cmd}
        if self.ws is not None:
            await self.ws.send(json.dumps(cmd))
        return cid

    async def _subscribe(self, ch: str) -> None:
        params: dict[str, Any] = {"channels": [ch]}
        if ch in (self.BOOK_CH, self.TRADE_CH, self.TICKER_CH):
            if not self.tickers:
                return
            params["market_tickers"] = list(self.tickers)
            if ch == self.BOOK_CH:
                for t in self.tickers:
                    self.books.setdefault(t, LiveBook())
        if ch == self.BOOK_CH and self.use_yes_price:
            params["use_yes_price"] = True
        if ch == self.BRTI_CH:
            params["index_ids"] = ["BRTI"]
        cid = await self._send({"cmd": "subscribe", "params": params})
        self._pending[cid] = ch

    def _spawn(self, coro: Awaitable) -> None:
        try:
            t = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        except RuntimeError:
            return
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # -- message handling --------------------------------------------------------------------------------------
    async def handle(self, d: dict) -> None:
        typ = d.get("type")
        msg = d.get("msg") or {}
        now = self.clock()
        if typ == "subscribed":
            ch = msg.get("channel") or self._pending.get(d.get("id", -1))
            if ch:
                self.sids[ch] = msg.get("sid")
            self._pending.pop(d.get("id", -1), None)
            return
        if typ in ("orderbook_snapshot", "orderbook_delta"):
            if self.on_raw is not None:
                await _call(self.on_raw, d)
            await self._book_msg(typ, d, msg, now)
            return
        if typ == "trade":
            tk = msg.get("market_ticker")
            t = parse_trade(msg)
            if tk and self.tape.add(tk, msg.get("trade_id"), t):
                await _call(self.on_trade, tk, t)
            return
        if typ == "fill":
            await _call(self.on_fill, parse_fill(msg), msg)
            return
        if typ == "ticker":
            await _call(self.on_ticker, msg)
            return
        if typ == "cfbenchmarks_value":
            await self._brti_msg(msg)
            return
        if typ == "error":
            log.error("kalshi ws error %s", msg)
            return

    async def _book_msg(self, typ: str, d: dict, msg: dict, now: float) -> None:
        sid, seq = d.get("sid"), d.get("seq")
        if sid is not None and seq is not None:
            last = self.seq.get(sid)
            self.seq[sid] = seq
            if last is not None and seq != last + 1 and typ == "orderbook_delta":
                self.gaps += 1
                log.warning("orderbook seq gap on sid %s: %s -> %s; resyncing", sid, last, seq)
                await self.resync(sid)
                return                      # the book is reloaded from a snapshot; don't apply onto a bad book
        tk = msg.get("market_ticker")
        if not tk:
            return
        lb = self.books.setdefault(tk, LiveBook())
        if typ == "orderbook_snapshot":
            yes = parse_levels(msg.get("yes_dollars_fp") or msg.get("yes_dollars"))
            no = parse_levels(msg.get("no_dollars_fp") or msg.get("no_dollars"))
            if self.use_yes_price:
                no = [(round(1.0 - p, 4), q) for p, q in no]
            lb.load(yes, no, now)
            if yes and no and max(yes)[0] + max(no)[0] > 1.0 + 1e-9:
                log.warning("%s snapshot is crossed - check use_yes_price handling", tk)
        else:
            if lb.dirty:
                return                      # wait for the snapshot
            side: Side = "y" if str(msg.get("side", "")).startswith("y") else "n"
            p = float(msg.get("price_dollars", msg.get("price", 0)))
            if side == "n" and self.use_yes_price:
                p = 1.0 - p
            lb.apply(side, p, float(msg.get("delta_fp", msg.get("delta", 0))), now)
        await _call(self.on_book, tk, lb.snapshot(now))

    async def resync(self, sid: int | None = None) -> None:
        """Reload every book of the orderbook subscription: WS get_snapshot + REST refetch in parallel."""
        self.resyncs += 1
        tickers = list(self.tickers)
        for t in tickers:
            self.books.setdefault(t, LiveBook()).dirty = True
        sid = sid if sid is not None else self.sids.get(self.BOOK_CH)
        if sid is not None and self.ws is not None:
            await self._send({"cmd": "update_subscription",
                              "params": {"sids": [sid], "market_tickers": tickers, "action": "get_snapshot"}})
        for t in tickers:
            self._spawn(self._rest_resync(t))

    async def _rest_resync(self, ticker: str) -> None:
        try:
            b = await asyncio.to_thread(self.rest.orderbook, ticker)
        except Exception as e:  # noqa: BLE001
            log.warning("REST resync of %s failed: %s", ticker, e)
            return
        lb = self.books.get(ticker)
        if lb is not None and lb.dirty:      # a WS snapshot may already have arrived; that one wins
            lb.load(b.yes_bids, b.no_bids, self.clock())
            await _call(self.on_book, ticker, lb.snapshot(self.clock()))

    async def _brti_msg(self, msg: dict) -> None:
        try:
            raw = msg.get("data")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if data.get("id", "BRTI") != "BRTI" and msg.get("index_id") != "BRTI":
                return
            ts = float(data.get("time", msg.get("received_at", self.clock() * 1000))) / 1000.0
            await _call(self.on_brti, ts, float(data["value"]))
        except (KeyError, TypeError, ValueError) as e:
            log.debug("bad cfbenchmarks msg %s: %s", msg, e)


# ----------------------------------------------------------------------------------------------------------------
# REST polling fallback (no key needed)
# ----------------------------------------------------------------------------------------------------------------

class PollingFeed:
    """Polls REST per ticker: the full book every `book_interval` (default = `interval`) and new trades every `interval`.
    1/book_interval + 1/interval read requests/ticker/s (0.2 s book + 1 s trades = 6/s).

    Book.ts = when the REST response arrived. Trades are de-duplicated by trade id; because the trades list is read
    once per second, the newest second's tape can be incomplete when a view is built right at the boundary."""

    def __init__(self, rest: KalshiRest, tickers: Iterable[str] = (), *, interval: float = 1.0,
                 book_interval: float | None = None, on_book: Callable | None = None, on_trade: Callable | None = None,
                 clock: Callable[[], float] = time.time, trade_lookback_s: float = 30.0):
        self.rest = rest
        self.tickers: list[str] = list(dict.fromkeys(tickers))
        self.interval = interval
        self.book_interval = book_interval or interval
        self.on_book, self.on_trade = on_book, on_trade
        self.clock = clock
        self.lookback = trade_lookback_s
        self.books: dict[str, Book] = {}
        self.tape = TradeTape()
        self.errors = 0
        self._stop = False

    def book(self, ticker: str) -> Book | None:
        return self.books.get(ticker)

    def trades(self, ticker: str, t0: float, t1: float) -> list[KTrade]:
        return self.tape.between(ticker, t0, t1)

    async def set_tickers(self, tickers: Iterable[str]) -> None:
        new = list(dict.fromkeys(tickers))
        for t in self.tickers:
            if t not in new:
                self.books.pop(t, None)
                self.tape.drop(t)
        self.tickers = new

    def stop(self) -> None:
        self._stop = True

    async def poll_once(self) -> None:
        for tk in list(self.tickers):
            await asyncio.gather(self._poll_book(tk), self._poll_trades(tk))

    async def _poll_book(self, tk: str) -> None:
        try:
            b = await asyncio.to_thread(self.rest.orderbook, tk)
        except Exception as e:  # noqa: BLE001
            self.errors += 1
            log.warning("poll book %s: %s", tk, e)
            return
        b.ts = self.clock()
        self.books[tk] = b
        await _call(self.on_book, tk, b)

    async def _poll_trades(self, tk: str) -> None:
        last = self.tape.last_ts(tk)
        since = (last - 2.0) if last is not None else self.clock() - self.lookback
        try:
            rows = await asyncio.to_thread(self.rest.trades_with_ids, tk, since)
        except Exception as e:  # noqa: BLE001
            self.errors += 1
            log.warning("poll trades %s: %s", tk, e)
            return
        for tid, t in rows:
            if self.tape.add(tk, tid, t):
                await _call(self.on_trade, tk, t)

    async def run(self) -> None:
        if self.book_interval >= self.interval:
            await self._loop(self.interval, self.poll_once)
            return
        async def books():
            for tk in list(self.tickers):
                await self._poll_book(tk)
        async def trades():
            for tk in list(self.tickers):
                await self._poll_trades(tk)
        await asyncio.gather(self._loop(self.book_interval, books), self._loop(self.interval, trades))

    async def _loop(self, every: float, fn) -> None:
        nxt = time.time()
        while not self._stop:
            await fn()
            nxt += every
            await asyncio.sleep(max(0.0, nxt - time.time()))
            if nxt < time.time() - 5 * every:
                nxt = time.time()
