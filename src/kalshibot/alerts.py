"""Telegram alerts for kalshibot (HTTPS Bot API via `requests`).

* Token and chat id come from environment variables whose NAMES are in IoCfg (default TELEGRAM_BOT_TOKEN /
  TELEGRAM_CHAT_ID). If either is unset every call is a silent no-op.
* Non-blocking: `send()` only puts the text on a queue; a daemon thread batches everything queued within
  `batch_s` seconds into one message and sends at most one message per `min_interval_s` (Telegram allows ~1/s per
  chat) and at most `max_per_min` per minute; overflow is merged into the next message, never lost silently
  (if the queue grows past `max_queue` the oldest lines are dropped and a "(N lines dropped)" note is added).
  Safe to call from asyncio code, threads, or signal handlers.
* Repeated identical errors are collapsed (one message per `dedupe_s` per key, with a repeat count).

Heartbeat / bot-down: the bot calls `write_heartbeat(data_dir)` every few seconds. A separate watchdog process
(`python -m kalshibot.alerts watch --data-dir /data`, the `watchdog` service in docker-compose.yml) sends
"bot down" when the heartbeat is older than `--max-age` seconds and "bot back up" when it resumes.
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("kalshibot.alerts")

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000  # Telegram limit is 4096 chars


def _default_post(url: str, data: dict, timeout: float) -> Any:
    import requests

    return requests.post(url, data=data, timeout=timeout)


class Alerter:
    def __init__(self, token: str | None = None, chat_id: str | None = None, *, prefix: str = "kalshibot",
                 batch_s: float = 2.0, min_interval_s: float = 1.1, max_per_min: int = 20, dedupe_s: float = 600.0,
                 max_queue: int = 500, post: Callable[[str, dict, float], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.token = token or None
        self.chat_id = chat_id or None
        self.prefix = prefix
        self.batch_s, self.min_interval_s, self.max_per_min = batch_s, min_interval_s, max_per_min
        self.dedupe_s, self.max_queue = dedupe_s, max_queue
        self._post = post or _default_post
        self._clock = clock
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._sent: deque[float] = deque()
        self._dedupe: dict[str, list[float]] = {}  # key -> [first_ts, count_suppressed]
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._outstanding = 0  # lines queued but not yet delivered/dropped
        self._closed = False
        self.sent_count = 0
        self.failed_count = 0

    @classmethod
    def from_env(cls, token_env: str = "TELEGRAM_BOT_TOKEN", chat_env: str = "TELEGRAM_CHAT_ID", **kw) -> "Alerter":
        """Build from the env var names in IoCfg: Alerter.from_env(cfg.io.telegram_token_env, cfg.io.telegram_chat_env)."""
        return cls(os.environ.get(token_env), os.environ.get(chat_env), **kw)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # ------------------------------------------------------------------ core
    def send(self, text: str, dedupe_key: str | None = None) -> None:
        """Queue a line. Never blocks, never raises."""
        if not self.enabled or self._closed:
            return
        try:
            if dedupe_key is not None:
                now = self._clock()
                with self._lock:
                    st = self._dedupe.get(dedupe_key)
                    if st and now - st[0] < self.dedupe_s:
                        st[1] += 1
                        return
                    if st and st[1]:
                        text = f"{text} (repeated {int(st[1])}x in the last {int(self.dedupe_s / 60)} min)"
                    self._dedupe[dedupe_key] = [now, 0]
            self._ensure_thread()
            with self._cv:
                self._outstanding += 1
            self._q.put(str(text))
        except Exception:  # pragma: no cover
            log.exception("alert enqueue failed")

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="kalshibot-alerts", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        pending: list[str] = []
        dropped = 0
        stop = False
        while not stop:
            if not pending:
                item = self._q.get()
                if item is None:
                    break
                pending.append(item)
            # gather the batch window
            deadline = self._clock() + self.batch_s
            while True:
                left = deadline - self._clock()
                try:
                    item = self._q.get(timeout=max(left, 0.0)) if left > 0 else self._q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    stop = True
                    break
                pending.append(item)
            if len(pending) > self.max_queue:
                k = len(pending) - self.max_queue
                dropped += k
                pending = pending[-self.max_queue:]
                self._done(k)
            # rate limit
            wait = self._wait_needed()
            if wait > 0 and not stop:
                time.sleep(min(wait, 5.0))
                continue  # keep collecting; pending is merged next round
            n0 = len(pending)
            body, pending = self._take(pending, dropped)
            dropped = 0
            self._deliver(body)
            self._done(n0 - len(pending))
        if pending:  # flush on close (best effort, ignores rate limits)
            while pending:
                n0 = len(pending)
                body, pending = self._take(pending, dropped)
                dropped = 0
                self._deliver(body)
                self._done(n0 - len(pending))

    def _done(self, n: int) -> None:
        with self._cv:
            self._outstanding = max(self._outstanding - n, 0)
            self._cv.notify_all()

    def _wait_needed(self) -> float:
        now = self._clock()
        while self._sent and now - self._sent[0] > 60:
            self._sent.popleft()
        w = 0.0
        if self._sent:
            w = max(w, self.min_interval_s - (now - self._sent[-1]))
        if len(self._sent) >= self.max_per_min:
            w = max(w, 60 - (now - self._sent[0]))
        return w

    def _take(self, pending: list[str], dropped: int) -> tuple[str, list[str]]:
        head = f"[{self.prefix}]" if self.prefix else ""
        lines = ([head] if head else []) + ([f"({dropped} lines dropped)"] if dropped else [])
        size = sum(len(x) + 1 for x in lines)
        i = 0
        while i < len(pending):
            ln = pending[i]
            if len(ln) > MAX_LEN - 200:
                ln = ln[: MAX_LEN - 203] + "..."
            if size + len(ln) + 1 > MAX_LEN and i > 0:
                break
            lines.append(ln)
            size += len(ln) + 1
            i += 1
        return "\n".join(lines), pending[i:]

    def _deliver(self, body: str) -> None:
        self._sent.append(self._clock())
        try:
            r = self._post(API.format(token=self.token), {"chat_id": self.chat_id, "text": body,
                                                          "disable_web_page_preview": "true"}, 10.0)
            code = getattr(r, "status_code", 200)
            if code != 200:
                self.failed_count += 1
                log.warning("telegram send failed: HTTP %s", code)
            else:
                self.sent_count += 1
        except Exception as e:
            self.failed_count += 1
            log.warning("telegram send failed: %s", type(e).__name__)  # never log the URL (contains the token)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything queued so far has been sent (for tests and shutdown)."""
        if not self.enabled or self._thread is None:
            return True
        with self._cv:
            return self._cv.wait_for(lambda: self._outstanding == 0, timeout)

    def close(self, timeout: float = 10.0) -> None:
        """Send whatever is queued, then stop the worker."""
        if self._thread is not None and self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout)
        self._closed = True

    # ------------------------------------------------------------------ message helpers
    def started(self, mode: str, detail: str = "") -> None:
        self.send(f"started in {mode.upper()} mode" + (f" - {detail}" if detail else ""))

    def stopped(self, reason: str = "shutdown") -> None:
        self.send(f"stopped: {reason}")

    def stop_out(self, ticker: str, side: str, qty: int, buy: float, exit_px: float, pnl: float | None = None) -> None:
        sd = "YES" if side == "y" else "NO"
        p = "" if pnl is None else f", P&L {_usd(pnl)}"
        self.send(f"STOP {ticker} {sd} {qty} @ {buy * 100:.1f}c -> {exit_px * 100:.1f}c{p}")

    def stop_out_lot(self, lot: Any) -> None:
        """Convenience for a closed types.Lot."""
        self.stop_out(lot.ticker, lot.side, lot.qty, lot.buy, lot.exit_px or 0.0, lot.pnl())

    def market_result(self, ticker: str, pnl: float, entries: int, stopped: int) -> None:
        self.send(f"{ticker}: {entries} entries, {stopped} stopped, P&L {_usd(pnl)}")

    def daily_summary(self, s: dict) -> None:
        """s = Journal.daily_summary(day)."""
        self.send(
            f"Daily summary {s.get('date', '')} (UTC): P&L {_usd(s.get('pnl', 0))} after {_usd(s.get('fees', 0), sign=False)} fees\n"
            f"markets {s.get('markets', 0)} (won {s.get('markets_won', 0)}, lost {s.get('markets_lost', 0)}, "
            f"skipped {s.get('markets_skipped', 0)}); entries {s.get('entries', 0)}, contracts {s.get('contracts', 0):,}\n"
            f"exits: {s.get('take_profit', 0)} sold at TP, {s.get('stopped', 0)} stopped, {s.get('settled', 0)} settled; "
            f"best {_usd(s.get('best_market', 0))}, worst {_usd(s.get('worst_market', 0))}"
        )

    def risk_block(self, reason: str) -> None:
        self.send(f"entries paused: {reason}", dedupe_key=f"risk:{reason}")

    def daily_loss_limit(self, pnl: float, limit: float) -> None:
        self.send(f"DAILY LOSS LIMIT hit: {_usd(pnl)} (limit -${limit:,.0f}). No new entries until 00:00 UTC.",
                  dedupe_key="daily_loss_limit")

    def kill_switch(self, source: str = "kill file") -> None:
        self.send(f"KILL SWITCH ({source}): cancelling all orders, selling every position at market, then stopping", dedupe_key="kill")

    def error(self, where: str, exc: BaseException | str) -> None:
        msg = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        self.send(f"ERROR in {where}: {msg}", dedupe_key=f"err:{where}:{str(msg)[:80]}")

    def heartbeat_missed(self, age_s: float) -> None:
        self.send(f"BOT DOWN? no heartbeat for {age_s:.0f} s", dedupe_key="heartbeat")

    def bot_down(self, reason: str) -> None:
        self.send(f"BOT DOWN: {reason}", dedupe_key="bot_down")

    def bot_up(self) -> None:
        self.send("bot is running again")


def _usd(v: float, sign: bool = True) -> str:
    v = float(v or 0)
    s = ("-" if v < 0 else "+") if sign else ""
    return f"{s}${abs(v):,.2f}"


# ---------------------------------------------------------------------- heartbeat + watchdog
HEARTBEAT_FILE = "heartbeat"


def write_heartbeat(data_dir: str | os.PathLike, ts: float | None = None) -> None:
    """Call every ~5 s from the bot's main loop."""
    p = Path(os.path.expanduser(str(data_dir))) / HEARTBEAT_FILE
    tmp = p.with_name(HEARTBEAT_FILE + ".tmp")
    tmp.write_text(f"{time.time() if ts is None else ts:.3f}\n")
    os.replace(tmp, p)


def heartbeat_age(data_dir: str | os.PathLike, now: float | None = None) -> float | None:
    p = Path(os.path.expanduser(str(data_dir))) / HEARTBEAT_FILE
    try:
        ts = float(p.read_text().strip())
    except (OSError, ValueError):
        return None
    return (time.time() if now is None else now) - ts


class Watchdog:
    """Checks the heartbeat file; alerts once when it goes stale and once when it recovers."""

    def __init__(self, data_dir: str | os.PathLike, alerter: Alerter, max_age_s: float = 60.0):
        self.data_dir, self.alerter, self.max_age_s = data_dir, alerter, max_age_s
        self.down = False

    def check(self, now: float | None = None) -> bool:
        """Returns True when the bot looks healthy."""
        age = heartbeat_age(self.data_dir, now)
        ok = age is not None and age <= self.max_age_s
        if not ok and not self.down:
            self.down = True
            if age is None:
                self.alerter.bot_down("no heartbeat file yet (bot never started?)")
            else:
                self.alerter.heartbeat_missed(age)
        elif ok and self.down:
            self.down = False
            self.alerter.bot_up()
        return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.alerts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="watch the bot's heartbeat and alert on Telegram when it stops")
    w.add_argument("--data-dir", default=os.environ.get("KALSHIBOT_DATA_DIR", "/data"))
    w.add_argument("--max-age", type=float, default=90.0)
    w.add_argument("--every", type=float, default=15.0)
    w.add_argument("--grace", type=float, default=120.0, help="seconds to wait at startup before alerting")
    t = sub.add_parser("test", help="send a test message")
    for p in (w, t):
        p.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
        p.add_argument("--chat-env", default="TELEGRAM_CHAT_ID")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    al = Alerter.from_env(a.token_env, a.chat_env, prefix="kalshibot watchdog" if a.cmd == "watch" else "kalshibot")
    if not al.enabled:
        print(f"Telegram not configured ({a.token_env}/{a.chat_env} unset).")
        if a.cmd == "test":
            return 1
    if a.cmd == "test":
        al.send("test message: alerts are working")
        al.close()
        print("sent" if al.sent_count else "FAILED - check the token and chat id")
        return 0 if al.sent_count else 1
    time.sleep(a.grace)
    wd = Watchdog(a.data_dir, al, a.max_age)
    while True:
        wd.check()
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
