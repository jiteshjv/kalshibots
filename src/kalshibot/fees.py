"""Kalshi trading fees, per order, rounded UP to the cent.

    taker: ceil(0.07   * n * p * (1-p) * 100) / 100
    maker: ceil(0.0175 * n * p * (1-p) * 100) / 100

The expression is evaluated in exactly the same order as the research code (`scripts/friend_v3.py:fee`,
`scripts/her_trades.py:mfee`), including the `- 1e-9` guard that stops float noise such as 7.000000000000001 from
rounding up an extra cent. Keep it that way: the parity test depends on it to the cent.
"""
from __future__ import annotations

import math

TAKER_RATE = 0.07
MAKER_RATE = 0.0175
_EPS = 1e-9


def _fee(rate: float, price: float, n: float) -> float:
    if n <= 0:
        return 0.0
    return math.ceil(rate * n * price * (1 - price) * 100 - _EPS) / 100


def taker_fee(price: float, n: float) -> float:
    """Fee in dollars for one taker order of n contracts at price (dollars)."""
    return _fee(TAKER_RATE, price, n)


def maker_fee(price: float, n: float) -> float:
    """Fee in dollars for one resting (maker) order of n contracts at price (dollars)."""
    return _fee(MAKER_RATE, price, n)


def fee(price: float, n: float, maker: bool) -> float:
    return maker_fee(price, n) if maker else taker_fee(price, n)
