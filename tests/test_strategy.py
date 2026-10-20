from datetime import datetime, timedelta, timezone

from kalshi_btc_bot.config import StrategyConfig
from kalshi_btc_bot.models import Market, PriceSample
from kalshi_btc_bot.strategy import SettlementModel


def make_market(now: datetime, strike: float = 100.0) -> Market:
    return Market(
        ticker="KXBTC15M-TEST-00",
        close_time=now + timedelta(seconds=90),
        strike=strike,
        yes_bid=0.59,
        yes_ask=0.60,
        no_bid=0.39,
        no_ask=0.40,
        exchange_index=2,
        status="open",
    )


def test_probability_rises_when_brti_is_above_strike() -> None:
    now = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
    config = StrategyConfig(min_samples=5, model_confidence=1.0)
    high = SettlementModel(config)
    low = SettlementModel(config)
    for index in range(30):
        timestamp = now - timedelta(seconds=29 - index)
        high.add(PriceSample(timestamp, 110.0 + (index % 2)))
        low.add(PriceSample(timestamp, 90.0 + (index % 2)))
    market = make_market(now)
    assert high.fair_yes_probability(market, now) > 0.75
    assert low.fair_yes_probability(market, now) < 0.30


def test_signal_requires_edge_and_sizes_to_premium_cap() -> None:
    now = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
    config = StrategyConfig(
        min_samples=5,
        min_edge=0.05,
        model_confidence=1.0,
        max_contracts=10,
        max_premium_per_trade=2.0,
    )
    model = SettlementModel(config)
    for index in range(30):
        model.add(PriceSample(now - timedelta(seconds=29 - index), 115.0 + (index % 2)))
    signal = model.signal(make_market(now), now)
    assert signal is not None
    assert signal.side == "yes"
    assert signal.contracts == 3


def test_no_signal_with_stale_data() -> None:
    now = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
    config = StrategyConfig(min_samples=5)
    model = SettlementModel(config)
    for index in range(10):
        model.add(PriceSample(now - timedelta(seconds=20 - index), 110.0))
    assert model.signal(make_market(now), now) is None

