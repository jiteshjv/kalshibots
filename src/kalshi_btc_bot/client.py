from __future__ import annotations

import base64
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # The macOS system OpenSSL path below supports RSA keys.
    hashes = serialization = padding = Ed25519PrivateKey = None

from .models import Market, PriceSample


class KalshiError(RuntimeError):
    pass


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        api_key_id: str,
        private_key_path: Path,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_id = api_key_id
        self.timeout = timeout
        self.private_key_path = private_key_path
        if not private_key_path.is_file():
            raise FileNotFoundError(private_key_path)
        if serialization is not None:
            with private_key_path.open("rb") as fh:
                self.private_key = serialization.load_pem_private_key(fh.read(), password=None)
        else:
            self.private_key = None
        self.session = requests.Session()

    def _headers(self, method: str, endpoint: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        full_path = urlparse(self.base_url + endpoint).path
        message = f"{timestamp}{method.upper()}{full_path}".encode()
        if Ed25519PrivateKey is not None and isinstance(self.private_key, Ed25519PrivateKey):
            signature = self.private_key.sign(message)
        elif self.private_key is not None:
            signature = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
        else:
            # OpenSSL is preinstalled on macOS and avoids making installation a
            # network prerequisite. This command implements RSA-PSS/SHA-256.
            process = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-sign",
                    str(self.private_key_path),
                    "-sigopt",
                    "rsa_padding_mode:pss",
                    "-sigopt",
                    "rsa_pss_saltlen:digest",
                ],
                input=message,
                capture_output=True,
                check=False,
            )
            if process.returncode != 0:
                detail = process.stderr.decode(errors="replace")[:300]
                raise KalshiError(
                    "OpenSSL could not sign the request. Use an RSA Kalshi key "
                    f"or install cryptography for Ed25519 support: {detail}"
                )
            signature = process.stdout
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        headers = self._headers(method, endpoint) if authenticated else None
        response = self.session.request(
            method,
            self.base_url + endpoint,
            params=params,
            json=json,
            headers=headers,
            timeout=self.timeout,
        )
        if not response.ok:
            body = response.text[:500]
            raise KalshiError(f"{method} {endpoint}: HTTP {response.status_code}: {body}")
        return response.json()

    def get_open_market(self, series_ticker: str) -> Market | None:
        payload = self._request(
            "GET",
            "/markets",
            params={
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 100,
                "exchange_index": 2,
            },
        )
        markets: list[Market] = []
        for raw in payload.get("markets", []):
            try:
                markets.append(Market.from_api(raw))
            except (KeyError, TypeError, ValueError):
                continue
        if not markets:
            return None
        return min(markets, key=lambda market: market.close_time)

    def get_market(self, ticker: str) -> Market:
        payload = self._request("GET", f"/markets/{ticker}")
        return Market.from_api(payload.get("market", payload))

    def get_brti(self) -> PriceSample:
        payload = self._request(
            "GET",
            "/cfbenchmarks/values",
            params={"id": "BRTI"},
            authenticated=True,
        )
        data = payload.get("data", payload)
        price, timestamp = _extract_brti(data)
        return PriceSample(timestamp=timestamp, price=price)

    def get_balance(self, exchange_index: int = 2) -> dict[str, Any]:
        return self._request(
            "GET",
            "/portfolio/balance",
            params={"exchange_index": exchange_index},
            authenticated=True,
        )

    def place_ioc(
        self,
        market: Market,
        outcome: str,
        contracts: int,
        max_price: float,
    ) -> dict[str, Any]:
        if outcome not in {"yes", "no"}:
            raise ValueError("outcome must be yes or no")
        if contracts <= 0 or not 0.0 < max_price < 1.0:
            raise ValueError("invalid order size or price")

        # V2 quotes the YES book only. Buying NO at q is an ASK on YES at 1-q.
        side = "bid" if outcome == "yes" else "ask"
        yes_book_price = max_price if outcome == "yes" else 1.0 - max_price
        body = {
            "ticker": market.ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "count": str(contracts),
            "price": f"{yes_book_price:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "exchange_index": market.exchange_index,
        }
        return self._request(
            "POST",
            "/portfolio/events/orders",
            json=body,
            authenticated=True,
        )


def _extract_brti(data: Any) -> tuple[float, Any]:
    """Accept the documented envelope plus common CF Benchmarks payload shapes."""
    from datetime import datetime, timezone

    candidates: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            candidates.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    price_keys = ("value", "price", "rate", "indexValue", "index_value")
    time_keys = ("timestamp", "time", "serverTime", "publishedAt", "published_at")
    envelope_time = next(
        (item[key] for item in candidates for key in time_keys if key in item),
        None,
    )
    for item in candidates:
        price_value = next((item[k] for k in price_keys if k in item), None)
        if price_value is None:
            continue
        try:
            price = float(price_value)
        except (TypeError, ValueError):
            continue
        raw_time = next((item[k] for k in time_keys if k in item), envelope_time)
        if raw_time is None:
            timestamp = datetime.now(timezone.utc)
        elif isinstance(raw_time, (int, float)):
            scale = 1000.0 if raw_time > 10_000_000_000 else 1.0
            timestamp = datetime.fromtimestamp(raw_time / scale, tz=timezone.utc)
        else:
            timestamp = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        return price, timestamp
    raise KalshiError("could not locate a numeric BRTI value in CF Benchmarks response")

