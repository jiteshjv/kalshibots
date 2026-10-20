from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .client import KalshiClient, KalshiError
from .config import StrategyConfig
from .strategy import SettlementModel


class JsonlJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


class TradingBot:
    def __init__(
        self,
        client: KalshiClient,
        strategy_config: StrategyConfig,
        journal: JsonlJournal,
        live: bool,
        record_only: bool = False,
    ) -> None:
        self.client = client
        self.config = strategy_config
        self.journal = journal
        self.live = live
        if live and record_only:
            raise ValueError("record-only mode cannot place live orders")
        self.record_only = record_only
        self.model = SettlementModel(strategy_config)
        self.traded_markets: set[str] = set()
        self.daily_premium = 0.0
        self.current_ticker: str | None = None

    def run(self, once: bool = False) -> None:
        logging.info("mode=%s series=%s", "LIVE" if self.live else "PAPER", self.config.series_ticker)
        while True:
            started = time.monotonic()
            try:
                self.tick()
            except (KalshiError, requests.RequestException) as exc:
                logging.warning("API error: %s", exc)
                self.journal.write("error", error=str(exc))
            except Exception:
                logging.exception("unexpected bot error")
                self.journal.write("error", error="unexpected exception; see stderr")
            if once:
                return
            delay = max(0.05, self.config.poll_seconds - (time.monotonic() - started))
            time.sleep(delay)

    def tick(self) -> None:
        market = self.client.get_open_market(self.config.series_ticker)
        if market is None:
            logging.info("no open %s market", self.config.series_ticker)
            return
        if market.ticker != self.current_ticker:
            self.current_ticker = market.ticker
            self.model = SettlementModel(self.config)
            logging.info("tracking %s strike=%.2f", market.ticker, market.strike)
            self.journal.write("market", ticker=market.ticker, strike=market.strike)

        sample = self.client.get_brti()
        self.model.add(sample)
        fresh_market = self.client.get_market(market.ticker)
        now = datetime.now(timezone.utc)
        fair = self.model.fair_yes_probability(fresh_market, now)
        logging.info(
            "%s brti=%.2f strike=%.2f yes=%.3f/%.3f fair=%.3f t=%.1fs",
            fresh_market.ticker,
            sample.price,
            fresh_market.strike,
            fresh_market.yes_bid,
            fresh_market.yes_ask,
            fair,
            (fresh_market.close_time - now).total_seconds(),
        )
        self.journal.write(
            "quote",
            ticker=fresh_market.ticker,
            brti=sample.price,
            brti_timestamp=sample.timestamp,
            strike=fresh_market.strike,
            yes_bid=fresh_market.yes_bid,
            yes_ask=fresh_market.yes_ask,
            no_bid=fresh_market.no_bid,
            no_ask=fresh_market.no_ask,
            close_time=fresh_market.close_time,
            status=fresh_market.status,
            fair_yes=fair,
        )

        if self.record_only:
            return
        if fresh_market.ticker in self.traded_markets:
            return
        signal = self.model.signal(fresh_market, now)
        if signal is None:
            return
        premium = signal.ask_price * signal.contracts
        if self.daily_premium + premium > self.config.max_daily_premium:
            self.journal.write("risk_reject", ticker=fresh_market.ticker, reason="daily premium cap")
            return

        self.traded_markets.add(fresh_market.ticker)
        self.daily_premium += premium
        fields = {"ticker": fresh_market.ticker, **asdict(signal), "premium": premium}
        if self.live:
            response = self.client.place_ioc(
                fresh_market,
                signal.side,
                signal.contracts,
                signal.ask_price,
            )
            logging.warning("LIVE ORDER %s response=%s", signal.reason, response)
            self.journal.write("live_order", **fields, response=response)
        else:
            logging.warning("PAPER FILL %s", signal.reason)
            self.journal.write("paper_fill", **fields)

