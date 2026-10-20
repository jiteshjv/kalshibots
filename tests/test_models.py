from datetime import timezone

from kalshi_btc_bot.models import Market


def test_market_parses_fixed_point_prices() -> None:
    market = Market.from_api(
        {
            "ticker": "KXBTC15M-TEST-00",
            "close_time": "2026-10-15T17:00:00Z",
            "floor_strike": 81983.32,
            "yes_bid_dollars": "0.6800",
            "yes_ask_dollars": "0.6900",
            "no_bid_dollars": "0.3100",
            "no_ask_dollars": "0.3200",
            "exchange_index": 2,
            "status": "open",
        }
    )
    assert market.ticker == "KXBTC15M-TEST-00"
    assert market.close_time.tzinfo == timezone.utc
    assert market.strike == 81983.32
    assert market.yes_ask == 0.69
    assert market.no_ask == 0.32
    assert market.exchange_index == 2

