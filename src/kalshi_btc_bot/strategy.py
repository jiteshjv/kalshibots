from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone
from statistics import median

from .config import StrategyConfig
from .models import Market, PriceSample, Signal


class SettlementModel:
    """Models the 60 one-second BRTI observations used for settlement."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.samples: deque[PriceSample] = deque()

    def add(self, sample: PriceSample) -> None:
        if self.samples and sample.timestamp <= self.samples[-1].timestamp:
            return
        self.samples.append(sample)
        cutoff = sample.timestamp.timestamp() - self.config.vol_lookback_seconds
        while self.samples and self.samples[0].timestamp.timestamp() < cutoff:
            self.samples.popleft()

    def volatility(self) -> float:
        """Robust dollars/sqrt(second) estimate using adjacent BRTI changes."""
        rates: list[float] = []
        pairs = zip(self.samples, list(self.samples)[1:])
        for left, right in pairs:
            dt = (right.timestamp - left.timestamp).total_seconds()
            if 0.2 <= dt <= 5.0:
                rates.append(abs(right.price - left.price) / math.sqrt(dt))
        if len(rates) < max(5, self.config.min_samples // 3):
            return self.config.min_vol_dollars_per_sqrt_second
        # median(|N(0, sigma)|) = 0.67449 sigma
        sigma = median(rates) / 0.67448975
        return min(
            self.config.max_vol_dollars_per_sqrt_second,
            max(self.config.min_vol_dollars_per_sqrt_second, sigma),
        )

    def fair_yes_probability(self, market: Market, now: datetime | None = None) -> float:
        if not self.samples:
            raise ValueError("no BRTI samples")
        now = now or datetime.now(timezone.utc)
        current = self.samples[-1].price
        seconds_left = max(0.0, (market.close_time - now).total_seconds())
        average_start = market.close_time.timestamp() - 60.0
        sigma = self.volatility()

        if now.timestamp() <= average_start:
            delay = average_start - now.timestamp()
            mean = current
            variance = sigma * sigma * (delay + 20.0)
        else:
            observed = [
                sample.price
                for sample in self.samples
                if average_start <= sample.timestamp.timestamp() <= now.timestamp()
            ]
            observed_count = min(60.0, float(len(observed)))
            remaining = max(0.0, 60.0 - observed_count)
            mean = (sum(observed) + remaining * current) / 60.0
            variance = sigma * sigma * remaining**3 / (3.0 * 60.0**2)

        if seconds_left <= 0.0 or variance <= 1e-12:
            raw = 1.0 if mean >= market.strike else 0.0
        else:
            z = (mean - market.strike) / math.sqrt(variance)
            raw = _normal_cdf(z)

        # Shrink toward 50% for basis, timestamp, and model error.
        confidence = self.config.model_confidence
        return 0.5 + confidence * (raw - 0.5)

    def signal(self, market: Market, now: datetime | None = None) -> Signal | None:
        now = now or datetime.now(timezone.utc)
        if len(self.samples) < self.config.min_samples:
            return None
        age = (now - self.samples[-1].timestamp).total_seconds()
        if age < -1.0 or age > self.config.max_data_age_seconds:
            return None
        seconds_left = (market.close_time - now).total_seconds()
        if not self.config.min_seconds_to_close <= seconds_left <= self.config.max_seconds_to_close:
            return None
        spread = market.yes_ask - market.yes_bid
        if spread < 0 or spread > self.config.max_spread:
            return None

        fair_yes = self.fair_yes_probability(market, now)
        candidates = [
            ("yes", fair_yes, market.yes_ask),
            ("no", 1.0 - fair_yes, market.no_ask),
        ]
        side, fair, ask = max(candidates, key=lambda item: item[1] - item[2])
        edge = fair - ask
        if not self.config.min_entry_price <= ask <= self.config.max_entry_price:
            return None
        if edge < self.config.min_edge:
            return None
        contracts = min(
            self.config.max_contracts,
            int(self.config.max_premium_per_trade / ask),
        )
        if contracts <= 0:
            return None
        return Signal(
            side=side,
            fair_probability=fair,
            ask_price=ask,
            edge=edge,
            contracts=contracts,
            reason=(
                f"fair={fair:.3f} ask={ask:.3f} edge={edge:.3f} "
                f"brti={self.samples[-1].price:.2f} strike={market.strike:.2f} "
                f"sigma={self.volatility():.2f} t={seconds_left:.1f}s"
            ),
        )


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

