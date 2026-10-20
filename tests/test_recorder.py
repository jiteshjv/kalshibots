from datetime import datetime, timedelta, timezone

from kalshi_btc_bot.bot import JsonlJournal, TradingBot
from kalshi_btc_bot.cli import parser
from kalshi_btc_bot.config import StrategyConfig
from kalshi_btc_bot.models import Market, PriceSample


def test_cli_defaults_to_recorder():
    assert parser().parse_args([]).strategy == "recorder"


def test_recorder_logs_quote_without_order_or_paper_fill(tmp_path):
    now = datetime.now(timezone.utc)
    market = Market("test", now+timedelta(seconds=90), 100, .59, .60, .39, .40, 2, "open")

    class Client:
        def get_open_market(self, series):
            return market

        def get_market(self, ticker):
            return market

        def get_brti(self):
            return PriceSample(now, 110)

        def place_ioc(self, *args):
            raise AssertionError("recorder submitted an order")

    log = tmp_path/"events.jsonl"
    bot = TradingBot(Client(), StrategyConfig(), JsonlJournal(log), live=False, record_only=True)
    bot.tick()
    text = log.read_text()
    assert '"event": "quote"' in text
    assert '"close_time"' in text
    assert "paper_fill" not in text
    assert not bot.traded_markets
    assert bot.daily_premium == 0
