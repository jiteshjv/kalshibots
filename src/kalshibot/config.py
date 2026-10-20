"""kalshibot settings. Defaults = the working model of 2026-10-19 (RESEARCH_RUNBOOK.md, scripts/her_trades.py).

Load with Settings.load(path) from a YAML file; any key left out keeps its default. Never put secrets in the YAML:
the Kalshi key id / private key path come from environment variables (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class StrategyCfg:
    series: str = "KXBTC15M"
    max_range_2h: float = 0.031          # skip the market if BTC's prior 2h (high-low)/mean > 3.1%
    first_check_s: int = 120             # first entry check at minute 2
    last_entry_s: int = 720              # no new entries after minute 12
    check_every_s: int = 10              # entry checks every 10 s
    mny_start: float = 0.0015            # required moneyness at first_check_s ...
    mny_floor: float = 0.0005            # ... falling linearly to this at end_s
    mny_end_s: int = 900
    max_price: float = 0.985             # buy only if the ask is below 98.5c
    max_spread: float = 0.03             # no entry if the spread is wider than 3c (her rule)
    size_early: int = 200                # contracts per entry before early_until_s ("mixed" sizing)
    size_late: int = 100
    early_until_s: int = 360
    sizing: str = "mixed"                # "mixed" | "equal" (equal = size_late every entry)
    max_held: int = 5000                 # max contracts held at once per market
    stop_pct: float = 0.30               # each entry: stop when its side trades <= buy * (1 - 0.30) ...
    stop_lead: float = 0.0001            # ... AND BTC is within +0.01% of the strike (our side)
    take_profit: float = 0.997           # resting sell for everything open at 99.7c
    roll: bool = True                    # after a full stop-out, may enter the other side
    stop_recheck: bool = False           # sell a triggered stop only if our side's bid is still <= that entry's stop price
    skip_hours_pt: str = ""              # skip whole markets whose window starts in these Pacific hours, e.g. "7-9" or
                                         # "7-9,13-14" (start hour inclusive, end hour exclusive); "" = none
    jump_pause: float = 0.0              # >0: no new entries while any clock minute in the last 15 moved more than this
                                         #     (fraction, e.g. 0.005 = 0.5%); 0 = off
    limit_offset: float = 0.005          # entry: bid 0.5c under the ask ...
    limit_fallback: bool = True          # ... then buy at market if not filled by the next check
    filters: bool = True                 # liquidity-sweep filters (checked at the first entry of a market)
    sweep_days: float = 2.0              # skip if the last 5 min ran through the prior 2-day high/low (our side)
    sweep_max_days: float = 0.0          # >0: do NOT skip when the break is of a level at least this old (e.g. 30 -> 3-to-14-day rule); 0 = no limit
    sweep_4h_m60: float = 0.003879       # skip if 4h sweep AND 1h move our way >= 0.3879% (as a fraction); 0 = rule off
    sweep_lookback_s: int = 300          # "last 5 minutes"


@dataclass
class RiskCfg:
    mode: str = "paper"                  # "paper" | "live"  (live also needs live_confirm)
    live_confirm: str = ""               # must equal "I UNDERSTAND THIS TRADES REAL MONEY" for live mode
    daily_loss_limit: float = 1500.0     # stop opening entries for the rest of the UTC day after this loss ($)
    max_open_orders: int = 60
    stale_btc_s: float = 3.0             # no new entries if the BTC price is older than this
    stale_book_s: float = 3.0            # no new entries if the Kalshi book is older than this
    max_brti_vs_composite: float = 25.0  # pause entries if BRTI and the exchange composite disagree by > $25
    kill_file: str = "KILL"              # create this file in the data dir: cancel all, SELL EVERYTHING at market, stop
    disarm_file: str = "DISARM"          # create this file: no new entries/markets; positions close normally, then stop
    news_mode: str = "off"               # news days: "off" (trade as usual) | "day" (no entries all day, PT) | "window"
    news_events: str = "jobs,cpi,fomc"   # which calendar events count (jobs, cpi, ppi, fomc, gdp_pce, retail, claims)
    news_before_min: int = 15            # window mode: no new entries from this many minutes before a release ...
    news_after_min: int = 60             # ... to this many minutes after it (stops and the 99.7c sell keep working)
    news_calendar: str = "news_calendar.csv"   # date,event,time_et rows (time in New York time); in the data dir
    news_windows: str = ""               # per-event windows "event:before_min:after_min,..." (overrides news_events /
                                         # before / after), e.g. "jobs:15:60,cpi:15:60,claims:15:60,fomc:0:60"


@dataclass
class IoCfg:
    data_dir: str = "~/kalshibot-data"   # journal, logs, state, recordings
    record: bool = False                 # full order-book recorder (research machine only)
    book_poll_s: float = 1.0             # no-key REST book polling interval (0.2 = 5 books/s); trades stay at 1 s
    s3_bucket: str = ""                  # optional: upload recordings here
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_env: str = "TELEGRAM_CHAT_ID"
    dashboard_port: int = 8787


@dataclass
class Settings:
    strategy: StrategyCfg = field(default_factory=StrategyCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    io: IoCfg = field(default_factory=IoCfg)

    @property
    def data_path(self) -> Path:
        p = Path(os.path.expanduser(self.io.data_dir))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def load(cls, path: str | os.PathLike | None) -> "Settings":
        s = cls()
        if path:
            import yaml
            raw = yaml.safe_load(Path(path).read_text()) or {}
            for sect in ("strategy", "risk", "io"):
                obj = getattr(s, sect)
                names = {f.name for f in fields(obj)}
                for k, v in (raw.get(sect) or {}).items():
                    if k not in names:
                        raise ValueError(f"unknown setting {sect}.{k}")
                    setattr(obj, k, type(getattr(obj, k))(v))
        s._path = str(path) if path else None        # remembered so the dashboard can reset a setting to config.yaml
        return s

    def to_dict(self) -> dict:
        return asdict(self)


LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS TRADES REAL MONEY"


def kalshi_credentials() -> tuple[str | None, str | None]:
    return os.environ.get("KALSHI_API_KEY_ID"), os.environ.get("KALSHI_PRIVATE_KEY_PATH")
