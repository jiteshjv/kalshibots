"""SQLite trade journal for kalshibot (stdlib sqlite3, WAL mode).

One file per bot at `<data_dir>/journal.sqlite`. Every public method is thread-safe (one connection + a lock), so the
asyncio loop, the dashboard server thread and the alert thread can all use the same Journal object.

Tables
  markets  ticker, open_ts, close_ts, strike, result ("YES"/"NO"/NULL while open), skipped (reason or NULL), range_2h
  lots     every field of types.Lot (+ ticker, moneyness/threshold at entry, created/updated ts)
  orders   client_id, ticker, lot_id, kind, side, qty, price, status, reason, ts, updated_ts, exchange_id
  fills    client_id, ticker, side, action, price, qty, fee, ts, maker
  events   ts, level, kind, message

Order statuses: "submitted" | "resting" | "partial" are OPEN; "filled" | "cancelled" | "expired" | "rejected" are done.

P&L conventions
  * realized_pnl(day) = sum of Lot.pnl() for lots whose exit_ts falls on that UTC day (stops, take-profits and
    settlements). Entry fees count when the lot closes. Open lots contribute nothing.
  * export_dashboard_json() writes the research sheet's shape: {"m": [market rows], "e": {ticker: [entry rows]},
    "meta": {...}, "ev": [recent events]}; display times are US/Pacific.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Iterable

from .types import Fill, Lot, MarketInfo, OrderIntent

try:
    from zoneinfo import ZoneInfo

    PT = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - no tz database on this machine
    PT = _dt.timezone(_dt.timedelta(hours=-7), "PT")

UTC = _dt.timezone.utc
OPEN_STATUSES = ("submitted", "resting", "partial")
DONE_STATUSES = ("filled", "cancelled", "expired", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets(
  ticker TEXT PRIMARY KEY, open_ts REAL, close_ts REAL, strike REAL, result TEXT, skipped TEXT, range_2h REAL,
  closed_ts REAL);
CREATE TABLE IF NOT EXISTS lots(
  lot_id TEXT PRIMARY KEY, ticker TEXT, side TEXT, qty INTEGER, buy REAL, fee_in REAL, stop REAL, entry_ts REAL,
  how TEXT, exit TEXT, exit_px REAL, exit_ts REAL, fee_out REAL DEFAULT 0, mny REAL, th REAL, updated_ts REAL);
CREATE INDEX IF NOT EXISTS lots_ticker ON lots(ticker);
CREATE INDEX IF NOT EXISTS lots_exit_ts ON lots(exit_ts);
CREATE TABLE IF NOT EXISTS orders(
  client_id TEXT PRIMARY KEY, ticker TEXT, lot_id TEXT, kind TEXT, side TEXT, qty INTEGER, price REAL,
  status TEXT, reason TEXT, ts REAL, updated_ts REAL, exchange_id TEXT, filled_qty INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, ticker TEXT, side TEXT, action TEXT, price REAL,
  qty INTEGER, fee REAL, ts REAL, maker INTEGER);
CREATE INDEX IF NOT EXISTS fills_client ON fills(client_id);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, kind TEXT, message TEXT);
"""

_LOT_FIELDS = [f.name for f in fields(Lot)]


def _utc_bounds(day: Any) -> tuple[float, float]:
    """[start, end) epoch seconds of a UTC calendar day. day: date | 'YYYY-MM-DD' | None (today)."""
    if day is None:
        d = _dt.datetime.now(UTC).date()
    elif isinstance(day, str):
        d = _dt.date.fromisoformat(day)
    elif isinstance(day, _dt.datetime):
        d = day.astimezone(UTC).date()
    else:
        d = day
    start = _dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()
    return start, start + 86400.0


def _pt(ts: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, PT)


def _mmss(sec: float) -> str:
    sec = max(int(round(sec)), 0)
    return f"{sec // 60}:{sec % 60:02d}"


def _clock_pt(ts: float) -> str:
    t = _pt(ts)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def _side_label(side: str) -> str:
    return "YES (up)" if side == "y" else "NO (down)"


def _exit_label(lot: dict) -> str:
    ex = lot.get("exit")
    if ex is None:
        return "open"
    if ex == "stop":
        return "stopped"
    if ex == "kill":
        return "kill switch"
    if ex == "tp":
        px = lot.get("exit_px")
        return f"sold {px * 100:g}c" if px is not None else "sold"
    if ex == "settle_win":
        return "settled $1"
    if ex == "settle_loss":
        return "settled $0"
    return str(ex)


def _lot_pnl(r: dict) -> float | None:
    if r.get("exit_px") is None:
        return None
    return r["qty"] * r["exit_px"] - (r.get("fee_out") or 0.0) - (r["qty"] * r["buy"] + (r.get("fee_in") or 0.0))


class Journal:
    def __init__(self, data_dir: str | os.PathLike, filename: str = "journal.sqlite"):
        self.data_dir = Path(os.path.expanduser(str(data_dir)))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / filename
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ low level
    def _exec(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, tuple(args))

    def _rows(self, sql: str, args: Iterable = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(args)).fetchall()]

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ markets
    def record_market_open(self, m: MarketInfo, range_2h: float | None = None, skipped: str | None = None) -> None:
        """Call once per market at its open (after Strategy.start_market). range_2h as a fraction (0.012 = 1.2%)."""
        self._exec(
            "INSERT INTO markets(ticker, open_ts, close_ts, strike, range_2h, skipped) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET open_ts=excluded.open_ts, close_ts=excluded.close_ts, "
            "strike=excluded.strike, range_2h=COALESCE(excluded.range_2h, markets.range_2h), "
            "skipped=excluded.skipped",                     # a restart re-decides the skip
            (m.ticker, m.open_ts, m.close_ts, m.strike, range_2h, skipped),
        )

    def mark_market_skipped(self, ticker: str, reason: str) -> None:
        """The strategy skipped the whole market (2h range or sweep filters)."""
        self._exec("UPDATE markets SET skipped=? WHERE ticker=?", (reason, ticker))

    def record_market_close(self, ticker: str, yes_won: bool | None, lots: Iterable[Lot] = ()) -> None:
        """Call at settlement with the result and the list returned by Strategy.end_market (lots are upserted)."""
        for lot in lots:
            self.record_lot(lot)
        res = None if yes_won is None else ("YES" if yes_won else "NO")
        self._exec("UPDATE markets SET result=?, closed_ts=? WHERE ticker=?", (res, time.time(), ticker))

    def unsettled_markets(self, before_ts: float) -> list[dict]:
        """Markets that closed before `before_ts` but have no result yet (e.g. the bot restarted around the close)."""
        return self._rows("SELECT * FROM markets WHERE result IS NULL AND close_ts < ? ORDER BY close_ts", [before_ts])

    def settle_orphans(self, ticker: str, yes_won: bool) -> int:
        """Settle lots of `ticker` still open in the journal (held across a restart): $1 if their side won, else $0."""
        n = 0
        for r in self.lots(ticker, open_only=True):
            win = (r["side"] == "y") == yes_won
            self.update_lot(r["lot_id"], exit="settle_win" if win else "settle_loss", exit_px=1.0 if win else 0.0,
                            exit_ts=time.time(), fee_out=0.0)
            n += 1
        return n

    # ------------------------------------------------------------------ lots
    def record_lot(self, lot: Lot, mny: float | None = None, th: float | None = None) -> None:
        """Insert or fully update a lot (all types.Lot fields). mny/th = moneyness and required threshold (fractions)."""
        d = asdict(lot)
        cols = _LOT_FIELDS + ["updated_ts"]
        vals = [d[k] for k in _LOT_FIELDS] + [time.time()]
        upd = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "lot_id")
        extra_cols, extra_vals, extra_upd = "", [], ""
        if mny is not None:
            extra_cols += ", mny"; extra_vals.append(mny); extra_upd += ", mny=excluded.mny"
        if th is not None:
            extra_cols += ", th"; extra_vals.append(th); extra_upd += ", th=excluded.th"
        q = ",".join("?" * (len(cols) + len(extra_vals)))
        self._exec(
            f"INSERT INTO lots({', '.join(cols)}{extra_cols}) VALUES({q}) ON CONFLICT(lot_id) DO UPDATE SET {upd}{extra_upd}",
            vals + extra_vals,
        )

    def update_lot(self, lot_id: str, **changes: Any) -> None:
        """Partial update, e.g. update_lot(id, exit="stop", exit_px=0.41, exit_ts=ts, fee_out=1.2)."""
        allowed = set(_LOT_FIELDS) | {"mny", "th"}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"unknown lot fields {sorted(bad)}")
        if not changes:
            return
        sets = ", ".join(f"{k}=?" for k in changes)
        self._exec(f"UPDATE lots SET {sets}, updated_ts=? WHERE lot_id=?", [*changes.values(), time.time(), lot_id])

    def delete_lot(self, lot_id: str) -> None:
        """Remove a lot that never existed (a provisional market entry whose order was rejected / not filled)."""
        self._exec("DELETE FROM lots WHERE lot_id=?", [lot_id])

    def lots(self, ticker: str | None = None, open_only: bool = False) -> list[dict]:
        sql, args = "SELECT * FROM lots WHERE 1=1", []
        if ticker:
            sql += " AND ticker=?"; args.append(ticker)
        if open_only:
            sql += " AND exit IS NULL"
        return self._rows(sql + " ORDER BY entry_ts", args)

    # ------------------------------------------------------------------ orders / fills
    def record_order(self, intent: OrderIntent, ticker: str, status: str = "submitted", ts: float | None = None,
                     exchange_id: str | None = None) -> None:
        """Call when an intent is approved and sent to the broker (or with status="rejected" when risk blocks it)."""
        ts = time.time() if ts is None else ts
        self._exec(
            "INSERT INTO orders(client_id, ticker, lot_id, kind, side, qty, price, status, reason, ts, updated_ts, exchange_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET status=excluded.status, "
            "updated_ts=excluded.updated_ts, exchange_id=COALESCE(excluded.exchange_id, orders.exchange_id)",
            (intent.client_id, ticker, intent.lot_id, intent.kind, intent.side, intent.qty, intent.price, status,
             intent.reason, ts, ts, exchange_id),
        )

    def update_order(self, client_id: str, status: str | None = None, reason: str | None = None,
                     exchange_id: str | None = None, filled_qty: int | None = None) -> None:
        sets, args = ["updated_ts=?"], [time.time()]
        for k, v in (("status", status), ("exchange_id", exchange_id), ("filled_qty", filled_qty)):
            if v is not None:
                sets.append(f"{k}=?"); args.append(v)
        if reason is not None:
            sets.append("reason=CASE WHEN reason IS NULL OR reason='' THEN ? ELSE reason || '; ' || ? END")
            args += [reason, reason]
        self._exec(f"UPDATE orders SET {', '.join(sets)} WHERE client_id=?", args + [client_id])

    def open_orders(self, ticker: str | None = None) -> list[dict]:
        """Orders not yet filled/cancelled/expired/rejected (dicts with the orders-table columns)."""
        sql = f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(OPEN_STATUSES))})"
        args: list = list(OPEN_STATUSES)
        if ticker:
            sql += " AND ticker=?"; args.append(ticker)
        return self._rows(sql + " ORDER BY ts", args)

    def record_fill(self, f: Fill, ticker: str | None = None) -> None:
        """Every Fill the broker reports. Also bumps orders.filled_qty and marks the order filled when complete."""
        with self._lock:
            if ticker is None:
                r = self._db.execute("SELECT ticker FROM orders WHERE client_id=?", (f.client_id,)).fetchone()
                ticker = r["ticker"] if r else None
            self._db.execute(
                "INSERT INTO fills(client_id, ticker, side, action, price, qty, fee, ts, maker) VALUES(?,?,?,?,?,?,?,?,?)",
                (f.client_id, ticker, f.side, f.action, f.price, f.qty, f.fee, f.ts, int(f.maker)),
            )
            self._db.execute(
                "UPDATE orders SET filled_qty=COALESCE(filled_qty,0)+?, updated_ts=?, "
                "status=CASE WHEN COALESCE(filled_qty,0)+? >= qty THEN 'filled' ELSE 'partial' END "
                "WHERE client_id=? AND status NOT IN ('cancelled','expired','rejected')",
                (f.qty, time.time(), f.qty, f.client_id),
            )

    def fills(self, since_ts: float = 0.0) -> list[dict]:
        return self._rows("SELECT * FROM fills WHERE ts>=? ORDER BY ts", (since_ts,))

    # ------------------------------------------------------------------ events
    def event(self, level: str, kind: str, msg: str, ts: float | None = None) -> None:
        """level: "info" | "warn" | "error"; kind: short tag ("start", "stop_out", "risk_block", "kill", ...)."""
        self._exec("INSERT INTO events(ts, level, kind, message) VALUES(?,?,?,?)",
                   (time.time() if ts is None else ts, level, kind, str(msg)[:4000]))

    def events(self, limit: int = 50, min_level: str | None = None) -> list[dict]:
        if min_level in ("warn", "error"):
            lv = ("warn", "error") if min_level == "warn" else ("error",)
            return self._rows(f"SELECT * FROM events WHERE level IN ({','.join('?' * len(lv))}) ORDER BY id DESC LIMIT ?",
                              [*lv, limit])
        return self._rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ P&L
    def realized_pnl(self, utc_date: Any = None) -> float:
        """Realized P&L ($, after fees) of lots closed during the UTC day (date | 'YYYY-MM-DD' | None = today)."""
        a, b = _utc_bounds(utc_date)
        rows = self._rows("SELECT qty, buy, fee_in, exit_px, fee_out FROM lots WHERE exit_ts>=? AND exit_ts<? "
                          "AND exit_px IS NOT NULL", (a, b))
        return round(sum(_lot_pnl(r) for r in rows), 6)

    def daily_summary(self, date: Any = None) -> dict:
        """Totals for lots closed on that UTC day: P&L, fees, entries, contracts, exits by kind, markets, win/loss."""
        a, b = _utc_bounds(date)
        lots = self._rows("SELECT * FROM lots WHERE exit_ts>=? AND exit_ts<? AND exit_px IS NOT NULL", (a, b))
        by_mkt: dict[str, float] = {}
        for r in lots:
            by_mkt[r["ticker"]] = by_mkt.get(r["ticker"], 0.0) + _lot_pnl(r)
        skipped = self._rows("SELECT COUNT(*) n FROM markets WHERE skipped IS NOT NULL AND open_ts>=? AND open_ts<?",
                             (a, b))[0]["n"]
        pnls = list(by_mkt.values())
        day = _dt.datetime.fromtimestamp(a, UTC).date().isoformat()
        return {
            "date": day,
            "pnl": round(sum(pnls), 2),
            "fees": round(sum((r["fee_in"] or 0) + (r["fee_out"] or 0) for r in lots), 2),
            "markets": len(by_mkt),
            "markets_won": sum(p > 0 for p in pnls),
            "markets_lost": sum(p < 0 for p in pnls),
            "markets_skipped": skipped,
            "entries": len(lots),
            "contracts": sum(r["qty"] for r in lots),
            "stopped": sum(r["exit"] == "stop" for r in lots),
            "take_profit": sum(r["exit"] == "tp" for r in lots),
            "settled": sum((r["exit"] or "").startswith("settle") for r in lots),
            "best_market": round(max(pnls), 2) if pnls else 0.0,
            "worst_market": round(min(pnls), 2) if pnls else 0.0,
            "open_lots": self._rows("SELECT COUNT(*) n FROM lots WHERE exit IS NULL")[0]["n"],
        }

    # ------------------------------------------------------------------ dashboard export
    def dashboard_data(self, now: float | None = None, meta: dict | None = None, n_events: int = 50) -> dict:
        now = time.time() if now is None else now
        markets = self._rows("SELECT * FROM markets ORDER BY open_ts")
        all_lots = self._rows("SELECT * FROM lots ORDER BY entry_ts, lot_id")
        by_t: dict[str, list[dict]] = {}
        for r in all_lots:
            by_t.setdefault(r["ticker"], []).append(r)
        M, E = [], {}
        for mk in markets:
            t = mk["ticker"]
            ml = by_t.get(t, [])
            if not ml and not mk["skipped"]:
                continue  # watched but never traded
            open_ts = mk["open_ts"] or (ml[0]["entry_ts"] if ml else now)
            close_ts = mk["close_ts"] or open_ts + 900
            pt = _pt(open_ts)
            row = {
                "day": f"{pt:%a} {pt:%b} {pt.day}",
                "window_pt": _clock_pt(open_ts),
                "ticker": t,
                "range2h": None if mk["range_2h"] is None else round(mk["range_2h"] * 100, 3),
                "strike": mk["strike"],
                "result": mk["result"],
                "d": pt.date().isoformat(),
            }
            if not ml:  # skipped
                row.update(side=f"skipped: {mk['skipped']}", entries=0, contracts=0, avg_buy=None, first_entry=None,
                           stopped=0, sold995=0, settled=0, pnl=0.0, skip=1, cap=0.0, held_s=0.0, open=0)
                M.append(row)
                continue
            n = sum(r["qty"] for r in ml)
            pnls = [_lot_pnl(r) for r in ml]
            # capital: peak cost of entries open at once; held: first entry to last exit
            ev = []
            for r in ml:
                cost = r["qty"] * r["buy"] + (r["fee_in"] or 0)
                ev.append((r["entry_ts"], 1, cost))
                ev.append((r["exit_ts"] if r["exit_ts"] is not None else max(min(now, close_ts), r["entry_ts"]), 0, -cost))
            run = cap = 0.0
            for _, _, c in sorted(ev):
                run += c
                cap = max(cap, run)
            first = min(r["entry_ts"] for r in ml)
            last = max(e[0] for e in ev)
            row.update(
                side=" then ".join(dict.fromkeys(_side_label(r["side"]) for r in ml)),
                entries=len(ml),
                contracts=n,
                avg_buy=round(sum(r["buy"] * r["qty"] for r in ml) / n, 4) if n else None,
                first_entry=_mmss(first - open_ts),
                stopped=sum(r["exit"] == "stop" for r in ml),
                sold995=sum(r["exit"] == "tp" for r in ml),
                settled=sum((r["exit"] or "").startswith("settle") for r in ml),
                pnl=round(sum(p for p in pnls if p is not None), 2),
                skip=1 if mk["skipped"] else 0,
                cap=round(cap, 2),
                held_s=round(last - first, 1),
                open=sum(r["exit"] is None for r in ml),
            )
            if row["skip"]:
                row["skip"] = 0  # it traded; keep the reason for reference only
                row["note"] = mk["skipped"]
            M.append(row)
            E[t] = [
                [i + 1, _mmss(r["entry_ts"] - open_ts), "Y" if r["side"] == "y" else "N", r["qty"],
                 None if r["mny"] is None else round(r["mny"] * 100, 3),
                 None if r["th"] is None else round(r["th"] * 100, 3),
                 r["buy"], r["stop"], _exit_label(r),
                 None if r["exit_ts"] is None else _mmss(r["exit_ts"] - open_ts),
                 r["exit_px"], None if p is None else round(p, 2)]
                for i, (r, p) in enumerate(zip(ml, pnls))
            ]
        today = self.realized_pnl(_dt.datetime.fromtimestamp(now, UTC).date())
        out_meta = {
            "generated_ts": now,
            "generated_pt": f"{_pt(now):%a %b %d %I:%M:%S %p} PT",
            "realized_today_utc": round(today, 2),
            "open_lots": sum(r["exit"] is None for r in all_lots),
            "open_orders": len(self.open_orders()),
        }
        out_meta.update(meta or {})
        ev = [
            {"ts": e["ts"], "pt": f"{_pt(e['ts']):%b %d %I:%M:%S %p}", "level": e["level"], "kind": e["kind"],
             "message": e["message"]}
            for e in self.events(n_events)
        ]
        return {"m": M, "e": E, "meta": out_meta, "ev": ev}

    def export_dashboard_json(self, path: str | os.PathLike | None = None, meta: dict | None = None) -> Path:
        """Write the dashboard JSON atomically (default `<data_dir>/dashboard.json`). meta: extra status fields
        (e.g. {"mode": "paper", "kill": False, "btc": 67000.1}) shown in the page header."""
        path = Path(path) if path else self.data_dir / "dashboard.json"
        data = self.dashboard_data(meta=meta)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":"), default=str))
        os.replace(tmp, path)
        return path
