"""Kalshi Trade API v2 REST client (public + authenticated) for kalshibot.

* Signing: RSA-PSS/SHA-256 via the `openssl` CLI (the `cryptography` package is not available), copied from
  `kalshi_btc_bot/client.py`. Pre-sign text = timestamp_ms + METHOD + full path WITHOUT the query string, e.g.
  ``1700000000000GET/trade-api/v2/portfolio/balance`` (docs.kalshi.com "API Environments", "Request Signing").
* Rate limits: two token buckets (reads, writes), default 8 requests/s each; endpoints that Kalshi bills above the
  default 10 tokens are charged proportionally (CF Benchmarks passthrough = 50 tokens = 5 request units).
* Retries: GET/DELETE retry on 429, 5xx and connection errors with exponential backoff. POST (order creation)
  retries ONLY on 429 (Kalshi did not accept it) so a 5xx/timeout never silently duplicates an order; the caller
  (LiveBroker) reconciles by client_order_id instead.
* Orders use the V2 single-book endpoint ``POST /portfolio/events/orders`` where everything is quoted on the YES leg:
  ``side="bid"`` = buy YES / sell NO, ``side="ask"`` = sell YES / buy NO. :func:`yes_book_order` is the ONE place
  where kalshibot's (side, action, price) is mapped onto that.
"""
from __future__ import annotations

import base64
import logging
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import requests

from ..types import Book, KTrade, MarketInfo, Side

log = logging.getLogger("kalshibot.marketdata.rest")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
CRYPTO_EXCHANGE_INDEX = 2            # KXBTC15M lives on Kalshi's crypto shard
CA_FILE = "/etc/ssl/cert.pem"        # system bundle; required for websockets/urllib on this Mac


class KalshiError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


# ----------------------------------------------------------------------------------------------------------------
# YES-book order mapping (the single helper execution uses)
# ----------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class YesBookOrder:
    book_side: Literal["bid", "ask"]   # V2 `side` field
    yes_price: float                   # V2 `price` field (YES-leg dollars)


def yes_book_order(side: Side, action: Literal["buy", "sell"], price: float) -> YesBookOrder:
    """Map (our side, buy/sell, price of OUR side) to Kalshi's YES-book order fields.

    | ours           | V2 side | V2 price |
    |----------------|---------|----------|
    | buy  YES at p  | bid     | p        |
    | sell YES at p  | ask     | p        |
    | buy  NO  at q  | ask     | 1 - q    |   (buying NO at q == selling YES at 1-q)
    | sell NO  at q  | bid     | 1 - q    |   (selling NO at q == buying YES at 1-q)

    For a bid the YES price is the most we pay for YES; for an ask the least we accept. So a NO buy with cap q
    becomes a YES ask with floor 1-q, i.e. the cap/floor semantics carry over unchanged.
    """
    if side not in ("y", "n"):
        raise ValueError(f"side must be 'y' or 'n', got {side!r}")
    if action not in ("buy", "sell"):
        raise ValueError(f"action must be 'buy' or 'sell', got {action!r}")
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be inside (0, 1), got {price}")
    yes_px = price if side == "y" else 1.0 - price
    bid = (side == "y") == (action == "buy")
    return YesBookOrder("bid" if bid else "ask", round(yes_px, 4))


def from_yes_book(book_side: str, yes_price: float, side: Side) -> tuple[str, float]:
    """Inverse view for fills/orders: given the YES-book side and price, return (action, price of `side`)."""
    if side == "y":
        return ("buy" if book_side == "bid" else "sell"), round(yes_price, 4)
    return ("buy" if book_side == "ask" else "sell"), round(1.0 - yes_price, 4)


TimeInForce = Literal["good_till_canceled", "immediate_or_cancel", "fill_or_kill"]


def build_order_body(ticker: str, side: Side, action: Literal["buy", "sell"], qty: float, price: float, *,
                     time_in_force: TimeInForce = "good_till_canceled", post_only: bool = False,
                     reduce_only: bool = False, client_order_id: str | None = None,
                     expiration_ts: int | None = None, exchange_index: int | None = CRYPTO_EXCHANGE_INDEX,
                     self_trade_prevention_type: str = "taker_at_cross", cancel_order_on_pause: bool = True,
                     order_group_id: str | None = None, subaccount: int | None = None) -> dict[str, Any]:
    """JSON body for ``POST /portfolio/events/orders`` (Create Order V2, docs.kalshi.com, checked 2026-10-19).

    Notes from the spec: `count` and `price` are strings (2-dp contracts, 4-dp dollars); `reduce_only=True` is
    rejected unless time_in_force is immediate_or_cancel; `expiration_time` (unix s) needs good_till_canceled and
    cannot be combined with IOC; `post_only` makes a crossing order get rejected instead of taking.
    """
    if qty <= 0:
        raise ValueError("qty must be > 0")
    if time_in_force not in ("good_till_canceled", "immediate_or_cancel", "fill_or_kill"):
        raise ValueError(f"bad time_in_force {time_in_force!r}")
    if reduce_only and time_in_force != "immediate_or_cancel":
        raise ValueError("Kalshi rejects reduce_only unless time_in_force is immediate_or_cancel")
    if expiration_ts is not None and time_in_force != "good_till_canceled":
        raise ValueError("expiration_time requires good_till_canceled")
    if post_only and time_in_force != "good_till_canceled":
        raise ValueError("post_only only makes sense for resting (good_till_canceled) orders")
    o = yes_book_order(side, action, price)
    body: dict[str, Any] = {
        "ticker": ticker,
        "client_order_id": client_order_id or str(uuid.uuid4()),
        "side": o.book_side,
        "count": f"{qty:.2f}",
        "price": f"{o.yes_price:.4f}",
        "time_in_force": time_in_force,
        "self_trade_prevention_type": self_trade_prevention_type,
        "post_only": bool(post_only),
        "cancel_order_on_pause": bool(cancel_order_on_pause),
        "reduce_only": bool(reduce_only),
    }
    if exchange_index is not None:
        body["exchange_index"] = exchange_index
    if expiration_ts is not None:
        body["expiration_time"] = int(expiration_ts)
    if order_group_id:
        body["order_group_id"] = order_group_id
    if subaccount is not None:
        body["subaccount"] = subaccount
    return body


# ----------------------------------------------------------------------------------------------------------------
# Parsing helpers (shared with kalshi_ws / tests)
# ----------------------------------------------------------------------------------------------------------------

def iso_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def parse_levels(levels: Any) -> list[tuple[float, float]]:
    """[[price_str, qty_str], ...] -> [(price, qty)] sorted descending by price, zero-qty levels dropped."""
    out = []
    for p, q in levels or []:
        p, q = float(p), float(q)
        if q > 0:
            out.append((round(p, 4), q))
    out.sort(reverse=True)
    return out


def parse_orderbook(payload: dict, ts: float | None = None) -> Book:
    """REST /markets/{t}/orderbook response -> Book. Both sides are bids in their own leg's prices."""
    ob = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    return Book(ts=time.time() if ts is None else ts,
                yes_bids=parse_levels(ob.get("yes_dollars") or ob.get("yes_dollars_fp")),
                no_bids=parse_levels(ob.get("no_dollars") or ob.get("no_dollars_fp")))


def _taker_side(t: dict) -> Side:
    s = t.get("taker_outcome_side") or t.get("taker_side")
    if s is None and t.get("taker_book_side"):
        s = "yes" if t["taker_book_side"] == "bid" else "no"
    return "y" if str(s).startswith("y") else "n"


def parse_trade(t: dict) -> KTrade:
    """REST or WS trade dict -> KTrade (price = what the taker paid for the taker's side)."""
    side = _taker_side(t)
    if "ts_ms" in t:
        ts = t["ts_ms"] / 1000.0
    elif "created_time" in t:
        ts = iso_ts(t["created_time"])
    else:
        ts = float(t["ts"])
    if side == "y":
        px = float(t["yes_price_dollars"]) if "yes_price_dollars" in t else 1.0 - float(t["no_price_dollars"])
    else:
        px = float(t["no_price_dollars"]) if "no_price_dollars" in t else 1.0 - float(t["yes_price_dollars"])
    qty = float(t.get("count_fp", t.get("count", 0)))
    return KTrade(ts=ts, taker_side=side, price=round(px, 4), qty=qty)


def parse_market(m: dict) -> MarketInfo | None:
    """Raw /markets item -> MarketInfo (None if it has no strike yet)."""
    if m.get("floor_strike") is None:
        return None
    op = iso_ts(m["open_time"])
    cl = iso_ts(m["close_time"])
    return MarketInfo(ticker=m["ticker"], open_ts=op, close_ts=cl, strike=float(m["floor_strike"]),
                      exchange_index=int(m.get("exchange_index", CRYPTO_EXCHANGE_INDEX)))


def extract_brti(data: Any) -> tuple[float, float]:
    """Find (ts, price) in a CF Benchmarks passthrough payload (shape copied from kalshi_btc_bot._extract_brti)."""
    cands: list[dict] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            cands.append(v)
            for c in v.values():
                walk(c)
        elif isinstance(v, list):
            for c in v:
                walk(c)

    walk(data)
    pkeys = ("value", "price", "rate", "indexValue", "index_value")
    tkeys = ("time", "timestamp", "serverTime", "publishedAt", "published_at")
    env_t = next((it[k] for it in cands for k in tkeys if k in it), None)
    for it in cands:
        pv = next((it[k] for k in pkeys if k in it), None)
        if pv is None:
            continue
        try:
            price = float(pv)
        except (TypeError, ValueError):
            continue
        rt = next((it[k] for k in tkeys if k in it), env_t)
        if rt is None:
            ts = time.time()
        elif isinstance(rt, (int, float)):
            ts = rt / 1000.0 if rt > 10_000_000_000 else float(rt)
        else:
            ts = iso_ts(str(rt))
        return ts, price
    raise KalshiError("no numeric BRTI value in CF Benchmarks response")


# ----------------------------------------------------------------------------------------------------------------
# Rate limiting + signing
# ----------------------------------------------------------------------------------------------------------------

class TokenBucket:
    """Thread-safe token bucket: `rate` units/s, holds up to `capacity`. acquire() blocks until `cost` is free."""

    def __init__(self, rate: float, capacity: float | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self.tokens = self.capacity
        self.clock, self.sleep = clock, sleep
        self.t = clock()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def acquire(self, cost: float = 1.0) -> float:
        """Take `cost` units, sleeping as needed. Returns seconds waited."""
        cost = min(cost, self.capacity)
        waited = 0.0
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= cost:
                    self.tokens -= cost
                    return waited
                need = (cost - self.tokens) / self.rate
            self.sleep(need)
            waited += need


def openssl_sign(key_path: str | Path, message: bytes) -> bytes:
    """RSA-PSS (salt = digest length) / SHA-256 signature using the openssl CLI."""
    p = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_path), "-sigopt", "rsa_padding_mode:pss",
                        "-sigopt", "rsa_pss_saltlen:digest"], input=message, capture_output=True, check=False)
    if p.returncode != 0:
        raise KalshiError("openssl could not sign the request (Kalshi RSA key required): "
                          + p.stderr.decode(errors="replace")[:300])
    return p.stdout


def auth_headers(key_id: str, key_path: str | Path, method: str, path: str,
                 now_ms: int | None = None) -> dict[str, str]:
    """KALSHI-ACCESS-* headers. `path` is the full URL path without query, e.g. /trade-api/v2/portfolio/balance
    or /trade-api/ws/v2 for the WebSocket handshake."""
    ts = str(int(time.time() * 1000) if now_ms is None else now_ms)
    path = path.split("?", 1)[0]
    sig = openssl_sign(key_path, f"{ts}{method.upper()}{path}".encode())
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


# ----------------------------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------------------------

RETRY_STATUS = {429, 500, 502, 503, 504}


class KalshiRest:
    """Kalshi REST. Public methods work without a key; portfolio/order/BRTI methods need key_id + key_path."""

    def __init__(self, key_id: str | None = None, key_path: str | Path | None = None, *, base_url: str = BASE_URL,
                 timeout: float = 5.0, reads_per_s: float = 8.0, writes_per_s: float = 8.0, max_retries: int = 4,
                 backoff: float = 0.5, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, exchange_index: int = CRYPTO_EXCHANGE_INDEX):
        self.key_id = key_id or None
        self.key_path = Path(key_path).expanduser() if key_path else None
        if self.key_path is not None and not self.key_path.is_file():
            raise FileNotFoundError(self.key_path)
        self.base_url = base_url.rstrip("/")
        self.base_path = urlparse(self.base_url).path            # /trade-api/v2
        self.timeout = timeout
        self.session = session or requests.Session()
        self.read_bucket = TokenBucket(reads_per_s, reads_per_s)
        self.write_bucket = TokenBucket(writes_per_s, writes_per_s)
        self.max_retries, self.backoff, self.sleep = max_retries, backoff, sleep
        self.exchange_index = exchange_index

    @property
    def has_key(self) -> bool:
        return bool(self.key_id and self.key_path)

    # -- core --------------------------------------------------------------------------------------------------
    def request(self, method: str, endpoint: str, *, params: dict | None = None, json: Any = None,
                auth: bool = False, cost: float = 1.0, write: bool | None = None) -> dict:
        method = method.upper()
        if auth and not self.has_key:
            raise KalshiError(f"{method} {endpoint} needs a Kalshi API key (KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH)")
        is_write = (method != "GET") if write is None else write
        bucket = self.write_bucket if is_write else self.read_bucket
        retry_any = method in ("GET", "DELETE")          # idempotent
        attempt = 0
        while True:
            bucket.acquire(cost)
            headers = {"Content-Type": "application/json"} if json is not None else {}
            if auth:
                headers.update(auth_headers(self.key_id, self.key_path, method, self.base_path + endpoint))
            try:
                r = self.session.request(method, self.base_url + endpoint, params=params, json=json,
                                         headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                if retry_any and attempt < self.max_retries:
                    attempt += 1
                    self.sleep(self.backoff * 2 ** (attempt - 1))
                    continue
                raise KalshiError(f"{method} {endpoint}: {type(e).__name__}: {e}") from e
            if r.status_code < 400:
                return r.json() if r.content else {}
            retryable = r.status_code == 429 or (retry_any and r.status_code in RETRY_STATUS)
            if retryable and attempt < self.max_retries:
                attempt += 1
                log.debug("%s %s -> %s, retry %d", method, endpoint, r.status_code, attempt)
                self.sleep(self.backoff * 2 ** (attempt - 1))
                continue
            raise KalshiError(f"{method} {endpoint}: HTTP {r.status_code}: {r.text[:500]}", r.status_code, r.text)

    def _paged(self, endpoint: str, key: str, params: dict, *, auth: bool = False, max_pages: int = 50) -> list:
        out: list = []
        params = dict(params)
        for _ in range(max_pages):
            d = self.request("GET", endpoint, params=dict(params), auth=auth)
            items = d.get(key) or []
            out.extend(items)
            cur = d.get("cursor")
            if not cur or not items:
                break
            params["cursor"] = cur
        return out

    # -- public market data -----------------------------------------------------------------------------------
    def open_markets(self, series: str = "KXBTC15M") -> list[MarketInfo]:
        """Open markets of a series, soonest close first (markets without a strike yet are left out)."""
        raw = self._paged("/markets", "markets", {"series_ticker": series, "status": "open", "limit": 100,
                                                  "exchange_index": self.exchange_index}, max_pages=5)
        ms = [m for m in (parse_market(x) for x in raw) if m is not None]
        return sorted(ms, key=lambda m: m.close_ts)

    def current_market(self, series: str = "KXBTC15M", now: float | None = None) -> MarketInfo | None:
        now = time.time() if now is None else now
        cur = [m for m in self.open_markets(series) if m.open_ts <= now < m.close_ts]
        return cur[0] if cur else None

    def market(self, ticker: str) -> dict:
        """Raw market dict (status, result, expiration_value, floor_strike, ...). parse_market() -> MarketInfo."""
        d = self.request("GET", f"/markets/{ticker}")
        return d.get("market", d)

    def orderbook(self, ticker: str, depth: int = 0) -> Book:
        params = {"depth": depth} if depth else None
        return parse_orderbook(self.request("GET", f"/markets/{ticker}/orderbook", params=params))

    def trades_with_ids(self, ticker: str, min_ts: float | None = None, max_pages: int = 20) -> list[tuple[str, KTrade]]:
        params: dict[str, Any] = {"ticker": ticker, "limit": 1000}
        if min_ts is not None:
            params["min_ts"] = int(min_ts)
        raw = self._paged("/markets/trades", "trades", params, max_pages=max_pages)
        out = [(t.get("trade_id", ""), parse_trade(t)) for t in raw]
        out.sort(key=lambda x: x[1].ts)
        return out

    def trades(self, ticker: str, min_ts: float | None = None) -> list[KTrade]:
        """Public trades of one market, oldest first (min_ts = unix seconds, inclusive)."""
        return [t for _, t in self.trades_with_ids(ticker, min_ts)]

    # -- authenticated ----------------------------------------------------------------------------------------
    def brti(self) -> tuple[float, float]:
        """(ts, price) of CF Benchmarks BRTI via Kalshi's passthrough (50 tokens = 5 read units; needs entitlement)."""
        d = self.request("GET", "/cfbenchmarks/values", params={"id": "BRTI"}, auth=True, cost=5.0)
        return extract_brti(d.get("data", d))

    def balance(self) -> dict:
        return self.request("GET", "/portfolio/balance", params={"exchange_index": self.exchange_index}, auth=True)

    def positions(self, ticker: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"limit": 1000, "count_filter": "position"}
        if ticker:
            p["ticker"] = ticker
        return self._paged("/portfolio/positions", "market_positions", p, auth=True)

    def orders(self, status: str | None = "resting", ticker: str | None = None) -> list[dict]:
        """status: resting | canceled | executed | None (all)."""
        p: dict[str, Any] = {"limit": 1000}
        if status:
            p["status"] = status
        if ticker:
            p["ticker"] = ticker
        return self._paged("/portfolio/orders", "orders", p, auth=True)

    def fills(self, min_ts: float | None = None, ticker: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"limit": 1000}
        if min_ts is not None:
            p["min_ts"] = int(min_ts)
        if ticker:
            p["ticker"] = ticker
        return self._paged("/portfolio/fills", "fills", p, auth=True)

    def create_order(self, ticker: str, side: Side, action: Literal["buy", "sell"], qty: float, price: float,
                     **kw: Any) -> dict:
        """Place one order (V2). kw -> build_order_body (time_in_force, post_only, reduce_only, client_order_id,
        expiration_ts, ...). Returns {order_id, client_order_id, fill_count, remaining_count, ...}."""
        kw.setdefault("exchange_index", self.exchange_index)
        body = build_order_body(ticker, side, action, qty, price, **kw)
        return self.request("POST", "/portfolio/events/orders", json=body, auth=True)

    def cancel_order(self, order_id: str, ticker: str | None = None) -> dict:
        p: dict[str, Any] = {"exchange_index": self.exchange_index}
        if ticker:
            p["market_ticker"] = ticker
        return self.request("DELETE", f"/portfolio/events/orders/{order_id}", params=p, auth=True)

    def batch_cancel(self, ids: list[str], ticker: str | None = None) -> dict:
        orders = []
        for oid in ids:
            o: dict[str, Any] = {"order_id": oid, "exchange_index": self.exchange_index}
            if ticker:
                o["market_ticker"] = ticker
            orders.append(o)
        if not orders:
            return {"orders": []}
        # batch cancels bill 2 tokens per order (0.2 units)
        return self.request("DELETE", "/portfolio/events/orders/batched", json={"orders": orders}, auth=True,
                            cost=max(1.0, 0.2 * len(orders)))
