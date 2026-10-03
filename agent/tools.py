"""Tool schemas the model reads, and the execution layer that actually runs them.

The model never executes anything. It emits a request; this module validates it,
retries transient failures, attaches idempotency keys to writes, and always
returns an explicit result or an explicit error, never an empty string.
"""
from __future__ import annotations

import json

from .backends import Backends, ServiceUnavailable
from .reliability import call_with_retry

SCHEMAS = [
    {
        "name": "order_lookup",
        "description": "Get details for one order. Use whenever the customer asks about order "
                       "status, delivery, tracking, or whether an order can be returned. Returns item, "
                       "category, total, delivered_days_ago, defective flag, and tracking number.",
        "input_schema": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Numeric order ID, for example 7841"}},
            "required": ["order_id"]},
    },
    {
        "name": "knowledge_search",
        "description": "Search return, refund, and shipping policies. Use before stating any policy. "
                       "Never quote a policy that this tool did not return.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look up, in plain words"}},
            "required": ["query"]},
    },
    {
        "name": "refund_process",
        "description": "Issue a refund. Use only after order_lookup and knowledge_search have "
                       "confirmed the order is inside the return window.",
        "input_schema": {"type": "object", "properties": {
            "order_id": {"type": "string"},
            "amount": {"type": "number", "description": "Refund amount in USD"},
            "reason": {"type": "string", "enum": ["defective", "changed_mind", "not_delivered"]}},
            "required": ["order_id", "amount", "reason"]},
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a person. Use when the request is out of scope, the "
                       "customer asks for a human, a refund exceeds the approval limit, legal action "
                       "is mentioned, or the issue is unresolved after three tool calls.",
        "input_schema": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "One-paragraph handoff note"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]}},
            "required": ["summary", "severity"]},
    },
]

WRITE_TOOLS = {"refund_process"}


def execute(name: str, args: dict, backends: Backends, idempotency_key: str, sleep=None) -> dict:
    """Run one tool call. Every path returns a dict the model can reason about."""
    kw = {"sleep": sleep} if sleep else {}
    try:
        if name == "order_lookup":
            return call_with_retry(backends.lookup_order, str(args["order_id"]), **kw)
        if name == "knowledge_search":
            hits = backends.search_knowledge(args["query"])
            return {"results": hits} if hits else {"results": [], "note": "no policy matched; do not guess"}
        if name == "refund_process":
            return call_with_retry(backends.refunds.refund, str(args["order_id"]), float(args["amount"]),
                                   args["reason"], idempotency_key, **kw)
        if name == "escalate_to_human":
            return {"status": "queued", "eta": "about 2 hours"}
        return {"error": f"unknown tool {name}"}
    except KeyError as e:
        return {"error": f"missing argument {e}"}
    except ServiceUnavailable as e:
        return {"error": f"{name} failed after 3 attempts: {e}"}


def as_content(result: dict) -> str:
    return json.dumps(result)
