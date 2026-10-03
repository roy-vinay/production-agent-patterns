"""Model adapters.

ScriptedModel is a deterministic stand-in that reasons the way the article
describes (look up data, check policy, then act). It lets the loop, guards,
and evals run offline in CI. AnthropicModel calls a real model with the same
tool schemas when ANTHROPIC_API_KEY is set.

Messages use one internal shape:
  {"role": "user" | "assistant" | "tool", "content": str,
   "tool_calls": [{"id", "name", "args"}],      # assistant only
   "tool_call_id": str, "name": str}            # tool only
"""
from __future__ import annotations

import json
import os
import re
from itertools import count

_ids = count(1)


def _call(name: str, **args) -> dict:
    return {"id": f"call_{next(_ids)}", "name": name, "args": args}


def _reply(text: str, *calls) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": list(calls), "tokens": 120 + len(text) // 4}


class ScriptedModel:
    """Rule-based policy that mimics a well-prompted support agent."""

    def __call__(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[last_user]["content"].lower()
        turn = messages[last_user + 1:]
        results = {m["name"]: json.loads(m["content"]) for m in turn if m["role"] == "tool"}

        history = " ".join(m["content"] for m in messages if m["role"] == "user")
        ids = re.findall(r"\b(\d{4})\b", history)
        order_id = ids[-1] if ids else None
        asks_window = "window" in question or "how long" in question
        wants_refund = not asks_window and any(
            w in question for w in ("refund", "return", "money back", "broken", "don't work"))

        if "human" in question or "lawyer" in question:
            if "escalate_to_human" in results:
                return _reply("I've passed this to a teammate. Expect a reply within about 2 hours.")
            return _reply("", _call("escalate_to_human", summary=question, severity="medium"))

        if not order_id:
            if "policy" in question or "window" in question or "shipping" in question:
                if "knowledge_search" not in results:
                    return _reply("", _call("knowledge_search", query=question))
                hits = results["knowledge_search"]["results"]
                return _reply(hits[0]["text"] if hits else "I don't have that policy on hand. "
                              "Let me connect you with a teammate.")
            return _reply("Happy to help. Which order is this about? The order number is in your "
                          "confirmation email.")

        if "order_lookup" not in results:
            return _reply("", _call("order_lookup", order_id=order_id))
        order = results["order_lookup"]
        if "error" in order:
            if "not found" in order["error"]:
                return _reply(f"I couldn't find order {order_id}. Could you double-check the number?")
            return _reply("", _call("escalate_to_human", severity="medium",
                                    summary=f"Order lookup failing for {order_id}: {order['error']}")) \
                if "escalate_to_human" not in results else \
                _reply("I'm having trouble reaching our order system, so I've asked a teammate to "
                       "follow up within about 2 hours.")

        if not wants_refund and not asks_window:
            return _reply(f"Order {order_id} ({order['item']}) was delivered {order['delivered_days_ago']} "
                          f"days ago. Tracking number: {order['tracking'] or 'not available'}.")

        if "knowledge_search" not in results:
            return _reply("", _call("knowledge_search", query=f"return policy {order['category']} refund"))
        policy = " ".join(h["text"] for h in results["knowledge_search"]["results"])
        window = int(m.group(1)) if (m := re.search(r"within (\d+) days", policy)) else None
        if window is None:
            return _reply("I can't confirm the return policy right now, so I've asked a teammate to check.")
        if asks_window:
            left = window - order["delivered_days_ago"]
            return _reply(f"Your {order['item']} can be returned for {max(left, 0)} more days "
                          f"({window}-day window).")
        if order["delivered_days_ago"] > window:
            return _reply(f"Order {order_id} was delivered {order['delivered_days_ago']} days ago, outside "
                          f"the {window}-day return window, so it isn't eligible for a refund.")

        if "refund_process" not in results:
            reason = "defective" if order["defective"] else "changed_mind"
            return _reply("", _call("refund_process", order_id=order_id, amount=order["total"], reason=reason))
        refund = results["refund_process"]
        if refund.get("status") == "pending_approval":
            return _reply(f"Your ${order['total']:.2f} refund is eligible. Because it's above our automatic "
                          f"limit, a support lead will confirm it within about 2 hours.")
        if refund.get("status") == "refunded":
            return _reply(f"Done. Your ${refund['amount']:.2f} refund for order {order_id} has been started "
                          f"and should appear in 3 to 5 business days.")
        return _reply("I couldn't complete the refund, so I've flagged it for a teammate to finish.")


class AnthropicModel:
    """Same interface, real model. Requires `pip install anthropic` and ANTHROPIC_API_KEY."""

    def __init__(self, model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")

    def __call__(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        api_msgs = []
        for m in messages:
            if m["role"] == "user":
                api_msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = [{"type": "text", "text": m["content"]}] if m["content"] else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                           for c in m["tool_calls"]]
                api_msgs.append({"role": "assistant", "content": blocks})
            else:
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if api_msgs and api_msgs[-1]["role"] == "user" and isinstance(api_msgs[-1]["content"], list):
                    api_msgs[-1]["content"].append(block)
                else:
                    api_msgs.append({"role": "user", "content": [block]})
        resp = self.client.messages.create(model=self.model, max_tokens=800, temperature=0,
                                           system=system, tools=tools, messages=api_msgs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": b.input} for b in resp.content if b.type == "tool_use"]
        return {"role": "assistant", "content": text, "tool_calls": calls,
                "tokens": resp.usage.input_tokens + resp.usage.output_tokens}


def default_model():
    return AnthropicModel() if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AGENT_LIVE") else ScriptedModel()
