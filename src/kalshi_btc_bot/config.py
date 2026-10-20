from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StrategyConfig:
    series_ticker: str = "KXBTC15M"
    min_seconds_to_close: float = 25.0
    max_seconds_to_close: float = 210.0
    min_entry_price: float = 0.08
    max_entry_price: float = 0.82
    min_edge: float = 0.08
    max_spread: float = 0.08
    min_samples: int = 20
    vol_lookback_seconds: int = 120
    min_vol_dollars_per_sqrt_second: float = 2.0
    max_vol_dollars_per_sqrt_second: float = 20.0
    model_confidence: float = 0.75
    max_contracts: int = 5
    max_premium_per_trade: float = 4.00
    max_daily_premium: float = 20.00
    poll_seconds: float = 1.0
    max_data_age_seconds: float = 3.0


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_key_id: str
    private_key_path: Path
    log_path: Path
    live: bool = False

