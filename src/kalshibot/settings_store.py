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

# (section, name, type, min, max, label, help). Order = order on the page (within its group, see GROUPS).
SPEC = [
    ("strategy", "sizing", "choice:equal,mixed", None, None, "Sizing", "equal = the same number of contracts every entry; mixed = a bigger size before minute 6"),
    ("strategy", "size_late", "int", 1, 10000, "Contracts per entry", "bought at every entry (mixed sizing: after minute 6)"),
    ("strategy", "size_early", "int", 1, 10000, "Contracts per entry before minute 6", "mixed sizing only"),
    ("strategy", "max_held", "int", 1, 100000, "Max contracts held", "in one market at once; no more entries above this"),
    ("strategy", "first_check_s", "int", 0, 899, "First entry check (seconds into the market)", "120 = minute 2"),
    ("strategy", "last_entry_s", "int", 0, 899, "Last entry check (seconds into the market)", "720 = minute 12; open positions are still managed after this"),
    ("strategy", "check_every_s", "int", 1, 120, "Seconds between entry checks", "10 = one possible entry every 10 seconds"),
    ("strategy", "risk_max", "pct", 0, 50, "Max risk (%)", "her risk check: buy only if the chance BTC ends on the wrong side of the strike is at most this, from how far BTC is from the strike, how jumpy it is and the time left. 15 = 15%. 0 = off (use the moneyness line instead)"),
    ("strategy", "mny_start", "pct", 0, 2, "Moneyness line: distance at the first check (%)", "how far past the strike BTC must be at the first check; 0 = no line"),
    ("strategy", "mny_floor", "pct", 0, 2, "Moneyness line: distance at the end (%)", "the line falls straight down to this by the end time"),
    ("strategy", "mny_end_s", "int", 1, 900, "Moneyness line: end time (seconds)", "900 = minute 15"),
    ("strategy", "max_price", "cents", 1, 99.9, "Max buy price (¢)", "no entry when the ask is at or above this"),
    ("strategy", "max_spread", "cents", 0, 50, "Max spread (¢)", "no entry when bid and ask are further apart than this"),
    ("strategy", "limit_offset", "cents", 0, 10, "Entry bid below the ask (¢)", "e.g. 0.3. Kalshi allows only whole cents between 10¢ and 90¢, so there the bid is 1¢ under. 0 = always buy at market"),
    ("strategy", "entry_wait_s", "int", 0, 60, "Bid wait before buying at market (seconds)", "e.g. 5. 0 = wait until the next entry check"),
    ("strategy", "limit_fallback", "bool", None, None, "Buy at market if the bid does not fill", "off = skip that entry instead"),
    ("strategy", "stop_pct", "pct", 1, 100, "Stop: price this far below the buy price (%)", "e.g. 25. Both stop conditions must hold"),
    ("strategy", "stop_lead", "pct", 0, 1, "Stop: and BTC within this distance of the strike (%)", "e.g. 0.03"),
    ("strategy", "stop_fair", "bool", None, None, "Stop: also on fair value", "also stop when BTC's fair value for our side is below the stop price, before Kalshi's bid catches up"),
    ("strategy", "stop_recheck", "bool", None, None, "Stop: re-check the bid before selling", "sell only if our side's bid is still at or below the stop price"),
    ("strategy", "fast_stop_ms", "int", 0, 1000, "Stop: check this often between seconds (ms)", "e.g. 100: a stop sells within ~0.1 s of the trade instead of at the next second. 0 = once a second"),
    ("strategy", "take_profit", "cents", 50, 100, "Take-profit sell (¢)", "a resting sell for every entry, e.g. 99.7; otherwise it settles at $1 / $0"),
    ("strategy", "max_range_2h", "pct", 0, 50, "Skip if BTC's 2-hour range is above (%)", "checked when the market opens"),
    ("strategy", "jump_pause", "pct", 0, 10, "Jump pause: no entries for 15 min after a 1-minute move above (%)", "e.g. 0.5. 0 = off"),
    ("strategy", "skip_hours_pt", "str", None, None, "Skip markets starting at these hours (PT)", 'e.g. "7-9" or "7-9,13-14"; empty = none'),
    ("strategy", "filters", "bool", None, None, "Sweep filters", "skip the market when BTC just ran through an earlier high or low on our side (checked at its first entry)"),
    ("strategy", "sweep_days", "float", 0, 30, "Sweep: shortest high/low (days)", "e.g. 3 = only highs/lows at least 3 days old count. Restart the bot after raising it"),
    ("strategy", "sweep_max_days", "float", 0, 60, "Sweep: ignore highs/lows at least this old (days)", "e.g. 30 (with 3 above: the 3- to 14-day rule). 0 = no limit"),
    ("strategy", "sweep_4h_m60", "pct", 0, 100, "Sweep: 4-hour high/low + 1-hour move above (%)", "an extra sweep rule; 0 = off (it lost money out of sample)"),
    ("risk", "daily_loss_limit", "float", 0, 1e7, "Daily loss limit ($)", "after losing this much in one UTC day (it starts 5 PM PT, 4 PM in winter), no new entries until the next day"),
    ("risk", "news_mode", "choice:off,window,day", None, None, "News releases", "window = no entries around each release; day = no entries all day; off = trade through them"),
    ("risk", "news_events", "str", None, None, "Which releases", "jobs, cpi, fomc, ppi, claims, gdp_pce, retail (comma-separated)"),
    ("risk", "news_before_min", "int", 0, 600, "Minutes before a release", "no new entries from this many minutes before"),
    ("risk", "news_after_min", "int", 0, 600, "Minutes after a release", "... until this many minutes after"),
    ("risk", "news_windows", "str", None, None, "Per-release windows", 'event:before:after,... e.g. "fomc:0:60" (overrides the two above for that release)'),
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


# Page sections: (title, one-line explanation, setting names). Every SPEC setting is in exactly one group.
GROUPS = [
    ("Size", "How many contracts each entry buys.", ["sizing", "size_late", "size_early", "max_held"]),
    ("Entry timing", "When in the 15-minute market the bot may open entries.", ["first_check_s", "last_entry_s", "check_every_s"]),
    ("Entry rule", "Which side to buy and when it is safe enough to enter.", ["risk_max", "mny_start", "mny_floor", "mny_end_s"]),
    ("Buying", "How each entry is bought: a bid under the ask (low fee), then market if it does not fill.",
     ["max_price", "max_spread", "limit_offset", "entry_wait_s", "limit_fallback"]),
    ("Stops and selling", "When an entry is sold early, and the resting sell near $1.",
     ["stop_pct", "stop_lead", "stop_fair", "stop_recheck", "fast_stop_ms", "take_profit"]),
    ("Skip markets", "Conditions that skip a market or pause new entries.",
     ["max_range_2h", "jump_pause", "skip_hours_pt", "filters", "sweep_days", "sweep_max_days", "sweep_4h_m60"]),
    ("Loss limit", "A stop for the whole day.", ["daily_loss_limit"]),
    ("News releases", "No new entries around big US data releases.",
     ["news_mode", "news_events", "news_before_min", "news_after_min", "news_windows"]),
]
# A setting is "not in use" (shown folded away) while its condition is false: key -> (other key, op, value)
SHOW_IF = {
    "size_early": ("strategy.sizing", "==", "mixed"),
    "mny_start": ("strategy.risk_max", "==", 0), "mny_floor": ("strategy.risk_max", "==", 0),
    "mny_end_s": ("strategy.risk_max", "==", 0),
    "entry_wait_s": ("strategy.limit_offset", ">", 0), "limit_fallback": ("strategy.limit_offset", ">", 0),
    "sweep_days": ("strategy.filters", "==", True), "sweep_max_days": ("strategy.filters", "==", True),
    "sweep_4h_m60": ("strategy.sweep_4h_m60", ">", 0),
    "news_events": ("risk.news_mode", "!=", "off"), "news_before_min": ("risk.news_mode", "==", "window"),
    "news_after_min": ("risk.news_mode", "==", "window"), "news_windows": ("risk.news_mode", "==", "window"),
}
# Not in use -> left off the page entirely (instead of folded away): the moneyness line once her risk check replaces it
HIDE_UNUSED = {"mny_start", "mny_floor", "mny_end_s"}
_GROUP_OF = {n: g for g, _, names in GROUPS for n in names}


def spec_json() -> list[dict]:
    return [dict(key=f"{s}.{n}", type=t, min=lo, max=hi, label=lab, help=h, group=_GROUP_OF.get(n, "Other"),
                 show_if=list(SHOW_IF[n]) if n in SHOW_IF else None, hide_unused=n in HIDE_UNUSED) for s, n, t, lo, hi, lab, h in SPEC]


def groups_json() -> list[dict]:
    return [dict(title=g, about=a) for g, a, _ in GROUPS]
