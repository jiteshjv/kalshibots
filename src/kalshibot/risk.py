"""RiskGuard: gate for NEW ENTRIES only. Stops, take-profits, cancels and every sell always pass.

    guard = RiskGuard(settings.risk, journal, data_dir=settings.data_path, open_orders=broker.open_order_count)
    ok, reason = guard.allow(intent, view)       # reason "" when ok
    if guard.must_flatten(): cancel all + stop trading     (kill file present)

Entry = intent.kind in ("limit_buy", "market_buy") (incl. the market fallback after an unfilled bid).
Rules, checked in this order (first hit is the reason):
    kill_file         <data_dir>/<kill_file> exists (absolute kill_file paths are used as is)
    daily_loss        realized P&L for the current UTC day <= -daily_loss_limit
    stale_btc         no BtcTick, tick.stale, or now - tick.ts > stale_btc_s
    no_book/stale_book view.book is None, or now - book.ts > stale_book_s
    brti_divergence   |brti - composite| > max_brti_vs_composite (when both are known)
    max_open_orders   open orders >= max_open_orders
Realized P&L comes from the injected `realized_pnl(day: "YYYY-MM-DD") -> float`, else `journal.realized_pnl(day)`.
Open orders come from the injected `open_orders()` (int or list), else `journal.open_orders()`.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kalshibot.config import LIVE_CONFIRM_PHRASE, RiskCfg, Settings
from kalshibot.types import OrderIntent, SecondView

ENTRY_KINDS = ("limit_buy", "market_buy")


class LiveModeRefused(RuntimeError):
    pass


def live_mode_ok(cfg: RiskCfg | Settings) -> tuple[bool, str]:
    """True only when mode == "live" AND live_confirm equals config.LIVE_CONFIRM_PHRASE exactly."""
    risk = cfg.risk if isinstance(cfg, Settings) else cfg
    if risk.mode == "demo":
        return True, ""                                   # Kalshi's demo exchange: fake money, no confirmation needed
    if risk.mode != "live":
        return False, f"risk.mode is {risk.mode!r}, not 'live'"
    if risk.live_confirm != LIVE_CONFIRM_PHRASE:
        return False, "risk.live_confirm does not match the confirm phrase"
    return True, ""


def utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


class RiskGuard:
    def __init__(self, cfg: RiskCfg, journal: Any = None, *, data_dir: str | os.PathLike | None = None,
                 realized_pnl: Callable[[str], float] | None = None,
                 open_orders: Callable[[], Any] | None = None, clock: Callable[[], float] = time.time):
        if getattr(cfg, "news_mode", "off") not in ("off", "day", "window"):
            raise ValueError(f"risk.news_mode must be 'off', 'day' or 'window', got {cfg.news_mode!r}")
        if cfg.mode not in ("paper", "live", "demo"):
            raise ValueError(f"risk.mode must be 'paper', 'live' or 'demo', got {cfg.mode!r}")
        if cfg.mode == "live":
            ok, why = live_mode_ok(cfg)
            if not ok:
                raise LiveModeRefused(why)
        self.cfg = cfg
        self.journal = journal
        self.data_dir = Path(os.path.expanduser(str(data_dir))) if data_dir is not None else None
        self._realized = realized_pnl
        self._open_orders = open_orders
        self.clock = clock
        self.allowed = 0
        self.passed_exits = 0
        self.blocked: Counter[str] = Counter()
        self.last_reason = ""

    # -------------------------------------------------------------------------------------------------- inputs
    @property
    def kill_path(self) -> Path:
        p = Path(os.path.expanduser(self.cfg.kill_file))
        if p.is_absolute() or self.data_dir is None:
            return p
        return self.data_dir / p

    def kill_present(self) -> bool:
        return bool(self.cfg.kill_file) and self.kill_path.exists()

    def must_flatten(self) -> bool:
        return self.kill_present()

    @property
    def disarm_path(self) -> Path:
        p = Path(os.path.expanduser(getattr(self.cfg, "disarm_file", "") or "DISARM"))
        if p.is_absolute() or self.data_dir is None:
            return p
        return self.data_dir / p

    # -------------------------------------------------------------------------------------------------- news days
    def calendar_path(self) -> Path:
        p = Path(os.path.expanduser(self.cfg.news_calendar))
        return p if p.is_absolute() or self.data_dir is None else self.data_dir / p

    def _calendar(self) -> list[tuple[float, str]]:
        """(release unix ts, event) from the news calendar file; re-read when the file changes. Missing file -> []."""
        p = Path(os.path.expanduser(self.cfg.news_calendar))
        if not p.is_absolute() and self.data_dir is not None:
            p = self.data_dir / p
        try:
            mt = p.stat().st_mtime
        except OSError:
            return []
        if getattr(self, "_cal_mtime", None) != mt:
            import csv
            import datetime as _dt
            from zoneinfo import ZoneInfo
            et, rows = ZoneInfo("America/New_York"), []
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    try:
                        t = _dt.datetime.fromisoformat(f"{r['date'].strip()}T{r['time_et'].strip()}").replace(tzinfo=et)
                        rows.append((t.timestamp(), r["event"].strip()))
                    except (KeyError, ValueError):
                        continue
            self._cal, self._cal_mtime = rows, mt
        return self._cal

    def _news_spec(self) -> tuple[set, dict]:
        c = self.cfg
        wins = {}
        for part in (getattr(c, "news_windows", "") or "").split(","):
            bits = part.strip().split(":")
            if len(bits) == 3:
                wins[bits[0]] = (int(bits[1]), int(bits[2]))
        want = set(wins) if wins else {e.strip() for e in c.news_events.split(",") if e.strip()}
        return want, wins

    def upcoming_news(self, now: float, days: float = 21) -> list[tuple[float, float, str]]:
        """(start, end, event) of the news no-entry windows from now to `days` ahead (window mode), or whole PT days
        (day mode). Windows already over are left out."""
        c = self.cfg
        if c.news_mode == "off":
            return []
        want, wins = self._news_spec()
        import datetime as _dt
        from zoneinfo import ZoneInfo
        pt = ZoneInfo("America/Los_Angeles")
        out = []
        for ts, ev in self._calendar():
            if ev not in want or ts > now + days * 86400:
                continue
            if c.news_mode == "day":
                d0 = _dt.datetime.fromtimestamp(ts, pt).replace(hour=0, minute=0, second=0, microsecond=0)
                a, b = d0.timestamp(), (d0 + _dt.timedelta(days=1)).timestamp()
            else:
                before, after = wins.get(ev, (c.news_before_min, c.news_after_min))
                a, b = ts - 60 * before, ts + 60 * after
            if b >= now:
                out.append((a, b, ev))
        return sorted(out)

    def news_block(self, now: float) -> str:
        """'' or the reason entries are blocked by the news-day setting (risk.news_mode)."""
        c = self.cfg
        if c.news_mode == "off":
            return ""
        want, wins = self._news_spec()
        import datetime as _dt
        from zoneinfo import ZoneInfo
        pt = ZoneInfo("America/Los_Angeles")
        today = _dt.datetime.fromtimestamp(now, pt).date()
        for ts, ev in self._calendar():
            if ev not in want:
                continue
            if c.news_mode == "day" and _dt.datetime.fromtimestamp(ts, pt).date() == today:
                return f"news_day:{ev}"
            before, after = wins.get(ev, (c.news_before_min, c.news_after_min))
            if c.news_mode == "window" and ts - 60 * before <= now <= ts + 60 * after:
                return f"news_window:{ev}"
        return ""

    def disarm_present(self) -> bool:
        return bool(getattr(self.cfg, "disarm_file", "")) and self.disarm_path.exists()

    def realized_today(self, now: float | None = None) -> float:
        day = utc_day(self.clock() if now is None else now)
        if self._realized is not None:
            return float(self._realized(day) or 0.0)
        if self.journal is not None and hasattr(self.journal, "realized_pnl"):
            return float(self.journal.realized_pnl(day) or 0.0)
        return 0.0

    def open_order_count(self) -> int:
        src = self._open_orders
        if src is None and self.journal is not None and hasattr(self.journal, "open_orders"):
            src = self.journal.open_orders
        if src is None:
            return 0
        n = src()
        return n if isinstance(n, int) else len(n)

    # -------------------------------------------------------------------------------------------------- checks
    @staticmethod
    def is_entry(intent: OrderIntent) -> bool:
        return intent.kind in ENTRY_KINDS

    def entry_block_reason(self, view: SecondView | None, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        c = self.cfg
        if self.kill_present():
            return "kill_file"
        if self.disarm_present():
            return "disarmed"
        if self.realized_today(now) <= -abs(c.daily_loss_limit):
            return "daily_loss"
        news = self.news_block(now)
        if news:
            return news
        btc = view.btc if view is not None else None
        if btc is None or btc.stale or now - btc.ts > c.stale_btc_s:
            return "stale_btc"
        book = view.book
        if book is None:
            return "no_book"
        if now - book.ts > c.stale_book_s:
            return "stale_book"
        if btc.brti is not None and btc.composite is not None and abs(btc.brti - btc.composite) > c.max_brti_vs_composite:
            return "brti_divergence"
        if self.open_order_count() >= c.max_open_orders:
            return "max_open_orders"
        return ""

    def allow(self, intent: OrderIntent, view: SecondView | None) -> tuple[bool, str]:
        if not self.is_entry(intent):
            self.passed_exits += 1
            return True, ""
        why = self.entry_block_reason(view)
        if why:
            self.blocked[why] += 1
            self.last_reason = why
            return False, why
        self.allowed += 1
        return True, ""

    def stats(self) -> dict:
        return dict(allowed=self.allowed, passed_exits=self.passed_exits, blocked=dict(self.blocked),
                    blocked_total=sum(self.blocked.values()), last_reason=self.last_reason,
                    kill_file=self.kill_present())
