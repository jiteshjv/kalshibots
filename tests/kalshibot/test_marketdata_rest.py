"""KalshiRest: YES-book order mapping, payload builder, signing, rate limits, retries, parsing (all offline)."""
import base64
import json
import subprocess
from pathlib import Path

import pytest
import requests

from kalshibot.marketdata.kalshi_rest import (KalshiError, KalshiRest, TokenBucket, auth_headers, build_order_body,
                                              extract_brti, from_yes_book, parse_orderbook, yes_book_order)

FIX = Path(__file__).parent / "fixtures" / "marketdata"


def fx(name):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------- YES-book mapping
@pytest.mark.parametrize("side,action,price,book_side,yes_px", [
    ("y", "buy", 0.93, "bid", 0.93),
    ("y", "sell", 0.997, "ask", 0.997),
    ("n", "buy", 0.93, "ask", 0.07),       # buy NO at 93c == sell YES at 7c
    ("n", "sell", 0.997, "bid", 0.003),    # sell NO at 99.7c == buy YES at 0.3c
    ("n", "buy", 0.985, "ask", 0.015),     # market buy cap 98.5c -> YES ask floor 1.5c
])
def test_yes_book_order_table(side, action, price, book_side, yes_px):
    o = yes_book_order(side, action, price)
    assert o.book_side == book_side
    assert o.yes_price == pytest.approx(yes_px, abs=1e-9)
    # inverse gives back what we asked for
    assert from_yes_book(o.book_side, o.yes_price, side) == (action, pytest.approx(price, abs=1e-9))


@pytest.mark.parametrize("bad", [("x", "buy", 0.5), ("y", "hold", 0.5), ("y", "buy", 0.0), ("n", "sell", 1.0)])
def test_yes_book_order_rejects(bad):
    with pytest.raises(ValueError):
        yes_book_order(*bad)


def test_build_order_body_shapes():
    b = build_order_body("KXBTC15M-X", "n", "buy", 200, 0.925, client_order_id="kb-1", post_only=True)
    assert b == {"ticker": "KXBTC15M-X", "client_order_id": "kb-1", "side": "ask", "count": "200.00",
                 "price": "0.0750", "time_in_force": "good_till_canceled",
                 "self_trade_prevention_type": "taker_at_cross", "post_only": True, "cancel_order_on_pause": True,
                 "reduce_only": False, "exchange_index": 2}
    ioc = build_order_body("T", "y", "sell", 100, 0.7, time_in_force="immediate_or_cancel", reduce_only=True)
    assert ioc["side"] == "ask" and ioc["price"] == "0.7000" and ioc["reduce_only"] is True
    exp = build_order_body("T", "y", "buy", 1, 0.5, expiration_ts=1792432210)
    assert exp["expiration_time"] == 1792432210
    assert len(build_order_body("T", "y", "buy", 1, 0.5)["client_order_id"]) == 36   # uuid default


@pytest.mark.parametrize("kw", [dict(reduce_only=True), dict(time_in_force="immediate_or_cancel", expiration_ts=5),
                                dict(time_in_force="immediate_or_cancel", post_only=True), dict(time_in_force="gtc")])
def test_build_order_body_rejects(kw):
    with pytest.raises(ValueError):
        build_order_body("T", "y", "buy", 1, 0.5, **kw)


# ---------------------------------------------------------------- signing
@pytest.fixture(scope="module")
def rsa_key(tmp_path_factory):
    d = tmp_path_factory.mktemp("key")
    key, pub = d / "k.pem", d / "pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
    return key, pub


def test_auth_headers_verify_with_openssl(rsa_key, tmp_path):
    key, pub = rsa_key
    h = auth_headers("kid", key, "get", "/trade-api/v2/portfolio/balance?exchange_index=2", now_ms=1700000000000)
    assert h["KALSHI-ACCESS-KEY"] == "kid" and h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    msg, sig = tmp_path / "m", tmp_path / "s"
    msg.write_bytes(b"1700000000000GET/trade-api/v2/portfolio/balance")      # query string NOT signed
    sig.write_bytes(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]))
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-sigopt", "rsa_padding_mode:pss",
                        "-sigopt", "rsa_pss_saltlen:digest", "-signature", str(sig), str(msg)], capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- token bucket
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_token_bucket_rate():
    c = FakeClock()
    b = TokenBucket(8, 8, clock=c, sleep=c.sleep)
    for _ in range(8):
        assert b.acquire() == 0.0          # burst of capacity
    for _ in range(16):
        b.acquire()
    assert c.t == pytest.approx(2.0)      # then 8/s
    b.acquire(5)                           # BRTI costs 5 units
    assert c.t == pytest.approx(2.625)


# ---------------------------------------------------------------- request / retries
class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(dict(method=method, url=url, params=params, json=json, headers=headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(responses, **kw):
    s = FakeSession(responses)
    return KalshiRest(session=s, sleep=lambda _: None, **kw), s


def test_get_retries_429_and_5xx_then_succeeds():
    r, s = make([Resp(429, {"error": "too many requests"}), Resp(503, {}), requests.ConnectionError("x"),
                 Resp(200, fx("rest_orderbook.json"))])
    b = r.orderbook("T")
    assert len(s.calls) == 4
    assert b.yes_bids == [(0.92, 40.0), (0.90, 120.0), (0.01, 500.0)]          # sorted, zero level dropped
    assert b.no_bids == [(0.06, 75.5), (0.05, 300.0), (0.02, 1000.0)]
    assert b.best_ask("y") == 0.94 and b.best_bid("y") == 0.92 and b.spread("y") == 0.02
    assert b.best_ask("n") == 0.08


def test_get_gives_up_after_max_retries():
    r, s = make([Resp(500, {})] * 3, max_retries=2)
    with pytest.raises(KalshiError) as e:
        r.market("T")
    assert e.value.status == 500 and len(s.calls) == 3


def test_post_order_not_retried_on_5xx_but_on_429(rsa_key):
    key, _ = rsa_key
    r, s = make([Resp(429, {}), Resp(500, {"error": "boom"})], key_id="kid", key_path=key)
    with pytest.raises(KalshiError):
        r.create_order("T", "y", "buy", 100, 0.9, client_order_id="c1")
    assert len(s.calls) == 2                       # one retry for 429, none for 500
    c = s.calls[-1]
    assert c["method"] == "POST" and c["url"].endswith("/trade-api/v2/portfolio/events/orders")
    assert c["json"]["side"] == "bid" and c["json"]["client_order_id"] == "c1"
    assert c["headers"]["KALSHI-ACCESS-KEY"] == "kid" and "KALSHI-ACCESS-SIGNATURE" in c["headers"]


def test_auth_required_without_key():
    r, s = make([])
    assert not r.has_key
    for call in (r.balance, r.brti, lambda: r.cancel_order("o1"), lambda: r.create_order("T", "y", "buy", 1, .5)):
        with pytest.raises(KalshiError):
            call()
    assert s.calls == []


def test_cancel_and_batch_cancel_paths(rsa_key):
    key, _ = rsa_key
    r, s = make([Resp(200, {"order_id": "o1", "reduced_by": "10.00"}), Resp(200, {"orders": []})],
                key_id="kid", key_path=key)
    r.cancel_order("o1", ticker="T")
    r.batch_cancel(["o1", "o2"], ticker="T")
    assert s.calls[0]["method"] == "DELETE" and s.calls[0]["url"].endswith("/portfolio/events/orders/o1")
    assert s.calls[0]["params"] == {"exchange_index": 2, "market_ticker": "T"}
    assert s.calls[1]["url"].endswith("/portfolio/events/orders/batched")
    assert s.calls[1]["json"] == {"orders": [{"order_id": "o1", "exchange_index": 2, "market_ticker": "T"},
                                             {"order_id": "o2", "exchange_index": 2, "market_ticker": "T"}]}


def test_trades_paginate_and_parse():
    r, s = make([Resp(200, fx("rest_trades_page1.json")), Resp(200, fx("rest_trades_page2.json"))])
    tr = r.trades("KXBTC15M-26OCT191345-45", min_ts=1792432200.7)
    assert s.calls[0]["params"] == {"ticker": "KXBTC15M-26OCT191345-45", "limit": 1000, "min_ts": 1792432200}
    assert s.calls[1]["params"]["cursor"] == "abc"
    assert [t.taker_side for t in tr] == ["y", "n", "y"]              # oldest first
    assert tr[1].price == 0.08 and tr[1].qty == 5.5                   # NO taker paid the NO price
    assert tr[2].price == 0.93 and tr[2].ts == pytest.approx(1792432202.512)


def test_open_markets_skips_unstruck_and_parses():
    r, _ = make([Resp(200, fx("rest_markets.json"))])
    ms = r.open_markets("KXBTC15M")
    assert len(ms) == 1 and ms[0].strike == 108123.45 and ms[0].close_ts - ms[0].open_ts == 900


def test_parse_orderbook_empty_sides():
    b = parse_orderbook({"orderbook_fp": {"yes_dollars": [], "no_dollars": None}})
    assert b.best_ask("y") is None and b.best_bid("n") is None


def test_extract_brti_shapes():
    assert extract_brti({"serverTime": "2026-10-19T17:50:00Z", "payload": [{"value": "108000.5", "time": 1792432200000}]}) \
        == (1792432200.0, 108000.5)
    ts, p = extract_brti({"serverTime": "2026-10-19T17:50:00Z", "payload": {"value": 1}})
    assert p == 1.0 and ts == pytest.approx(1792432200.0)
