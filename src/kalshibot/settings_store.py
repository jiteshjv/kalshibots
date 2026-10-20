"""Settings you can change from the dashboard (Settings page). Saved in <data_dir>/settings.json, applied on top of
config.yaml at start and whenever they are saved. Strategy settings take effect from the NEXT market (the current
market keeps the rules it started with); risk settings take effect at once. Never editable here: paper/live mode, the
live confirmation, the market series and folders.
"""
from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

# (section, name, type, min, max, label, help)
SPEC = [
    ("strategy", "sizing", "choice:equal,mixed", None, None, "Sizing", "equal = same size every entry; mixed = early size before minute 6"),
    ("strategy", "size_late", "int", 1, 10000, "Contracts per entry", "contracts bought at each entry (mixed: after minute 6)"),
    ("strategy", "size_early", "int", 1, 10000, "Early contracts per entry (mixed)", "contracts per entry before minute 6 (mixed sizing only)"),
    ("strategy", "max_held", "int", 1, 100000, "Max contracts held", "per market"),
    ("strategy", "check_every_s", "int", 1, 120, "Seconds between entry checks", "e.g. 10 = every 10 seconds"),
    ("strategy", "first_check_s", "int", 0, 899, "First entry check (seconds)", "120 = minute 2"),
    ("strategy", "last_entry_s", "int", 0, 899, "Last entry check (seconds)", "720 = minute 12"),
    ("strategy", "mny_start", "pct", 0, 2, "Moneyness at the first check (%)", "how far past the strike the price must be at the first check"),
    ("strategy", "mny_floor", "pct", 0, 2, "Moneyness at the end (%)", "falls in a straight line to this by the end time"),
    ("strategy", "mny_end_s", "int", 1, 900, "Moneyness end (seconds)", "900 = minute 15"),
    ("strategy", "max_price", "cents", 1, 99.9, "Max buy price (c)", "no entry at or above this ask"),
    ("strategy", "max_spread", "cents", 0, 50, "Max spread (c)", "no entry when the spread is wider"),
    ("strategy", "limit_offset", "cents", 0, 10, "Entry bid below the ask (c)", "0 = buy at market"),
    ("strategy", "limit_fallback", "bool", None, None, "Buy at market if the bid does not fill", ""),
    ("strategy", "stop_pct", "pct", 1, 100, "Stop: % below the buy price", "e.g. 25"),
    ("strategy", "stop_lead", "pct", 0, 1, "Stop: price within this % of the strike", "e.g. 0.03"),
    ("strategy", "stop_recheck", "bool", None, None, "Stop: re-check the bid before selling", ""),
    ("strategy", "take_profit", "cents", 50, 100, "Take-profit sell (c)", "resting sell price, e.g. 99.7"),
    ("strategy", "max_range_2h", "pct", 0, 50, "Skip if the 2-hour range is above (%)", ""),
    ("strategy", "jump_pause", "pct", 0, 10, "Jump pause: 1-minute move above (%)", "0 = off"),
    ("strategy", "filters", "bool", None, None, "Sweep filters on", ""),
    ("strategy", "sweep_days", "float", 0, 30, "Sweep: shortest level (days)", "restart the bot after raising the sweep days"),
    ("strategy", "sweep_max_days", "float", 0, 60, "Sweep: ignore levels at least this old (days, 0 = none)", ""),
    ("strategy", "sweep_4h_m60", "pct", 0, 100, "Sweep: 4h + stretched 1h move (%, 0 = off)", ""),
    ("strategy", "skip_hours_pt", "str", None, None, "Skip markets starting (PT hours)", 'e.g. "7-9" or "7-9,13-14"; empty = none'),
    ("risk", "daily_loss_limit", "float", 0, 1e7, "Daily loss limit ($)", "no new entries for the rest of the UTC day after this loss"),
    ("risk", "news_mode", "choice:off,window,day", None, None, "News days", "window = no entries around releases; day = none all day"),
    ("risk", "news_events", "str", None, None, "News events", "jobs,cpi,fomc,ppi,claims,gdp_pce,retail"),
    ("risk", "news_before_min", "int", 0, 600, "Minutes before a release", ""),
    ("risk", "news_after_min", "int", 0, 600, "Minutes after a release", ""),
    ("risk", "news_windows", "str", None, None, "Per-event windows", 'event:before:after,... e.g. "fomc:0:60" (overrides the three above)'),
]
_BY = {(s, n): (t, lo, hi) for s, n, t, lo, hi, _, _ in SPEC}
_SCALE = {"pct": 100.0, "cents": 100.0}              # shown as % / cents, stored as fractions


def path(data_dir: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(str(data_dir))) / "settings.json"


def load(data_dir) -> dict:
    try:
        d = json.loads(path(data_dir).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def to_display(settings) -> dict:
    """{"section.name": value in display units} for every SPEC field."""
    out = {}
    for s, n, t, *_ in SPEC:
        v = getattr(getattr(settings, s), n)
        k = t.split(":")[0]
        out[f"{s}.{n}"] = round(v * _SCALE[k], 6) if k in _SCALE and v is not None else v
    return out


def validate(raw: dict) -> tuple[dict, list[str]]:
    """Display-unit input {"section.name": value} -> ({"section.name": stored value}, errors)."""
    clean, errs = {}, []
    for key, val in (raw or {}).items():
        sec, _, name = str(key).partition(".")
        spec = _BY.get((sec, name))
        if spec is None:
            errs.append(f"{key}: not a setting you can change here")
            continue
        t, lo, hi = spec
        k = t.split(":")[0]
        try:
            if k == "bool":
                v = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
            elif k == "choice":
                v = str(val).strip()
                if v not in t.split(":")[1].split(","):
                    raise ValueError(f"one of {t.split(':')[1]}")
            elif k == "str":
                v = str(val).strip()
            else:
                v = float(val)
                if v != v:
                    raise ValueError("not a number")
                if lo is not None and not (lo <= v <= hi):
                    raise ValueError(f"between {lo:g} and {hi:g}")
                if k == "int":
                    if v != int(v):
                        raise ValueError("whole number")
                    v = int(v)
                elif k in _SCALE:
                    v = round(v / _SCALE[k], 8)
        except (TypeError, ValueError) as e:
            errs.append(f"{key}: {e}")
            continue
        clean[f"{sec}.{name}"] = v
    return clean, errs


def apply(settings, overrides: dict) -> list[str]:
    """Set overrides on settings (in place). Returns the keys applied."""
    done = []
    for key, v in overrides.items():
        sec, _, name = key.partition(".")
        if (sec, name) in _BY and hasattr(getattr(settings, sec), name):
            setattr(getattr(settings, sec), name, v)
            done.append(key)
    return done


def save(data_dir, overrides: dict) -> None:
    p = path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, indent=1, sort_keys=True))
    tmp.replace(p)


def spec_json() -> list[dict]:
    return [dict(key=f"{s}.{n}", type=t, min=lo, max=hi, label=lab, help=h) for s, n, t, lo, hi, lab, h in SPEC]
