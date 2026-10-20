"""Keep the news calendar (<data_dir>/news_calendar.csv: date,event,time_et) up to date by itself.

Sources (both free, no key; any failure just keeps the current file):
  * Federal Reserve FOMC calendar page: every meeting of this and next year -> "fomc" at 14:00 ET on the last day of
    each meeting (notation votes are skipped).
  * faireconomy weekly feed (ff_calendar_thisweek.json): this week's high-impact US releases with their exact times ->
    jobs (Non-Farm Employment Change), cpi, ppi, claims, gdp_pce, retail, fomc. It corrects times and adds events.
The Labor Department's own site blocks automated downloads, so the bundled file (shipped with the bot) stays the base for
releases more than a week ahead.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import re
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("kalshibot.newscal")
ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
FED = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FF = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
FF_MAP = [("non-farm employment change", "jobs"), ("core cpi m/m", "cpi"), ("cpi m/m", "cpi"), ("core ppi m/m", "ppi"),
          ("ppi m/m", "ppi"), ("unemployment claims", "claims"), ("federal funds rate", "fomc"), ("fomc statement", "fomc"),
          ("advance gdp", "gdp_pce"), ("prelim gdp", "gdp_pce"), ("final gdp", "gdp_pce"), ("core pce price index", "gdp_pce"),
          ("retail sales m/m", "retail")]


def _get(url: str, timeout: float = 20) -> str:
    from kalshibot.marketdata.btc import ssl_context
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    with urllib.request.urlopen(req, context=ssl_context(), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_fomc(html: str) -> list[tuple[str, str, str]]:
    out = []
    for y in sorted(set(re.findall(r"(\d{4}) FOMC Meetings", html))):
        i = html.find(f"{y} FOMC Meetings")
        j = html.find("FOMC Meetings", i + 20)
        seg = html[i:j if j > 0 else i + 20000]
        for mon, days in re.findall(r"fomc-meeting__month[^>]*>\s*(?:<strong>)?([A-Za-z/]+)(?:</strong>)?\s*<.*?"
                                    r"fomc-meeting__date[^>]*>\s*([^<]+?)\s*<", seg, re.S):
            if "notation" in days.lower() or "unscheduled" in days.lower():
                continue
            last_mon = mon.split("/")[-1].lower()
            m = MONTHS.get(last_mon) or next((v for k, v in MONTHS.items() if k.startswith(last_mon[:3])), None)
            d = re.findall(r"\d+", days)
            if m and d:
                out.append((f"{y}-{m:02d}-{int(d[-1]):02d}", "fomc", "14:00"))
    return out


def parse_ff(text: str) -> list[tuple[str, str, str]]:
    out = []
    for x in json.loads(text):
        if x.get("country") != "USD" or x.get("impact") != "High":
            continue
        t = (x.get("title") or "").lower()
        ev = next((e for k, e in FF_MAP if k in t), None)
        if not ev:
            continue
        try:
            when = dt.datetime.fromisoformat(x["date"]).astimezone(ET)
        except (KeyError, ValueError):
            continue
        out.append((when.strftime("%Y-%m-%d"), ev, when.strftime("%H:%M")))
    return out


def read(path: Path) -> list[tuple[str, str, str]]:
    try:
        with open(path, newline="") as fh:
            return [(r["date"].strip(), r["event"].strip(), r["time_et"].strip()) for r in csv.DictReader(fh)]
    except (OSError, KeyError):
        return []


def merge(base: list, new: list) -> list:
    """Union by (date, event); a downloaded row replaces the time of the same (date, event)."""
    rows = {(d, e): t for d, e, t in base}
    for d, e, t in new:
        rows[(d, e)] = t
    return sorted((d, e, t) for (d, e), t in rows.items())


def update(path: str | Path, get=_get) -> dict:
    """Download + merge into `path`. Returns {"added": n, "sources": {...}, "updated": ts}. Never raises."""
    path = Path(path)
    base = read(path)
    new, src = [], {}
    for name, url, parser in (("fed", FED, parse_fomc), ("ff", FF, parse_ff)):
        try:
            got = parser(get(url))
            new += got
            src[name] = len(got)
        except Exception as e:                            # noqa: BLE001 - keep the current file
            src[name] = f"failed: {type(e).__name__}"
            log.warning("news calendar %s: %s", name, e)
    rows = merge(base, new)
    added = len(set(rows) - set(base))
    if rows and (added or len(rows) != len(base)):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "event", "time_et"])
            w.writerows(rows)
        tmp.replace(path)
    import time
    info = {"added": added, "sources": src, "updated": time.time()}
    log.info("news calendar: %s", info)
    return info
