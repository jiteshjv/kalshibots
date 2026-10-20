from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .bot import JsonlJournal, TradingBot
from .client import KalshiClient
from .config import StrategyConfig


LIVE_CONFIRMATION = "I_UNDERSTAND_REAL_MONEY"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Trade Kalshi KXBTC15M with hard risk caps")
    result.add_argument("--live", action="store_true", help="submit real-money IOC orders")
    result.add_argument("--strategy", choices=["recorder", "legacy"], default="recorder",
                        help="default: record quotes only; legacy explicitly enables the original research model")
    result.add_argument("--confirm-live", default="", help=argparse.SUPPRESS)
    result.add_argument("--once", action="store_true", help="perform one polling cycle")
    result.add_argument("--log", type=Path, default=Path("data/trades.jsonl"))
    result.add_argument("--max-contracts", type=int, default=5)
    result.add_argument("--max-premium", type=float, default=4.0)
    result.add_argument("--max-daily-premium", type=float, default=20.0)
    result.add_argument("--min-edge", type=float, default=0.08)
    result.add_argument("--verbose", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    if args.live and args.strategy == "recorder":
        raise SystemExit("Recorder mode does not submit orders")
    if args.live and args.confirm_live != LIVE_CONFIRMATION:
        raise SystemExit(
            "Live mode refused. Re-run with --live --confirm-live " + LIVE_CONFIRMATION
        )
    key_id = os.environ.get("KALSHI_API_KEY_ID", "")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    if not key_id or not key_path:
        raise SystemExit("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = StrategyConfig(
        max_contracts=args.max_contracts,
        max_premium_per_trade=args.max_premium,
        max_daily_premium=args.max_daily_premium,
        min_edge=args.min_edge,
    )
    client = KalshiClient(
        base_url=os.environ.get(
            "KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"
        ),
        api_key_id=key_id,
        private_key_path=Path(key_path),
    )
    bot = TradingBot(client, config, JsonlJournal(args.log), live=args.live,
                     record_only=args.strategy == "recorder")
    bot.run(once=args.once)


if __name__ == "__main__":
    main()

