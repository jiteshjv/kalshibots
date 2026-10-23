"""BtcHistory: per-second BTC price store for the last few days (+ optional 1-minute bars for startup seeding).

Semantics follow the research code exactly (scripts/her_full.py `rng`, scripts/her_trades.py `swept`), where the
history is a dense per-second array `ARR` indexed from `T0`, with NaN for seconds without a price:

* ``extreme(a, b, side)``  == ``np.nanmax(ARR[a-T0:b-T0])`` ("y") / ``np.nanmin`` ("n"); None when all NaN/empty.
* ``range_2h(ts)``         == ``rng(op)``: over ``ARR[max(0,i-7200):i]``, (nanmax-nanmin)/nanmean if MORE than 3600
                             seconds have a price, else None (her NaN -> market skipped).
* ``swept(ts, d, k)``      == ``swept(i, d, k)``: False when ``ts-k-300`` is before the start of the history.

Times are integer UTC epoch seconds (floats are floored). The current second must already be in the history before
the strategy asks about it (the app adds each BtcTick before calling Strategy.step).

Minute bars (``add_minute_bar``) only fill the time BEFORE the first per-second sample (startup seeding from
1-minute candles); once per-second data exists it is authoritative.
"""
from __future__ import annotations

import math
import os
import time

import numpy as np

_NAN = float("nan")


class BtcHistory:
    CHUNK = 6 * 3600          # growth step of the per-second buffer

    def __init__(self, max_days: float | None = 4):
        self.max_days = max_days
        self._t0: int | None = None          # epoch second of _buf[0]
        self._buf = np.empty(0, dtype=np.float64)
        self._n = 0                          # used length: seconds [t0, t0+n) are addressable
        self._bars: dict[int, tuple[float, float, float]] = {}   # minute_start -> (high, low, close)

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_array(cls, t0: int, arr: np.ndarray, max_days: float | None = None) -> "BtcHistory":
        """Wrap a dense per-second array (NaN = no price) starting at epoch second t0 (no copy). Backtest use."""
        h = cls(max_days=max_days)
        h._t0 = int(t0)
        h._buf = np.asarray(arr, dtype=np.float64)
        h._n = len(h._buf)
        return h

    def add(self, ts: float, price: float) -> None:
        sec = int(math.floor(ts))
        if self._t0 is None:
            self._t0 = sec
            self._buf = np.full(self.CHUNK, _NAN)
            self._n = 0
        i = sec - self._t0
        if i < 0:                            # older than the buffer: ignore (late out-of-order tick)
            return
        if i >= len(self._buf):
            self._grow(i + 1)
            i = sec - self._t0
        self._buf[i] = float(price)
        if i + 1 > self._n:
            self._n = i + 1
        self._trim()

    def save_seconds(self, path: str | os.PathLike, last_s: int = 1800) -> int:
        """Write the newest `last_s` per-second prices to `path` (.npz, atomic): a restart reloads them so the
        risk check (5/15-min lookbacks) has its history at once. Returns the number of seconds written."""
        if self._t0 is None or self._n == 0:
            return 0
        lo = max(self._n - last_s, 0)
        tmp = f"{path}.tmp.npz"
        np.savez(tmp, t0=np.int64(self._t0 + lo), px=self._buf[lo:self._n])
        os.replace(tmp, path)
        return self._n - lo

    def load_seconds(self, path: str | os.PathLike, max_age_s: float = 900, now: float | None = None) -> int:
        """Add per-second prices saved by save_seconds() if the newest is at most `max_age_s` old (call at startup,
        before live prices arrive). Returns the number of prices added."""
        try:
            z = np.load(path)
            t0, px = int(z["t0"]), np.asarray(z["px"], dtype=np.float64)
        except (OSError, KeyError, ValueError):
            return 0
        now = time.time() if now is None else now
        if not len(px) or now - (t0 + len(px) - 1) > max_age_s:
            return 0
        n = 0
        for i, v in enumerate(px):
            if math.isfinite(v):
                self.add(t0 + i, float(v))
                n += 1
        return n

    def add_minute_bar(self, minute_start_ts: float, high: float, low: float, close: float) -> None:
        m = int(math.floor(minute_start_ts)) // 60 * 60
        self._bars[m] = (float(high), float(low), float(close))
        if self.max_days is not None and len(self._bars) > self.max_days * 1440 + 120:
            cut = self.latest_ts - self.max_days * 86400 if self.latest_ts is not None else None
            if cut is not None:
                for k in [k for k in self._bars if k < cut]:
                    del self._bars[k]

    def minute_bars(self) -> dict[int, tuple[float, float, float]]:
        """All history as minute bars: the stored bars plus bars built from the per-second buffer (which wins)."""
        out = dict(self._bars)
        if self._t0 is not None and self._n:
            a = (-self._t0) % 60
            buf = self._buf[: self._n]
            for i in range(a, self._n - 59, 60):
                seg = buf[i:i + 60]
                ok = seg[seg == seg]
                if len(ok):
                    out[self._t0 + i] = (float(ok.max()), float(ok.min()), float(ok[-1]))
        return out

    def save(self, path: str | os.PathLike) -> int:
        """Write the minute bars to `path` (.npz, atomic). Returns the number of bars written."""
        b = self.minute_bars()
        if not b:
            return 0
        k = sorted(b)
        arr = np.array([b[t] for t in k], dtype=np.float64)
        tmp = str(path) + ".tmp.npz"
        np.savez_compressed(tmp, t=np.array(k, dtype=np.int64), hlc=arr)
        os.replace(tmp, path)
        return len(k)

    def load(self, path: str | os.PathLike, max_age_s: float | None = None, now: float | None = None) -> int | None:
        """Add bars saved by save(). Returns the newest bar's minute (None if nothing usable)."""
        try:
            z = np.load(path)
            t, hlc = z["t"], z["hlc"]
        except Exception:
            return None
        if not len(t):
            return None
        now = time.time() if now is None else now
        if max_age_s is not None:
            keep = t >= now - max_age_s
            t, hlc = t[keep], hlc[keep]
        for m, (h, lo, c) in zip(t.tolist(), hlc.tolist()):
            self.add_minute_bar(m, h, lo, c)
        return int(t[-1]) if len(t) else None

    def _grow(self, need: int) -> None:
        cap = max(len(self._buf) + self.CHUNK, need + self.CHUNK)
        nb = np.full(cap, _NAN)
        nb[: self._n] = self._buf[: self._n]
        self._buf = nb

    def _trim(self) -> None:
        if self.max_days is None:
            return
        keep = int(self.max_days * 86400)
        if self._n <= keep + self.CHUNK:
            return
        drop = self._n - keep
        nb = np.full(keep + self.CHUNK, _NAN)
        nb[:keep] = self._buf[drop: self._n]
        self._buf = nb
        self._t0 += drop
        self._n = keep

    # ------------------------------------------------------------------ bounds
    @property
    def earliest_ts(self) -> int | None:
        c = [x for x in (self._t0, min(self._bars) if self._bars else None) if x is not None]
        return min(c) if c else None

    @property
    def latest_ts(self) -> int | None:
        if self._t0 is not None and self._n > 0:
            return self._t0 + self._n - 1
        return max(self._bars) + 59 if self._bars else None

    def covers(self, seconds: float, at: float | None = None) -> bool:
        """True when the history reaches back at least `seconds` before `at` (default: the latest sample)."""
        e = self.earliest_ts
        at = self.latest_ts if at is None else at
        return e is not None and at is not None and e <= at - seconds

    # ------------------------------------------------------------------ helpers
    def _sec_slice(self, a: int, b: int) -> np.ndarray:
        """Per-second values for [a, b) clipped to the stored range."""
        if self._t0 is None or self._n == 0:
            return self._buf[:0]
        lo = max(a - self._t0, 0)
        hi = min(b - self._t0, self._n)
        if hi <= lo:
            return self._buf[:0]
        return self._buf[lo:hi]

    def _bars_in(self, a: int, b: int) -> list[tuple[float, float, float]]:
        if not self._bars:
            return []
        lim = b if self._t0 is None else min(b, self._t0)     # bars only before per-second data starts
        return [v for k, v in self._bars.items() if a <= k < lim]

    # ------------------------------------------------------------------ queries
    def price_at(self, ts: float, max_age: float | None = None) -> float | None:
        """Last price at or before ts (no older than max_age seconds if given; max_age=0 = that exact second)."""
        sec = int(math.floor(ts))
        if self._t0 is not None and self._n > 0 and sec >= self._t0:
            hi = min(sec - self._t0, self._n - 1)
            lo = 0 if max_age is None else max(0, sec - int(max_age) - self._t0)
            if hi >= lo:
                seg = self._buf[lo: hi + 1]
                ok = np.flatnonzero(seg == seg)
                if len(ok):
                    return float(seg[ok[-1]])
            if max_age is not None:
                return None
        if self._bars:
            m = sec // 60 * 60
            ks = [k for k in self._bars if k <= m]
            if ks:
                k = max(ks)
                if max_age is None or sec - (k + 59) <= max_age:
                    return self._bars[k][2]
        return None

    def extreme(self, start_ts: float, end_ts: float, side: str) -> float | None:
        """Max ("y") or min ("n") price over [start_ts, end_ts). None when there is no price in the range."""
        a, b = int(math.floor(start_ts)), int(math.floor(end_ts))
        best = None
        seg = self._sec_slice(a, b)
        if len(seg) and not np.isnan(seg).all():
            best = float(np.nanmax(seg) if side == "y" else np.nanmin(seg))
        bars = self._bars_in(a, b)
        if bars:
            v = max(h for h, _, _ in bars) if side == "y" else min(lo for _, lo, _ in bars)
            best = v if best is None else (max(best, v) if side == "y" else min(best, v))
        return best

    def range_2h(self, ts: float) -> float | None:
        """(high - low) / mean over [ts-7200, ts); None unless more than 3600 seconds of data (her `rng`)."""
        b = int(math.floor(ts))
        a = b - 7200
        seg = self._sec_slice(a, b)
        cnt = int(np.sum(seg == seg)) if len(seg) else 0
        if cnt > 3600:
            return float((np.nanmax(seg) - np.nanmin(seg)) / np.nanmean(seg))
        bars = self._bars_in(a, b)
        if bars and cnt + 60 * len(bars) > 3600:          # startup: 1-minute candles (approximation)
            vals = [c for _, _, c in bars]
            hi = max(h for h, _, _ in bars)
            lo = min(x for _, x, _ in bars)
            if cnt:
                vals += list(seg[seg == seg])
                hi = max(hi, float(np.nanmax(seg)))
                lo = min(lo, float(np.nanmin(seg)))
            return float((hi - lo) / np.mean(vals))
        return None

    def swept(self, ts: float, direction: int, k: int, lookback: int = 300) -> bool:
        """her_trades.swept: in the last `lookback` s (incl. ts), did BTC trade beyond the extreme of the k seconds
        before that, on side direction (+1 = above the prior high, -1 = below the prior low)?"""
        t = int(math.floor(ts))
        e = self.earliest_ts
        if e is None or t - k - lookback < e:
            return False
        side = "y" if direction > 0 else "n"
        prior = self.extreme(t - k - lookback, t - lookback, side)
        last = self.extreme(t - lookback, t + 1, side)
        if prior is None or last is None:
            return False
        return bool(direction * (last - prior) > 0)
