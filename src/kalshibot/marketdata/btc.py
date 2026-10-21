"""BTC price feed: public exchange WebSockets -> research composite (+ BRTI when a Kalshi key exists) -> 1 BtcTick/s.

Composite (same estimator as the backtest's `data/composite_1s.npy`, see scripts/calibrate_brti.py / recent_dl.py):

    composite = sum(w_i * p_i) / sum(w_i) + offset        over the FRESH primary sources i

with the fitted research weights CB 0.653, KR 0.251 (Binance 0.096 is geo-blocked here, so it is dropped and the rest
renormalised - exactly what the research pipeline does for seconds without Binance data: `nobn`) and offset +$3.25.

Bitstamp and Gemini are FALLBACK ONLY (weight 0 while Coinbase or Kraken is fresh). Reason: they were not part of
the fitted composite, so giving them weight would change the estimator the strategy was backtested on, and each has
its own basis vs BRTI that the offset doesn't cover. When neither primary is fresh we use their mean after removing
a live basis (EMA of fallback price - primary composite, learned while both were fresh), so a Coinbase+Kraken outage
degrades the price by a few dollars instead of halting it.

BRTI: polled via KalshiRest.brti() (needs a key + CF Benchmarks entitlement; 50 tokens/request, so every 2 s by
default) or pushed in with set_brti() (e.g. from the Kalshi WS `cfbenchmarks_value` channel). BtcTick.price = BRTI
while fresh, else the composite (types.BtcTick contract).

Optional live bias correction (`BiasTracker`, from scripts/paper_trader.py): median of (official settlement value -
our 60 s composite average) over the last 96 settled markets, added to the composite. Off by default because the
backtest did not use it.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import ssl
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from ..types import BtcTick
from .kalshi_rest import CA_FILE, KalshiError

log = logging.getLogger("kalshibot.marketdata.btc")

ROOT = Path(__file__).resolve().parents[3]
WEIGHTS_FILE = ROOT / "data" / "composite_weights.json"
DEFAULT_WEIGHTS = {"w_cb": 0.6532100767557303, "w_kr": 0.2508347008574827, "w_bn": 0.095955222386787,
                   "offset": 3.252103672177359}


def load_weights(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else WEIGHTS_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return dict(DEFAULT_WEIGHTS)


def ssl_context() -> ssl.SSLContext:
    try:
        return ssl.create_default_context(cafile=CA_FILE)
    except (FileNotFoundError, ssl.SSLError):
        return ssl.create_default_context()


# ----------------------------------------------------------------------------------------------------------------
# Exchange sources (all public, no keys)
# ----------------------------------------------------------------------------------------------------------------

def parse_coinbase(d: dict) -> float | None:
    return float(d["price"]) if d.get("type") in ("match", "last_match") and "price" in d else None


def parse_kraken(d: dict) -> float | None:
    if d.get("channel") == "trade" and d.get("data"):
        return float(d["data"][-1]["price"])
    return None


def parse_bitstamp(d: dict) -> float | None:
    if d.get("event") == "trade" and isinstance(d.get("data"), dict) and "price" in d["data"]:
        return float(d["data"]["price"])
    return None


def parse_gemini(d: dict) -> float | None:
    if d.get("type") != "update":
        return None
    px = [e["price"] for e in d.get("events") or [] if e.get("type") == "trade" and "price" in e]
    return float(px[-1]) if px else None


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    subscribe: dict | None
    parse: Callable[[dict], float | None]
    max_age: float                  # seconds a last-trade price counts as fresh


SOURCES: dict[str, Source] = {
    "cb": Source("cb", "wss://ws-feed.exchange.coinbase.com",
                 {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["matches"]}, parse_coinbase, 15.0),
    "kr": Source("kr", "wss://ws.kraken.com/v2",
                 {"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}}, parse_kraken, 30.0),
    "bs": Source("bs", "wss://ws.bitstamp.net",
                 {"event": "bts:subscribe", "data": {"channel": "live_trades_btcusd"}}, parse_bitstamp, 30.0),
    "gm": Source("gm", "wss://api.gemini.com/v1/marketdata/BTCUSD?trades=true&bids=false&offers=false&heartbeat=true",
                 None, parse_gemini, 30.0),
}
PRIMARY = ("cb", "kr")
FALLBACK = ("bs", "gm")


# ----------------------------------------------------------------------------------------------------------------
# History interface (implemented by the strategy agent's kalshibot.history.BtcHistory)
# ----------------------------------------------------------------------------------------------------------------

class HistoryLike(Protocol):
    def add(self, ts: float, price: float) -> None: ...
    def add_minute_bar(self, minute_start_ts: float, high: float, low: float, close: float) -> None: ...


# ----------------------------------------------------------------------------------------------------------------
# Live bias correction (optional)
# ----------------------------------------------------------------------------------------------------------------

class BiasTracker:
    """median(official settlement - our raw 60 s composite average) over the last `window` settled markets."""

    def __init__(self, window: int = 96, min_n: int = 4, keep_s: float = 3 * 3600):
        self.window, self.min_n = window, min_n
        self.errs: deque[float] = deque(maxlen=200)
        self.samples: deque[tuple[float, float]] = deque()
        self.keep_s = keep_s

    def observe(self, ts: float, raw_composite: float) -> None:
        self.samples.append((ts, raw_composite))
        while self.samples and self.samples[0][0] < ts - self.keep_s:
            self.samples.popleft()

    def avg60(self, close_ts: float) -> float | None:
        v = [p for t, p in self.samples if close_ts - 60 <= t < close_ts]
        return sum(v) / len(v) if len(v) >= 50 else None

    def settle(self, close_ts: float, expiration_value: float) -> float | None:
        """Record one settled market; returns the error added (None if we had too few samples)."""
        raw = self.avg60(close_ts)
        if raw is None:
            return None
        e = float(expiration_value) - raw
        self.errs.append(e)
        return e

    def value(self) -> float:
        e = sorted(list(self.errs)[-self.window:])
        return e[len(e) // 2] if len(e) >= self.min_n else 0.0

    def state(self) -> dict:
        return {"errs": list(self.errs)}

    def load_state(self, d: dict) -> None:
        self.errs.extend(float(x) for x in d.get("errs") or [])


# ----------------------------------------------------------------------------------------------------------------
# Feed
# ----------------------------------------------------------------------------------------------------------------

@dataclass
class _Px:
    price: float | None = None
    ts: float = 0.0                 # local receive time
    n: int = 0
    connected: bool = False


@dataclass
class BtcFeed:
    """Usage:  feed = BtcFeed(rest=kalshi_rest_or_None, on_tick=cb, history=h);  feed.seed_history(h);
    asyncio.create_task(feed.run());  feed.latest() -> BtcTick."""

    rest: Any = None                                    # KalshiRest; BRTI is polled only if rest.has_key
    on_tick: Callable[[BtcTick], Any] | None = None
    history: HistoryLike | None = None                  # if set, every non-stale tick is added (composite price)
    sources: Iterable[str] = ("cb", "kr", "bs", "gm")
    weights: dict | None = None                         # composite_weights.json shape; default = research file
    max_age: dict | None = None                         # per-source freshness override, seconds
    bias: BiasTracker | Callable[[], float] | None = None
    brti_interval: float = 2.0
    brti_max_age: float = 5.0
    basis_alpha: float = 1 / 300                        # EMA for fallback sources' basis vs primary
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        w = self.weights or load_weights()
        self.w = {"cb": float(w["w_cb"]), "kr": float(w["w_kr"])}
        self.offset = float(w["offset"])
        self.sources = tuple(self.sources)
        self.px: dict[str, _Px] = {s: _Px() for s in self.sources}
        self.ages_max = {s: SOURCES[s].max_age if s in SOURCES else 30.0 for s in self.sources}
        self.ages_max.update(self.max_age or {})
        self.basis: dict[str, float | None] = {s: None for s in FALLBACK}
        self.brti_px: float | None = None
        self.brti_ts: float = 0.0
        self.brti_errors = 0
        self._latest: BtcTick | None = None
        self._last_price: float | None = None
        self._stop = False
        self._tasks: list[asyncio.Task] = []

    # -- inputs ------------------------------------------------------------------------------------------------
    def update(self, source: str, price: float, ts: float | None = None) -> None:
        p = self.px.setdefault(source, _Px())
        p.price, p.ts, p.n = float(price), self.clock() if ts is None else ts, p.n + 1

    def set_brti(self, ts: float, price: float) -> None:
        self.brti_ts, self.brti_px = float(ts), float(price)

    # -- composite ---------------------------------------------------------------------------------------------
    def fresh(self, source: str, now: float) -> bool:
        p = self.px.get(source)
        return p is not None and p.price is not None and now - p.ts <= self.ages_max.get(source, 30.0)

    def ages(self, now: float | None = None) -> dict[str, float | None]:
        now = self.clock() if now is None else now
        out: dict[str, float | None] = {s: (now - p.ts if p.price is not None else None) for s, p in self.px.items()}
        out["brti"] = now - self.brti_ts if self.brti_px is not None else None
        return out

    def age(self, now: float | None = None) -> float | None:
        """Seconds since the newest price update from any fresh input (None if nothing has ever arrived)."""
        a = [v for v in self.ages(now).values() if v is not None]
        return min(a) if a else None

    def _primary_raw(self, now: float) -> float | None:
        num = den = 0.0
        for s in PRIMARY:
            if s in self.px and self.fresh(s, now):
                num += self.w[s] * self.px[s].price  # type: ignore[operator]
                den += self.w[s]
        return num / den if den > 0 else None

    def raw_composite(self, now: float | None = None) -> float | None:
        """Composite without the bias correction (with the research offset)."""
        now = self.clock() if now is None else now
        prim = self._primary_raw(now)
        if prim is not None:
            return prim + self.offset
        fb = [self.px[s].price - (self.basis.get(s) or 0.0) for s in FALLBACK  # type: ignore[operator]
              if s in self.px and self.fresh(s, now)]
        return (sum(fb) / len(fb) + self.offset) if fb else None

    def _bias_value(self) -> float:
        if self.bias is None:
            return 0.0
        return self.bias.value() if isinstance(self.bias, BiasTracker) else float(self.bias())

    def composite(self, now: float | None = None) -> float | None:
        c = self.raw_composite(now)
        return None if c is None else c + self._bias_value()

    def _learn_basis(self, now: float) -> None:
        prim = self._primary_raw(now)
        if prim is None:
            return
        for s in FALLBACK:
            if s in self.px and self.fresh(s, now):
                d = self.px[s].price - prim  # type: ignore[operator]
                b = self.basis.get(s)
                self.basis[s] = d if b is None else b + self.basis_alpha * (d - b)

    def brti_fresh(self, now: float) -> bool:
        return self.brti_px is not None and now - self.brti_ts <= self.brti_max_age

    def tick(self, now: float | None = None) -> BtcTick:
        """Build the tick for `now` (also updates fallback basis, bias samples and history)."""
        now = self.clock() if now is None else now
        self._learn_basis(now)
        raw = self.raw_composite(now)
        comp = None if raw is None else raw + self._bias_value()
        if raw is not None and isinstance(self.bias, BiasTracker):
            self.bias.observe(now, raw)
        brti = self.brti_px if self.brti_fresh(now) else None
        if brti is not None:
            t = BtcTick(ts=now, price=brti, brti=brti, composite=comp, source="brti", stale=False)
        elif comp is not None:
            t = BtcTick(ts=now, price=comp, brti=None, composite=comp, source="composite", stale=False)
        else:
            last = self._last_price if self._last_price is not None else math.nan
            t = BtcTick(ts=now, price=last, brti=None, composite=None, source="composite", stale=True)
        if not t.stale:
            self._last_price = t.price
            if self.history is not None:
                self.history.add(math.floor(now), comp if comp is not None else t.price)
        self._latest = t
        return t

    def latest(self) -> BtcTick | None:
        return self._latest

    # -- network -----------------------------------------------------------------------------------------------
    async def _source_loop(self, src: Source) -> None:
        import websockets
        back = 1.0
        while not self._stop:
            try:
                async with websockets.connect(src.url, open_timeout=10, ping_interval=20, ping_timeout=20,
                                              ssl=ssl_context(), max_size=2 ** 22) as ws:
                    if src.subscribe is not None:
                        await ws.send(json.dumps(src.subscribe))
                    self.px[src.name].connected = True
                    log.info("%s connected", src.name)
                    back = 1.0
                    async for m in ws:
                        try:
                            d = json.loads(m)
                        except ValueError:
                            continue
                        if src.name == "bs" and d.get("event") == "bts:request_reconnect":
                            break
                        try:
                            p = src.parse(d) if isinstance(d, dict) else None
                        except (KeyError, TypeError, ValueError, IndexError):
                            p = None
                        if p:
                            self.update(src.name, p)
                        if self._stop:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("%s disconnected: %s %s", src.name, type(e).__name__, str(e)[:120])
            self.px[src.name].connected = False
            if self._stop:
                break
            await asyncio.sleep(back)
            back = min(back * 2, 30.0)

    async def _brti_loop(self) -> None:
        interval = self.brti_interval
        while not self._stop:
            try:
                ts, p = await asyncio.to_thread(self.rest.brti)
                now = self.clock()
                if ts > now + 60:                          # clock skew the other way: trust the value as current
                    ts = now
                if now - ts <= self.brti_max_age:          # an old value is never relabelled as current
                    self.set_brti(ts, p)
                interval = self.brti_interval
            except KalshiError as e:
                self.brti_errors += 1
                if getattr(e, "status", None) in (401, 403):
                    if self.brti_errors == 1:
                        log.warning("BRTI not available for this key (%s); retrying every 60 s", e.status)
                    interval = 60.0
                else:
                    log.debug("brti: %s", e)
            except Exception as e:  # noqa: BLE001
                self.brti_errors += 1
                log.debug("brti: %s", e)
            await asyncio.sleep(interval)

    async def run(self) -> None:
        """Start the source sockets (+ BRTI poller) and emit one tick per wall-clock second until stop()."""
        for s in self.sources:
            if s in SOURCES:
                self._tasks.append(asyncio.create_task(self._source_loop(SOURCES[s])))
        if self.rest is not None and getattr(self.rest, "has_key", False):
            self._tasks.append(asyncio.create_task(self._brti_loop()))
        try:
            while not self._stop:
                await asyncio.sleep(max(0.0, math.floor(time.time()) + 1 - time.time()))
                t = self.tick(float(round(self.clock())))     # whole-second ticks
                if self.on_tick is not None:
                    try:
                        r = self.on_tick(t)
                        if inspect.isawaitable(r):
                            await r
                    except Exception:  # noqa: BLE001
                        log.exception("on_tick failed")
        finally:
            for t in self._tasks:
                t.cancel()

    def stop(self) -> None:
        self._stop = True

    # -- history seeding ---------------------------------------------------------------------------------------
    def seed_history(self, history: HistoryLike, days: float = 3, *, get: Callable[[str, dict], list] | None = None,
                     now: float | None = None, product: str = "BTC-USD", pause: float = 0.15) -> int:
        """Load `days` of Coinbase public 1-min candles (300 per request) into history.add_minute_bar(), with the
        composite offset added (Coinbase is the dominant composite weight). Blocking; wrap in asyncio.to_thread
        from async code. Returns the number of bars added."""
        get = get or _coinbase_get
        now = self.clock() if now is None else now
        last = int(now // 60) * 60 - 60              # last COMPLETE minute
        first = last - int(days * 86400) + 60
        bars: dict[int, tuple[float, float, float]] = {}
        end = last
        while end >= first:
            start = max(first, end - 299 * 60)
            rows = get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                       {"granularity": 60, "start": _iso(start), "end": _iso(end)})
            for r in rows or []:
                t, lo, hi, _o, c = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])
                if first <= t <= last:
                    bars[t] = (hi, lo, c)
            end = start - 60
            if pause and end >= first:
                time.sleep(pause)
        off = self.offset
        for t in sorted(bars):
            hi, lo, c = bars[t]
            history.add_minute_bar(t, hi + off, lo + off, c + off)
        log.info("seeded %d Coinbase minute bars (%.1f days)", len(bars), days)
        return len(bars)

    async def aseed_history(self, history: HistoryLike, days: float = 3) -> int:
        return await asyncio.to_thread(self.seed_history, history, days)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coinbase_get(url: str, params: dict, retries: int = 4) -> list:
    from urllib.parse import urlencode
    full = f"{url}?{urlencode(params)}"
    ctx = ssl_context()
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "kalshibot"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == retries:
                raise
            log.debug("coinbase candles retry %d: %s", i + 1, e)
            time.sleep(0.5 * 2 ** i)
    return []
