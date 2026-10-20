"""Execution: Broker protocol, PaperBroker (simulated on the live book), LiveBroker (real orders, armed only)."""
from .base import Broker, BrokerBase, OrderState, maker_fee, order_fee, taker_fee
from .paper import PaperBroker


def __getattr__(name):  # LiveBroker is imported lazily so paper mode never touches it
    if name in ("LiveBroker", "build_order_body", "to_yes_book"):
        from . import live
        return getattr(live, name)
    raise AttributeError(name)


__all__ = ["Broker", "BrokerBase", "OrderState", "PaperBroker", "LiveBroker", "maker_fee", "taker_fee", "order_fee",
           "build_order_body", "to_yes_book"]
