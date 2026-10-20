from datetime import datetime, timezone

from kalshi_btc_bot.client import KalshiClient, _extract_brti
from kalshi_btc_bot.models import Market


class RecordingClient(KalshiClient):
    def __init__(self) -> None:
        self.recorded = None

    def _request(self, method, endpoint, **kwargs):
        self.recorded = (method, endpoint, kwargs)
        return {"order_id": "test-order"}


def market() -> Market:
    return Market(
        ticker="KXBTC15M-TEST-00",
        close_time=datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc),
        strike=80_000.0,
        yes_bid=0.59,
        yes_ask=0.60,
        no_bid=0.39,
        no_ask=0.40,
        exchange_index=2,
        status="open",
    )


def test_buy_no_maps_to_yes_ask() -> None:
    client = RecordingClient()
    client.place_ioc(market(), "no", contracts=3, max_price=0.40)
    method, endpoint, kwargs = client.recorded
    body = kwargs["json"]
    assert method == "POST"
    assert endpoint == "/portfolio/events/orders"
    assert body["side"] == "ask"
    assert body["price"] == "0.6000"
    assert body["count"] == "3"
    assert body["time_in_force"] == "immediate_or_cancel"
    assert body["exchange_index"] == 2


def test_buy_yes_maps_to_yes_bid() -> None:
    client = RecordingClient()
    client.place_ioc(market(), "yes", contracts=2, max_price=0.61)
    body = client.recorded[2]["json"]
    assert body["side"] == "bid"
    assert body["price"] == "0.6100"


def test_extract_brti_from_documented_envelope_shape() -> None:
    price, timestamp = _extract_brti(
        {
            "serverTime": "2026-10-15T17:00:00.000Z",
            "payload": {"values": [{"id": "BRTI", "value": "82034.70"}]},
        }
    )
    assert price == 82034.70
    assert timestamp == datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
