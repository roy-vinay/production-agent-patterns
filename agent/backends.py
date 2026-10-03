"""In-memory stand-ins for the systems a support agent talks to.

They behave like real services in the ways that matter for testing:
they can time out, they enforce idempotency on writes, and they return
structured data rather than prose.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

TODAY = dt.date(2026, 10, 2)


@dataclass
class Order:
    order_id: str
    customer_id: str
    item: str
    category: str
    total: float
    delivered: dt.date
    defective: bool = False
    tracking: str = ""


ORDERS: dict[str, Order] = {
    "7841": Order("7841", "C-1001", "Wireless headphones", "electronics", 45.00,
                  TODAY - dt.timedelta(days=6), defective=True, tracking="FX789012"),
    "4821": Order("4821", "C-1001", "USB-C hub", "electronics", 29.00,
                  TODAY - dt.timedelta(days=2), tracking="FX445511"),
    "9300": Order("9300", "C-1002", "Laptop", "electronics", 899.00,
                  TODAY - dt.timedelta(days=10)),
    "5150": Order("5150", "C-1002", "Blender", "kitchen", 120.00,
                  TODAY - dt.timedelta(days=60)),
}

POLICIES = [
    {"id": "returns-electronics", "text": "Electronics may be returned within 30 days of delivery "
     "for a full refund. Defective items are always eligible within the window."},
    {"id": "returns-kitchen", "text": "Kitchen appliances may be returned within 14 days of delivery."},
    {"id": "refund-approval", "text": "Refunds up to $50 are processed automatically. "
     "Larger refunds require approval by a support lead, usually within two hours."},
    {"id": "shipping", "text": "Standard shipping takes 3 to 5 business days. Tracking numbers "
     "are emailed when an order ships."},
]


class ServiceUnavailable(Exception):
    """Raised by a backend that is down or timing out."""


@dataclass
class RefundService:
    processed: dict[str, dict] = field(default_factory=dict)
    total_paid: float = 0.0
    fail_next: int = 0           # simulate outages before the refund happens
    lose_response_next: int = 0  # simulate the refund succeeding but the response timing out

    def refund(self, order_id: str, amount: float, reason: str, idempotency_key: str) -> dict:
        if self.fail_next:
            self.fail_next -= 1
            raise ServiceUnavailable("payment service timed out")
        if idempotency_key in self.processed:  # retry of a refund that already happened
            return {**self.processed[idempotency_key], "replayed": True}
        self.total_paid += amount
        result = {"status": "refunded", "order_id": order_id, "amount": amount, "reason": reason}
        self.processed[idempotency_key] = result
        if self.lose_response_next:
            self.lose_response_next -= 1
            raise ServiceUnavailable("response lost after refund was applied")
        return result


@dataclass
class Backends:
    refunds: RefundService = field(default_factory=RefundService)
    orders_down: bool = False

    def lookup_order(self, order_id: str) -> dict:
        if self.orders_down:
            raise ServiceUnavailable("order API timed out")
        o = ORDERS.get(order_id)
        if not o:
            return {"error": f"order {order_id} not found"}
        return {"order_id": o.order_id, "customer_id": o.customer_id, "item": o.item,
                "category": o.category, "total": o.total, "defective": o.defective,
                "delivered_days_ago": (TODAY - o.delivered).days, "tracking": o.tracking}

    def search_knowledge(self, query: str, top_k: int = 2, floor: float = 0.2) -> list[dict]:
        """Keyword-overlap retrieval with a relevance floor, a stand-in for hybrid search."""
        q = set(query.lower().split())
        scored = []
        for p in POLICIES:
            words = set(p["text"].lower().replace(".", "").split()) | set(p["id"].split("-"))
            score = len(q & words) / max(len(q), 1)
            if score >= floor:
                scored.append({**p, "score": round(score, 2)})
        return sorted(scored, key=lambda d: -d["score"])[:top_k]
