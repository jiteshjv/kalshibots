"""Live gold price for KXGOLD15M (paper/live), drop-in for BtcFeed (latest / age / run / stop / aseed_history).

Kalshi settles gold on Pyth (Metal.Index.1OZGOLD/USD, 1-minute close). Live prices:
  * PYTH_API_KEY set: Pyth Hermes (the settlement source). Sent as "Authorization: Bearer <key>" unless PYTH_AUTH_HEADER
    names another header (e.g. x-api-key). PYTH_URL overrides the Hermes base URL.
  * otherwise (free, no key): the Hyperliquid xyz:GOLD oracle price, falling back to the Swissquote public quote (mid)
    when Hyperliquid fails. Live test vs Kalshi strikes (Oct 20): both within ~$0.2 typical, ~$0.3 p90.

History (2-hour range, 2-day sweeps) is seeded from Dukascopy XAU/USD ticks (public, no key) for the last `days` days and
shifted to the Pyth level with the median (Kalshi strike - Dukascopy price at the open) of recent settled gold markets.
Dukascopy publishes each hour late, so the minutes after its last bar are filled from Hyperliquid xyz:GOLD 1-minute candles
(shifted to the Dukascopy level where the two overlap). Hyperliquid also trades weekends, so it only fills that recent gap.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import lzma
import math
import os
import statistics
import struct
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from kalshibot.marketdata.btc import ssl_context
from kalshibot.types import BtcTick

log = logging.getLogger("kalshibot.gold")
PYTH_GOLD_ID = "7fd2c87083dd8fd5af2486a43f40b8e444acd77fda4e96263a53421cd079c387"   # Metal.Index.1OZGOLD/USD
HL_URL = "https://api.hyperliquid.xyz/info"
SQ_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
DUKA = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"


class GoldFeed:
    def __init__(self, history=None, rest=None, data_dir: str | os.PathLike | None = None, *, poll_s: float = 1.0,
                 stale_s: float = 5.0, feed_id: str = PYTH_GOLD_ID):
        self.history, self.rest, self.poll_s, self.stale_s, self.feed_id = history, rest, poll_s, stale_s, feed_id
        self.cache = Path(os.path.expanduser(str(data_dir or "~/kalshibot-gold-data"))) / "duka_cache"
        self.key = os.environ.get("PYTH_API_KEY", "").strip()
        self.header = os.environ.get("PYTH_AUTH_HEADER", "Authorization").strip()
        self.base = os.environ.get("PYTH_URL", "https://hermes.pyth.network").rstrip("/")
        self._px: float | None = None
        self._pub: float | None = None
        self._latest: BtcTick | None = None
        self._stop = False
        self.source = "pyth" if self.key else "hyperliquid"
        self.last_error = ""
        self.on_price: Callable[[float, float, str], Any] | None = None    # (price, publish/fetch time, source) per poll (recorder)

    # -- feed interface ----------------------------------------------------------------------------------------
    def age(self, now: float | None = None) -> float | None:
        return None if self._pub is None else (time.time() if now is None else now) - self._pub

    def latest(self) -> BtcTick | None:
        return self._latest

    def stop(self) -> None:
        self._stop = True

    def _get(self, url: str, body: bytes | None = None):
        hdr = {"User-Agent": "Mozilla/5.0"} | ({"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), context=ssl_context(), timeout=5) as r:
            return json.load(r)

    def _fetch_free(self) -> tuple[float, float]:
        """Hyperliquid xyz:GOLD oracle; Swissquote mid when Hyperliquid fails. Both stamped with the local clock."""
        try:
            meta, ctx = self._get(HL_URL, json.dumps({"type": "metaAndAssetCtxs", "dex": "xyz"}).encode())
            i = [a["name"] for a in meta["universe"]].index("xyz:GOLD")
            self.source = "hyperliquid"
            return float(ctx[i]["oraclePx"]), time.time()
        except Exception as e:
            hl_err = e
        try:
            q = [x for x in self._get(SQ_URL)[0]["spreadProfilePrices"] if x["spreadProfile"] == "elite"][0]
            self.source = "swissquote"
            return (float(q["bid"]) + float(q["ask"])) / 2, time.time()
        except Exception as e:
            raise RuntimeError(f"hyperliquid: {hl_err}; swissquote: {e}") from None

    def _fetch(self) -> tuple[float, float]:
        if not self.key:
            return self._fetch_free()
        url = f"{self.base}/v2/updates/price/latest?ids[]={self.feed_id}&parsed=true"
        hdr = {"User-Agent": "kalshibot"}
        if self.key:
            hdr[self.header] = f"Bearer {self.key}" if self.header.lower() == "authorization" else self.key
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), context=ssl_context(), timeout=5) as r:
            p = json.load(r)["parsed"][0]["price"]
        return int(p["price"]) * 10 ** int(p["expo"]), float(p["publish_time"])

    async def run(self) -> None:
        warned = 0.0
        while not self._stop:
            now = time.time()
            try:
                px, pub = await asyncio.to_thread(self._fetch)
                self._px, self._pub, self.last_error = px, pub, ""
                if self.on_price is not None:
                    self.on_price(px, pub, self.source)
            except Exception as e:                           # keep going; ticks turn stale
                self.last_error = f"{'pyth' if self.key else 'gold feed'}: {type(e).__name__}: {e}"
            stale = self._px is None or now - (self._pub or 0) > self.stale_s
            if stale and now - warned > 300 and self.last_error:
                log.warning("gold price unavailable (%s)", self.last_error)
                warned = now
            if not stale and self.history is not None:
                self.history.add(math.floor(now), self._px)
            self._latest = BtcTick(ts=now, price=self._px if self._px is not None else math.nan, source=self.source, stale=stale)
            await asyncio.sleep(self.poll_s)

    # -- history seed (Dukascopy) --------------------------------------------------------------------------------
    @staticmethod
    def _trading_hour(t: int) -> bool:
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        wd = d.weekday()
        return not (wd == 5 or (wd == 4 and d.hour >= 21) or (wd == 6 and d.hour < 22))

    def _hour(self, t: int) -> list[tuple[int, float]]:
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        f = self.cache / f"{d:%Y-%m-%d_%H}.bi5"
        if not f.exists() or f.stat().st_size == 0 and time.time() - t < 3 * 3600:
            url = DUKA.format(y=d.year, m=d.month - 1, d=d.day, h=d.hour)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "kalshibot"}),
                                            context=ssl_context(), timeout=30) as r:
                    raw = r.read()
            except urllib.error.HTTPError as e:
                raw = b"" if e.code == 404 else None
            except Exception:
                raw = None
            if raw is None:
                return []
            self.cache.mkdir(parents=True, exist_ok=True)
            f.write_bytes(raw)
            time.sleep(0.5)
        raw = f.read_bytes()
        if not raw:
            return []
        try:
            data = lzma.decompress(raw)
        except Exception:
            return []
        out = []
        for i in range(0, len(data) - 19, 20):
            ms, ask, bid, _, _ = struct.unpack(">IIIff", data[i:i + 20])
            out.append((t + ms // 1000, (ask + bid) / 2000.0))
        return out

    def _offset(self, ticks: dict[int, float]) -> float:
        """Median (Kalshi strike - Dukascopy price just before the open) over recent settled gold markets."""
        if self.rest is None or not ticks:
            return 0.0
        try:
            ms = self.rest.request("GET", "/markets", params={"series_ticker": "KXGOLD15M", "status": "settled", "limit": 60}).get("markets", [])
        except Exception:
            return 0.0
        keys = sorted(ticks)
        diffs = []
        for m in ms:
            k, ot = m.get("floor_strike"), m.get("open_time")
            if not k or not ot:
                continue
            op = int(dt.datetime.fromisoformat(ot.replace("Z", "+00:00")).timestamp())
            prev = [ticks[s] for s in range(op - 60, op) if s in ticks]
            if prev:
                diffs.append(float(k) - prev[-1])
        return statistics.median(diffs) if diffs else 0.0

    def _hl_candles(self, start: int, end: int) -> list[tuple[int, float, float, float]]:
        """Hyperliquid xyz:GOLD 1-minute candles (minute ts, high, low, close) over [start, end)."""
        req = {"type": "candleSnapshot", "req": {"coin": "xyz:GOLD", "interval": "1m", "startTime": start * 1000, "endTime": end * 1000}}
        try:
            c = self._get(HL_URL, json.dumps(req).encode())
        except Exception as e:
            log.warning("hyperliquid candles: %s", e)
            return []
        return [(int(x["t"]) // 1000, float(x["h"]), float(x["l"]), float(x["c"])) for x in c if start <= int(x["t"]) // 1000 < end]

    def seed_history(self, history, days: float = 3) -> int:
        """Load `days` of gold TRADING-hours history (the weekend close and the daily 21:00-22:00 UTC break are
        skipped). Hyperliquid 1-minute candles first (fast, reliable), shifted to the Kalshi/Pyth level with recent
        strikes; Dukascopy hourly files only if Hyperliquid gives nothing."""
        now = int(time.time()) // 60 * 60
        need = int(days * 24 * 60)                                     # trading minutes wanted
        bars: dict[int, tuple[float, float, float]] = {}
        end, oldest = now, now - 14 * 86400
        while end > oldest and sum(1 for m in bars if self._trading_hour(m)) < need:
            start = max(oldest, end - 5000 * 60)                       # Hyperliquid: at most 5000 candles per request
            got = self._hl_candles(start, end)
            if not got:
                break
            for m, h, lo, c in got:
                bars[m] = (h, lo, c)
            end = start
        bars = {m: v for m, v in bars.items() if self._trading_hour(m)}
        src = "Hyperliquid"
        if not bars:                                                   # fallback: Dukascopy ticks
            src = "Dukascopy"
            hours, t = [], now // 3600 * 3600
            while sum(1 for h in hours if self._trading_hour(h)) < days * 24 and t > now - 12 * 86400:
                hours.append(t); t -= 3600
            ticks: dict[int, float] = {}
            for t in sorted(hours):
                for sec, p in self._hour(t):
                    ticks[sec] = p
            mins: dict[int, list[float]] = {}
            for sec in sorted(ticks):
                mins.setdefault(sec // 60 * 60, []).append(ticks[sec])
            bars = {m: (max(v), min(v), v[-1]) for m, v in mins.items()}
        off = self._offset({m: c for m, (_, _, c) in bars.items()})
        for m, (h, lo, c) in sorted(bars.items()):
            history.add_minute_bar(m, h + off, lo + off, c + off)
        log.info("seeded %d gold minute bars from %s (%.1f trading days, offset to Kalshi strikes %+.2f)",
                 len(bars), src, len(bars) / 1380, off)
        return len(bars)

    async def aseed_history(self, history, days: float = 3) -> int:
        return await asyncio.to_thread(self.seed_history, history, days)
