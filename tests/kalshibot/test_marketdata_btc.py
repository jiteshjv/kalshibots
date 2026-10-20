"""BtcFeed: source parsers, composite weighting/renormalisation/staleness, fallback basis, BRTI, bias, seeding."""
import json
import math
from pathlib import Path

import pytest

from kalshibot.marketdata.btc import (SOURCES, BiasTracker, BtcFeed, load_weights, parse_bitstamp, parse_coinbase,
                                      parse_gemini, parse_kraken)

FIX = Path(__file__).parent / "fixtures" / "marketdata"
MSG = json.loads((FIX / "btc_messages.json").read_text())
W = {"w_cb": 0.6532100767557303, "w_kr": 0.2508347008574827, "w_bn": 0.095955222386787, "offset": 3.252103672177359}


class StubHistory:
    def __init__(self):
        self.secs, self.bars = [], []

    def add(self, ts, price):
        self.secs.append((ts, price))

    def add_minute_bar(self, minute_ts, high, low, close):
        self.bars.append((minute_ts, high, low, close))


def feed(**kw):
    return BtcFeed(weights=W, clock=lambda: 1000.0, **kw)


def test_research_weights_file():
    w = load_weights()
    assert w["w_cb"] == pytest.approx(0.653, abs=1e-3) and w["w_kr"] == pytest.approx(0.251, abs=1e-3)


@pytest.mark.parametrize("src,parse,want", [("cb", parse_coinbase, 108100.50), ("kr", parse_kraken, 108091.2),
                                            ("bs", parse_bitstamp, 108120.0), ("gm", parse_gemini, 108111.11)])
def test_parsers(src, parse, want):
    a, b = MSG[src]
    assert parse(a) is None
    assert parse(b) == pytest.approx(want)
    assert SOURCES[src].parse is parse


def test_composite_both_primaries():
    f = feed()
    f.update("cb", 100000.0, 999.0)
    f.update("kr", 100010.0, 999.0)
    want = (W["w_cb"] * 100000 + W["w_kr"] * 100010) / (W["w_cb"] + W["w_kr"]) + W["offset"]
    assert f.composite(1000.0) == pytest.approx(want)
    t = f.tick(1000.0)
    assert t.source == "composite" and not t.stale and t.price == pytest.approx(want) and t.brti is None


def test_composite_renormalises_over_fresh_sources():
    f = feed()
    f.update("cb", 100000.0, 990.0)       # 10 s old: fresh (cb max age 15 s)
    f.update("kr", 100050.0, 960.0)       # 40 s old: stale (kr max age 30 s)
    assert f.composite(1000.0) == pytest.approx(100000.0 + W["offset"])
    f2 = feed()
    f2.update("cb", 100000.0, 980.0)      # stale
    f2.update("kr", 100050.0, 990.0)
    assert f2.composite(1000.0) == pytest.approx(100050.0 + W["offset"])


def test_fallback_only_when_primaries_stale_with_basis():
    f = feed()
    for s in range(0, 600):                 # 10 min with everyone fresh: bitstamp trades $20 above, gemini $-10
        now = 1000.0 + s
        f.update("cb", 100000.0, now)
        f.update("kr", 100000.0, now)
        f.update("bs", 100020.0, now)
        f.update("gm", 99990.0, now)
        t = f.tick(now)
        assert t.price == pytest.approx(100000.0 + W["offset"])      # fallback sources have zero weight
    assert f.basis["bs"] == pytest.approx(20.0) and f.basis["gm"] == pytest.approx(-10.0)
    now = 1700.0                           # Coinbase + Kraken silent for 100 s; fallbacks still trading higher
    f.update("bs", 100120.0, now)
    f.update("gm", 100090.0, now)
    t = f.tick(now)
    assert not t.stale and t.price == pytest.approx(100100.0 + W["offset"])


def test_stale_when_nothing_fresh_keeps_last_price():
    f = feed()
    t0 = f.tick(1000.0)
    assert t0.stale and math.isnan(t0.price)
    f.update("cb", 100000.0, 1000.0)
    assert not f.tick(1000.0).stale
    t = f.tick(1100.0)
    assert t.stale and t.composite is None and t.price == pytest.approx(100000.0 + W["offset"])
    assert f.latest() is t


def test_brti_preferred_when_fresh():
    f = feed()
    f.update("cb", 100000.0, 1000.0)
    f.set_brti(998.0, 100005.0)
    t = f.tick(1000.0)
    assert t.source == "brti" and t.price == 100005.0 and t.composite == pytest.approx(100000.0 + W["offset"])
    t = f.tick(1004.0)                     # BRTI 6 s old > brti_max_age 5 s -> composite
    assert t.source == "composite" and t.brti is None


def test_history_gets_composite_each_live_tick():
    h = StubHistory()
    f = feed(history=h)
    f.update("cb", 100000.0, 1000.0)
    f.set_brti(1000.0, 100500.0)
    f.tick(1000.4)
    f.tick(2000.0)                         # stale -> not added
    assert h.secs == [(1000, pytest.approx(100000.0 + W["offset"]))]


def test_bias_tracker_median_and_hook():
    b = BiasTracker(min_n=4)
    assert b.value() == 0.0
    for k, err in enumerate([5.0, 1.0, 3.0, 100.0, 2.0]):
        close = 10_000 + 900 * k
        for s in range(60):
            b.observe(close - 60 + s, 50_000.0)
        assert b.settle(close, 50_000.0 + err) == pytest.approx(err)
    assert b.value() == pytest.approx(3.0)            # median, robust to the 100 outlier
    assert b.settle(99_999_999, 1.0) is None          # no samples for that window
    f = feed(bias=b)
    f.update("cb", 100000.0, 1000.0)
    assert f.composite(1000.0) == pytest.approx(100003.0 + W["offset"])
    assert f.raw_composite(1000.0) == pytest.approx(100000.0 + W["offset"])
    f2 = feed(bias=lambda: -2.5)
    f2.update("cb", 100000.0, 1000.0)
    assert f2.composite(1000.0) == pytest.approx(99997.5 + W["offset"])


def test_seed_history_paginates_300_per_request():
    now = 1_792_400_000.0                  # arbitrary; minute-aligned below
    calls = []

    def get(url, params):
        from datetime import datetime
        s = int(datetime.strptime(params["start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=__import__("datetime").timezone.utc).timestamp())
        e = int(datetime.strptime(params["end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=__import__("datetime").timezone.utc).timestamp())
        calls.append((s, e))
        assert url.endswith("/products/BTC-USD/candles") and params["granularity"] == 60
        assert (e - s) // 60 + 1 <= 300
        # Coinbase: newest first, [time, low, high, open, close, volume]; one missing minute per page
        return [[t, 99.0, 101.0, 100.0, 100.5, 1.0] for t in range(e, s - 1, -60) if t != s + 60]

    h = StubHistory()
    n = feed().seed_history(h, days=3, get=get, now=now, pause=0)
    assert len(calls) == math.ceil(3 * 1440 / 300) == 15
    for (s1, e1), (s2, e2) in zip(calls, calls[1:]):
        assert e2 == s1 - 60                                        # contiguous, no overlap
    last = int(now // 60) * 60 - 60
    assert calls[0][1] == last and calls[-1][0] == last - 3 * 86400 + 60
    assert n == len(h.bars) == 3 * 1440 - 15
    ts = [b[0] for b in h.bars]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    assert h.bars[0][1:] == pytest.approx((101.0 + W["offset"], 99.0 + W["offset"], 100.5 + W["offset"]))
