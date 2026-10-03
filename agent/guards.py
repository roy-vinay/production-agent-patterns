"""Security layers. The model decides what to do; this code decides whether it is allowed."""
from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION_PATTERNS = [
    r"ignore (all |your |the )?(previous |prior )?(instructions|rules)",
    r"you are now",
    r"new instructions",
    r"system prompt",
]

CANARY = "cnry-7f3a9c"  # hidden in the system prompt; must never appear in output


def screen_input(text: str) -> bool:
    """True when the message matches a known injection pattern."""
    t = text.lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


PII = [
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[card redacted]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn redacted]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email redacted]"),
]


def filter_output(text: str) -> str:
    """Last checkpoint before the customer sees a reply."""
    if CANARY in text:
        return "Sorry, I can't share that. I can help with orders, returns, and product questions."
    for pattern, repl in PII:
        text = pattern.sub(repl, text)
    return text


POLICY = {
    "order_lookup": {"allow": True},
    "knowledge_search": {"allow": True},
    "escalate_to_human": {"allow": True},
    "refund_process": {"allow": True, "auto_limit": 50.0, "requires": ["order_lookup"],
                       "blocked_reasons": ["fraud", "chargeback"]},
    "account_delete": {"allow": False},
}


@dataclass
class Decision:
    verdict: str  # "allow" | "deny" | "needs_human"
    reason: str = ""


def authorize(tool: str, args: dict, tools_used: list[str], customer_id: str | None = None,
              order_owner: str | None = None) -> Decision:
    rule = POLICY.get(tool)
    if not rule or not rule["allow"]:
        return Decision("deny", f"{tool} is not permitted")
    for needed in rule.get("requires", []):
        if needed not in tools_used:
            return Decision("deny", f"{tool} requires {needed} first")
    if tool == "refund_process":
        if customer_id and order_owner and customer_id != order_owner:
            return Decision("deny", "order belongs to another customer")
        if args.get("reason") in rule["blocked_reasons"]:
            return Decision("deny", "reason requires manual review")
        if float(args.get("amount", 0)) > rule["auto_limit"]:
            return Decision("needs_human", f"amount exceeds ${rule['auto_limit']:.0f} auto-approval limit")
    return Decision("allow")
