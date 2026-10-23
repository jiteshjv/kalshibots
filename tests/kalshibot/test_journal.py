

def test_fractional_fills_summing_to_the_order_size_mark_it_filled(tmp_path):
    """Kalshi fractional fills: 0.1 + 0.2 + ... can sum to 9.999999999999998; the order must still count as filled."""
    from kalshibot.journal import Journal
    from kalshibot.types import Fill
    j = Journal(tmp_path)
    from kalshibot.types import OrderIntent
    j.record_order(OrderIntent(kind="market_buy", side="n", qty=10, price=0.985, lot_id="L1", client_id="c1",
                               reason="entry_market_fallback"), "T")
    for q in [0.1] * 100:
        j.record_fill(Fill(client_id="c1", side="n", action="buy", price=0.9, qty=q, fee=0.0, ts=1.0, maker=False))
    assert j.open_orders() == []
    j._db.execute("UPDATE orders SET status='partial', filled_qty=9.999999999999998")   # an old journal's ghost row
    assert len(j.open_orders()) == 1
    assert Journal(tmp_path).open_orders() == []
