from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Market:
    ticker: str
    close_time: datetime
    strike: float
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    exchange_index: int
    status: str

    @classmethod
    def from_api(cls, raw: dict) -> "Market":
        def price(name: str, legacy: str) -> float:
            value = raw.get(name)
            if value not in (None, ""):
                return float(Decimal(str(value)))
            cents = raw.get(legacy)
            return float(cents) / 100.0 if cents is not None else 0.0

        strike = raw.get("floor_strike")
        if strike is None:
            strike = raw.get("functional_strike")
        if strike is None:
            raise ValueError(f"market {raw.get('ticker')} has no published strike")

        close = datetime.fromisoformat(str(raw["close_time"]).replace("Z", "+00:00"))
        return cls(
            ticker=str(raw["ticker"]),
            close_time=close,
            strike=float(strike),
            yes_bid=price("yes_bid_dollars", "yes_bid"),
            yes_ask=price("yes_ask_dollars", "yes_ask"),
            no_bid=price("no_bid_dollars", "no_bid"),
            no_ask=price("no_ask_dollars", "no_ask"),
            exchange_index=int(raw.get("exchange_index", 2)),
            status=str(raw.get("status", "")),
        )


@dataclass(frozen=True)
class PriceSample:
    timestamp: datetime
    price: float


@dataclass(frozen=True)
class Signal:
    side: str
    fair_probability: float
    ask_price: float
    edge: float
    contracts: int
    reason: str

