import asyncio

import pytest

from kalshibot.config import LIVE_CONFIRM_PHRASE, RiskCfg, Settings
from kalshibot.execution.live import LiveBroker, classify_yes_book, order_args, to_yes_book
from kalshibot.marketdata.kalshi_rest import build_order_body
from kalshibot.risk import LiveModeRefused
from kalshibot.types import MarketInfo, OrderIntent

M = MarketInfo(ticker="KXBTC15M-TEST", open_ts=1000.0, close_ts=1900.0, strike=100000.0)
ARMED = RiskCfg(mode="live", live_confirm=LIVE_CONFIRM_PHRASE)


class FakeRest:
    """Records calls; never touches the network."""

    def __init__(self):
        self.created, self.cancelled, self.batch = [], [], []
        self.resting, self.pos, self.fill_rows = [], [], []
        self.fill_calls = []
        self.n = 0
        self.ioc_fill = None            # fill_count to report for IOC replies (None = no counts)
        self.fail_create = None
        self.fail_batch = False
        self.before_reply = None        # hook(body, order_id) run before create_order returns

    def create_order(self, ticker, side, action, qty, price, **kw):
        body = build_order_body(ticker, side, action, qty, price, **kw)   # pure: same body KalshiRest would POST
        self.created.append(body)
        if self.fail_create:
            raise RuntimeError(self.fail_create)
        self.n += 1
        oid = f"X{self.n}"
        if self.before_reply:
            self.before_reply(body, oid)
        if body["time_in_force"] == "immediate_or_cancel":
            o = {"order_id": oid, "status": "executed" if self.ioc_fill == float(body["count"]) else "canceled"}
            if self.ioc_fill is not None:
                o["fill_count"] = self.ioc_fill
            return {"order": o}
        return {"order": {"order_id": oid, "status": "resting"}}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def batch_cancel(self, ids):
        if self.fail_batch:
            raise RuntimeError("boom")
        self.batch.append(list(ids))
        bad = getattr(self, "batch_errors", {})              # per-order errors inside a successful batch request
        return {"orders": [{"order_id": i, "error": bad.get(i)} for i in ids]}

    def orders(self, status):
        return {"orders": list(self.resting)}

    def positions(self):
        return {"market_positions": list(self.pos)}

    def fills(self, min_ts=None):
        self.fill_calls.append(min_ts)
        return {"fills": list(self.fill_rows)}


def mk(rest=None, **kw):
    rec = {"fills": [], "done": []}
    b = LiveBroker(rest or FakeRest(), ARMED, on_fill=rec["fills"].append,
                   on_done=lambda c, s: rec["done"].append((c, s)), inline=True, clock=lambda: 1200.0, **kw)
    return b, rec


def run(c):
    return asyncio.run(c)


def submit(b, **kw):
    run(b.submit(OrderIntent(**kw), M))


# ------------------------------------------------------------------------------------------------- refusal
@pytest.mark.parametrize("cfg", [
    RiskCfg(),                                                   # paper
    RiskCfg(mode="live"),                                        # no phrase
    RiskCfg(mode="live", live_confirm="i understand this trades real money"),
    RiskCfg(mode="paper", live_confirm=LIVE_CONFIRM_PHRASE),
])
def test_refuses_unless_armed(cfg):
    with pytest.raises(LiveModeRefused):
        LiveBroker(FakeRest(), cfg)
    with pytest.raises(LiveModeRefused):
        LiveBroker(FakeRest(), Settings(risk=cfg))


def test_accepts_armed_settings():
    LiveBroker(FakeRest(), Settings(risk=ARMED))


# ------------------------------------------------------------------------------------------- order payloads
def test_yes_book_mapping_roundtrip():
    assert to_yes_book("y", "buy", 0.95) == ("bid", 0.95)
    assert to_yes_book("n", "buy", 0.96) == ("ask", 0.04)
    assert to_yes_book("y", "sell", 0.997) == ("ask", 0.997)
    assert to_yes_book("n", "sell", 0.997) == ("bid", 0.003)
    for held in ("y", "n"):
        bs, yp = to_yes_book(held, "sell", 0.997)                 # the TP on the held side
        assert classify_yes_book(bs, yp, held) == (held, "sell", 0.997)
        bs, yp = to_yes_book(held, "buy", 0.95)                   # an entry bid on the held side
        assert classify_yes_book(bs, yp, held) == (held, "buy", 0.95)
    assert classify_yes_book("bid", 0.95, None) == ("y", "buy", 0.95)
    assert classify_yes_book("ask", 0.05, None) == ("n", "buy", 0.95)


def test_payloads_per_intent_kind():
    rest = FakeRest(); b, _ = mk(rest)
    rest.ioc_fill = 0
    b.positions[(M.ticker, "y")] = 100
    b.positions[(M.ticker, "n")] = 100
    submit(b, kind="limit_buy", side="n", qty=200, price=0.955, client_id="e1", expires_s=10)
    submit(b, kind="market_buy", side="y", qty=100, price=0.985, client_id="f1")
    submit(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp1")
    submit(b, kind="market_sell", side="n", qty=100, price=None, client_id="st1")
    e, f, tp, st = rest.created
    assert e == {"ticker": M.ticker, "client_order_id": "e1", "side": "ask", "count": "200.00", "price": "0.0450",
                 "time_in_force": "good_till_canceled", "self_trade_prevention_type": "taker_at_cross",
                 "post_only": True, "cancel_order_on_pause": True, "reduce_only": False, "exchange_index": 2}
    assert (f["side"], f["price"], f["time_in_force"], f["post_only"], f["reduce_only"]) == \
        ("bid", "0.9850", "immediate_or_cancel", False, False)
    assert (tp["side"], tp["price"], tp["time_in_force"], tp["post_only"], tp["reduce_only"]) == \
        ("ask", "0.9970", "good_till_canceled", False, False)       # Kalshi: reduce_only only on IOC
    assert (st["side"], st["price"], st["time_in_force"], st["reduce_only"]) == \
        ("bid", "0.9900", "immediate_or_cancel", True)              # sell NO, 1c floor -> YES bid at 99c
    assert b.get("e1").exchange_id == "X1" and b.get("e1").status == "resting"


def test_order_args_direct():
    from kalshibot.execution.base import OrderState
    st = OrderState(client_id="c", ticker="T", kind="market_buy", side="n", qty=5, price=None, tif="ioc")
    a = order_args(st, exchange_index=3)
    assert a == dict(ticker="T", side="n", action="buy", qty=5, price=0.99, time_in_force="immediate_or_cancel",
                     post_only=False, reduce_only=False, client_order_id="c", exchange_index=3)


def test_tp_local_reduce_only():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp0")
    assert rec["done"] == [("tp0", "rejected")] and rest.created == []
    b.positions[(M.ticker, "y")] = 300
    submit(b, kind="limit_sell", side="y", qty=200, price=0.997, client_id="tp1")
    submit(b, kind="limit_sell", side="y", qty=200, price=0.997, client_id="tp2")     # only 100 unreserved
    assert [c["count"] for c in rest.created] == ["200.00", "100.00"]
    assert b.get("tp2").qty == 100 and b.get("tp2").meta["clipped_from"] == 200
    submit(b, kind="limit_sell", side="y", qty=1, price=0.997, client_id="tp3")
    assert rec["done"][-1] == ("tp3", "rejected")


def test_bad_orders_rejected_without_sending():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=None, client_id="a")
    submit(b, kind="limit_buy", side="y", qty=0, price=0.5, client_id="b")
    submit(b, kind="limit_buy", side="y", qty=10, price=1.2, client_id="c")
    assert rest.created == [] and rec["done"] == [("a", "rejected"), ("b", "rejected"), ("c", "rejected")]


def test_idempotent_client_order_id():
    rest = FakeRest(); b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.5, client_id="a")
    submit(b, kind="limit_buy", side="y", qty=10, price=0.5, client_id="a")
    assert len(rest.created) == 1


def test_create_error_adopts_order_found_on_exchange_else_rejects():
    rest = FakeRest(); b, rec = mk(rest)
    rest.fail_create = "timeout"
    rest.resting = [{"order_id": "Z9", "client_order_id": "a", "ticker": M.ticker, "side": "bid", "price": "0.5"}]
    submit(b, kind="limit_buy", side="y", qty=10, price=0.5, client_id="a")
    assert b.get("a").status == "resting" and b.get("a").exchange_id == "Z9" and rec["done"] == []
    submit(b, kind="limit_buy", side="y", qty=10, price=0.5, client_id="b")
    assert rec["done"] == [("b", "rejected")] and "timeout" in b.get("b").meta["reject_reason"]


# --------------------------------------------------------------------------------------------------- fills
def test_ingest_fill_rest_and_ws_shapes_partial_and_dedupe():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="limit_buy", side="n", qty=200, price=0.955, client_id="e1")
    row = {"trade_id": "t1", "order_id": "X1", "ticker": M.ticker, "side": "no", "action": "buy", "count": 120,
           "yes_price": 4.5, "no_price": 95.5, "is_taker": False, "created_time": "2026-10-19T12:00:00Z"}
    assert b.ingest_fill(row) is True
    assert b.ingest_fill(row) is False                                     # dedupe
    ws = {"type": "fill", "msg": {"trade_id": "t2", "order_id": "X1", "market_ticker": M.ticker, "side": "no",
                                  "count": 80, "yes_price_dollars": "0.0450", "is_taker": False, "ts": 1792411201}}
    assert b.ingest_fill(ws) is True
    assert [(f.qty, f.price, f.maker, f.side, f.action) for f in rec["fills"]] == \
        [(120, 0.955, True, "n", "buy"), (80, 0.955, True, "n", "buy")]
    assert rec["fills"][0].ts == pytest.approx(1792411200.0)
    assert b.get("e1").status == "filled" and b.position(M.ticker, "n") == 200
    assert rec["done"] == []


def test_fill_fee_from_row_or_computed():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=100, price=0.5, client_id="e1")
    b.ingest_fill({"trade_id": "a", "order_id": "X1", "count": 50, "yes_price": 50, "fee_cost": "0.11"})
    b.ingest_fill({"trade_id": "b", "client_order_id": "e1", "count": 50, "yes_price": 50, "is_taker": False})
    assert [f.fee for f in rec["fills"]] == [0.11, 0.22]


def test_unknown_fill_kept_aside():
    b, rec = mk()
    assert b.ingest_fill({"trade_id": "q", "order_id": "NOPE", "count": 5, "yes_price": 50}) is False
    assert rec["fills"] == [] and len(b.unknown_fills) == 1


def test_fill_arriving_before_create_reply_is_replayed():
    rest = FakeRest(); b, rec = mk(rest)
    rest.ioc_fill = 100
    rest.before_reply = lambda body, oid: b.ingest_fill(
        {"trade_id": "early", "order_id": oid, "count": 100, "yes_price": 97, "is_taker": True})
    submit(b, kind="market_buy", side="y", qty=100, price=0.985, client_id="m1")
    assert [(f.qty, f.price, f.maker) for f in rec["fills"]] == [(100, 0.97, False)]
    assert b.get("m1").status == "filled" and rec["done"] == []


def test_ioc_partial_reports_cancelled_after_fills_arrive():
    rest = FakeRest(); b, rec = mk(rest)
    rest.ioc_fill = 150
    submit(b, kind="market_buy", side="y", qty=200, price=0.985, client_id="m1")
    assert rec["done"] == [] and b.get("m1").active                   # waiting for the 150 executed contracts
    rest.fill_rows = [{"trade_id": "t1", "order_id": "X1", "count": 100, "yes_price": 96, "is_taker": True,
                       "ts": 1201},
                      {"trade_id": "t2", "order_id": "X1", "count": 50, "yes_price": 97, "is_taker": True,
                       "ts": 1202}]
    assert run(b.poll_fills()) == 2
    assert rec["done"] == [("m1", "cancelled")] and b.get("m1").filled == 150
    assert rest.fill_calls == [None]
    run(b.poll_fills())
    assert rest.fill_calls[-1] == 1197 and len(rec["fills"]) == 2       # min_ts = last fill - overlap; deduped


def test_ioc_zero_fill_done_immediately():
    rest = FakeRest(); b, rec = mk(rest)
    rest.ioc_fill = 0
    submit(b, kind="market_sell", side="y", qty=100, price=0.3, client_id="s1")
    assert rec["done"] == [("s1", "cancelled")]


def test_ioc_reply_without_counts_settles_after_poll():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="market_buy", side="y", qty=100, price=0.985, client_id="m1")
    assert b.get("m1").active
    rest.fill_rows = [{"trade_id": "t1", "order_id": "X1", "count": 40, "yes_price": 96, "is_taker": True}]
    run(b.poll_fills())
    assert rec["done"] == [("m1", "cancelled")] and b.get("m1").filled == 40


# ------------------------------------------------------------------------------------------------- cancels
def test_cancel_and_cancel_intent_by_lot():
    rest = FakeRest(); b, rec = mk(rest)
    b.positions[(M.ticker, "y")] = 100
    submit(b, kind="limit_sell", side="y", qty=100, price=0.997, client_id="tp1", lot_id="L1")
    submit(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="e1")
    assert run(b.cancel("e1")) is True and rest.cancelled == ["X2"]
    assert run(b.cancel("e1")) is False
    submit(b, kind="cancel", side="y", qty=0, client_id="cx", lot_id="L1")
    assert rest.cancelled == ["X2", "X1"] and rec["done"] == [("e1", "cancelled"), ("tp1", "cancelled")]


def test_cancel_all_includes_untracked_exchange_orders():
    rest = FakeRest(); b, rec = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="e1")
    rest.resting = [{"order_id": "OLD1", "ticker": M.ticker}, {"order_id": "OTHER", "ticker": "ELSE"}]
    assert run(b.cancel_all(M.ticker)) == 1
    assert rest.batch == [["OLD1", "X1"]] and rec["done"] == [("e1", "cancelled")]
    assert run(b.shutdown()) == 0 and rest.batch[-1] == ["OLD1", "OTHER"]


def test_cancel_all_falls_back_to_single_cancels():
    rest = FakeRest(); b, _ = mk(rest)
    rest.fail_batch = True
    submit(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="e1")
    submit(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="e2")
    assert run(b.on_disconnect()) == 2 and rest.cancelled == ["X1", "X2"]


def test_expire_stale_backstop():
    rest = FakeRest(); b, rec = mk(rest, expiry_grace_s=2.0)
    submit(b, kind="limit_buy", side="y", qty=100, price=0.9, client_id="e1", expires_s=10)   # placed at 1200
    assert run(b.expire_stale(1211.0)) == []
    assert run(b.expire_stale(1212.0)) == ["e1"] and rest.cancelled == ["X1"]
    assert rec["done"] == [("e1", "expired")]


# ------------------------------------------------------------------------------------------------ reconcile
def test_reconcile_positions_and_resting_orders():
    rest = FakeRest(); b, rec = mk(rest)
    rest.pos = [{"ticker": M.ticker, "position": -300, "market_exposure": 28800, "fees_paid": 120},
                {"ticker": "ELSE", "position": 50}]
    rest.resting = [
        {"order_id": "R1", "client_order_id": "kb-tp-1", "ticker": M.ticker, "side": "bid", "price": "0.0030",
         "remaining_count": 300},
        {"order_id": "R2", "client_order_id": "kb-e-9", "ticker": M.ticker, "side": "ask", "yes_price": 5,
         "remaining_count": 100},
        {"order_id": "R3", "ticker": "ELSE", "side": "bid", "price": "0.5"},
    ]
    lots = run(b.reconcile(M))
    assert lots == [dict(lot_id=f"recon-{M.ticker}-n", ticker=M.ticker, side="n", qty=300, buy=0.96, fee_in=1.2,
                         stop=None, entry_ts=None, how="reconciled", tp_client_id="kb-tp-1")]
    tp, e = b.get("kb-tp-1"), b.get("kb-e-9")
    assert (tp.kind, tp.side, tp.price, tp.qty, tp.status) == ("limit_sell", "n", 0.997, 300, "resting")
    assert (e.kind, e.side, e.price, e.qty) == ("limit_buy", "n", 0.95, 100)
    assert b.get("ext-R3") is None and b.position(M.ticker, "n") == 300
    # adopted orders are live: their fills and cancels work
    b.ingest_fill({"trade_id": "f", "order_id": "R1", "count": 300, "yes_price": 0.3, "is_taker": False})
    assert rec["fills"][-1].price == pytest.approx(0.997) and rec["fills"][-1].action == "sell"
    assert b.position(M.ticker, "n") == 0
    assert run(b.cancel("kb-e-9")) and rest.cancelled == ["R2"]


def test_no_side_tp_listed_as_yes_buy_is_kept():
    # Kalshi lists "sell NO at 99.7c" as side=yes action=buy yes_price 0.003 (seen on demo, Oct 21): holding NO it is
    # our take-profit, not a stray buy -- restart must adopt it and shutdown must not cancel it
    r = FakeRest()
    r.pos = [{"ticker": M.ticker, "position_fp": "-3.00"}]
    tp = {"order_id": "E1", "client_order_id": "kb-tp-n", "ticker": M.ticker, "side": "yes", "action": "buy",
          "yes_price_dollars": "0.0030", "remaining_count_fp": "3.00", "status": "resting"}
    entry = {"order_id": "E2", "client_order_id": "kb-bid-n", "ticker": M.ticker, "side": "yes", "action": "sell",
             "yes_price_dollars": "0.0650", "remaining_count_fp": "1.00", "status": "resting"}
    r.resting = [tp, entry]
    b, _ = mk(r)
    lots = run(b.reconcile(M))
    assert lots[0]["side"] == "n" and lots[0]["qty"] == 3 and lots[0]["tp_client_id"] == "kb-tp-n"
    o = b.orders["kb-tp-n"]
    assert (o.kind, o.side, o.price, o.qty) == ("limit_sell", "n", 0.997, 3)
    e = b.orders["kb-bid-n"]
    assert (e.kind, e.side, e.price) == ("limit_buy", "n", 0.935)
    b2, _ = mk(r)                                   # a fresh process shutting down: only the entry bid goes
    run(b2.cancel_entries())
    assert r.cancelled == ["E2"]


def test_shutdown_keeps_inflight_market_buy_and_records_its_fill():
    # demo, Oct 21 02:11: a fallback market buy sent 1 s before SIGTERM was marked cancelled by the shutdown and its
    # fill never read -> journal 1 contract, Kalshi 2. Shutdown must leave IOC orders alone and wait for their result.
    rest = FakeRest()                                 # IOC replies without counts: the result comes from the fills
    b, rec = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=1, price=0.955, client_id="bid", expires_s=10)
    submit(b, kind="market_buy", side="y", qty=1, price=0.985, client_id="mkt")
    run(b.cancel_entries())
    sent = rest.cancelled + [i for bt in rest.batch for i in bt]
    assert sent == ["X1"]                              # only the resting bid (shutdown sends it in one batch)
    assert b.get("mkt").active and ("mkt", "cancelled") not in rec["done"]
    rest.fill_rows = [{"trade_id": "t9", "order_id": "X2", "count": 1, "yes_price": 96, "is_taker": True, "ts": 1201}]
    assert run(b.settle_inflight(timeout_s=1, every_s=0)) == 0
    assert b.get("mkt").status == "filled" and b.position(M.ticker, "y") == 1 and len(rec["fills"]) == 1


def test_settle_inflight_unfilled_stop_ends_cancelled():
    rest = FakeRest()
    b, rec = mk(rest)
    b.positions[(M.ticker, "y")] = 1
    submit(b, kind="market_sell", side="y", qty=1, client_id="stop")
    assert b.get("stop").active
    assert run(b.settle_inflight(timeout_s=1, every_s=0)) == 0           # no fill came back: not executed
    assert rec["done"] == [("stop", "cancelled")] and b.position(M.ticker, "y") == 1


def test_fractional_fill_is_kept():
    # demo, Oct 21 05:55: 0.23 of a 1-contract NO bid filled; it used to be rounded to 0 and lost (Kalshi -2.23 vs 2)
    rest = FakeRest()
    b, rec = mk(rest)
    submit(b, kind="limit_buy", side="n", qty=1, price=0.945, client_id="bid", expires_s=10)
    rest.fill_rows = [{"trade_id": "f1", "order_id": "X1", "count_fp": "0.23", "yes_price_dollars": "0.0550",
                       "is_taker": False, "ts": 1201}]
    run(b.poll_fills())
    o = b.get("bid")
    assert o.filled == 0.23 and o.remaining == 0.77 and b.position(M.ticker, "n") == 0.23 and rec["fills"][0].qty == 0.23
    submit(b, kind="limit_sell", side="n", qty=1, price=0.997, client_id="tp")      # TP clipped to what is held
    assert b.get("tp").qty == 0.23 and rest.created[-1]["count"] == "0.23"



def test_order_event_log_records_send_ack_and_cancel():
    b, _ = mk()
    ev = []
    b.on_event = lambda k, d: ev.append((k, d))
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="c1", lot_id="L1", reason="entry")
    run(b.cancel("c1"))
    assert [k for k, _ in ev] == ["send", "ack", "cancel"]
    assert ev[0][1]["cid"] == "c1" and ev[1][1]["ms"] == 0.0 and ev[2][1]["err"] is None
    b.on_event = lambda k, d: 1 / 0                    # a broken log never stops an order
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="c2", lot_id="L2", reason="entry")
    assert b.orders["c2"].exchange_id


def test_cancel_while_create_in_flight_cancels_on_the_exchange_once_it_answers():
    rest = FakeRest()
    b, _ = mk(rest)

    def cancel_now(body, oid):                         # a cancel arrives before Kalshi has answered the create
        coro = b.cancel("e1")
        try:
            coro.send(None)
        except StopIteration:
            pass
    rest.before_reply = cancel_now
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    st = b.orders["e1"]
    assert st.status == "cancelled" and st.exchange_id == "X1"
    assert rest.cancelled == ["X1"]                    # cancelled on Kalshi too, not only locally
    rest.before_reply = None
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e2", lot_id="L2", reason="entry")
    assert rest.cancelled == ["X1"] and b.orders["e2"].status == "resting"   # normal orders unaffected


def test_cancel_in_flight_then_create_times_out_still_cancels_on_the_exchange():
    rest = FakeRest()
    b, _ = mk(rest)
    real_create = rest.create_order

    def create_then_time_out(*a, **kw):                # Kalshi accepts the order, but the reply is lost (timeout)
        reply = real_create(*a, **kw)
        o = reply["order"]
        rest.resting.append({"order_id": o["order_id"], "client_order_id": "e1", "status": "resting",
                             "ticker": a[0], "side": "yes", "action": "buy", "yes_price_dollars": "0.9500",
                             "remaining_count_fp": "10.00", "fill_count_fp": "0.00"})
        coro = b.cancel("e1")                          # ... and a cancel came in while the request was out
        try:
            coro.send(None)
        except StopIteration:
            pass
        raise TimeoutError("read timed out")
    rest.create_order = create_then_time_out
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    st = b.orders["e1"]
    assert st.status == "cancelled" and st.exchange_id == "X1"
    assert rest.cancelled == ["X1"]                    # found by the timeout recovery AND cancelled on Kalshi


def test_late_cancel_retries_when_kalshi_refuses():
    rest = FakeRest()
    b, _ = mk(rest)
    fails = {"n": 2}
    real_cancel = rest.cancel_order

    def flaky_cancel(oid):
        if fails["n"]:
            fails["n"] -= 1
            raise RuntimeError("503")
        real_cancel(oid)
    rest.cancel_order = flaky_cancel

    def cancel_now(body, oid):
        coro = b.cancel("e1")
        try:
            coro.send(None)
        except StopIteration:
            pass
    rest.before_reply = cancel_now
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    assert rest.cancelled == ["X1"] and b.orders["e1"].meta.get("late_cancel_done")


def test_failed_late_cancel_stays_pending_until_kalshi_confirms():
    rest = FakeRest()
    b, _ = mk(rest)
    down = {"on": True}
    real_cancel = rest.cancel_order

    def cancel(oid):
        if down["on"]:
            raise RuntimeError("connection reset")
        real_cancel(oid)
    rest.cancel_order = cancel

    def cancel_now(body, oid):
        coro = b.cancel("e1")
        try:
            coro.send(None)
        except StopIteration:
            pass
    rest.before_reply = cancel_now
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    assert b.pending_cancels == {"X1": "e1"} and rest.cancelled == []     # 3 tries failed: kept, not forgotten
    assert run(b.retry_pending_cancels()) == 1                           # still down: still pending
    down["on"] = False
    run(b.cancel_entries())                                              # connectivity back: shutdown retries it
    assert rest.cancelled + [i for bt in rest.batch for i in bt] == ["X1"] and b.pending_cancels == {}


def test_pending_cancel_resolved_when_kalshi_no_longer_knows_the_order():
    rest = FakeRest()
    b, _ = mk(rest)
    b.pending_cancels["X9"] = "e9"
    def gone(oid):
        raise RuntimeError("404 not_found")
    rest.cancel_order = gone
    assert run(b.retry_pending_cancels()) == 0


def test_ordinary_cancel_timeout_stays_pending():
    rest = FakeRest()
    b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    def timeout(oid):
        raise TimeoutError("read timed out")
    rest.cancel_order = timeout
    run(b.cancel("e1"))                                  # the entry bid expires; its cancel times out
    assert b.pending_cancels == {"X1": "e1"}             # kept: the bid may still be live, so no fallback buy yet
    rest.cancel_order = lambda oid: rest.cancelled.append(oid)
    assert run(b.retry_pending_cancels()) == 0 and rest.cancelled == ["X1"]


def test_cancel_of_an_order_kalshi_no_longer_has_is_not_pending():
    rest = FakeRest()
    b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    def gone(oid):
        raise RuntimeError("404 not_found")
    rest.cancel_order = gone
    run(b.cancel("e1"))
    assert b.pending_cancels == {}


def test_shutdown_cancels_all_entry_bids_in_one_batch_first():
    rest = FakeRest()
    b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    submit(b, kind="limit_buy", side="y", qty=10, price=0.94, client_id="e2", lot_id="L2", reason="entry")
    b.pending_cancels["X9"] = "old"                      # an older cancel Kalshi never confirmed
    def stalled(oid):                                    # single cancels hang (degraded exchange)
        raise TimeoutError("read timed out")
    rest.cancel_order = stalled
    run(b.cancel_entries())
    assert rest.batch and sorted(rest.batch[0]) == ["X1", "X2", "X9"]   # one request covered everything
    assert b.open_orders() == [] and b.pending_cancels == {}


def test_batch_cancel_with_one_failed_order_keeps_that_bid_open_and_retried():
    rest = FakeRest()
    b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    submit(b, kind="limit_buy", side="y", qty=10, price=0.94, client_id="e2", lot_id="L2", reason="entry")
    rest.batch_errors = {"X2": {"code": "internal_error", "message": "try again"}}
    def single_fails(oid):
        raise TimeoutError("read timed out")
    rest.cancel_order = single_fails
    run(b.cancel_entries())
    assert b.pending_cancels == {"X2": "e2"}             # X1 confirmed; X2 not -> kept and retried, never forgotten
    rest.cancel_order = lambda oid: rest.cancelled.append(oid)
    assert run(b.retry_pending_cancels()) == 0 and rest.cancelled == ["X2"]


def test_cancel_all_keeps_unconfirmed_batch_results_pending():
    rest = FakeRest()
    b, _ = mk(rest)
    submit(b, kind="limit_buy", side="y", qty=10, price=0.95, client_id="e1", lot_id="L1", reason="entry")
    rest.batch_errors = {"X1": "exchange busy"}
    run(b.cancel_all())
    assert b.pending_cancels == {"X1": "e1"}
