"""Customer-support assistant that mixes plain LLM calls with an agent.

Flow for each incoming message:
  1. route()       plain LLM call  -> "faq" or "problem"
  2. answer_faq()  plain LLM call  -> answer from the FAQ, done
     run_agent()   agent           -> LLM in a loop with tools until resolved
  3. summarize()   plain LLM call  -> case notes

Steps 1, 2 (FAQ) and 3 have a path known in advance, so one call each is
enough. Only the "problem" branch needs an agent, because what to do next
depends on what the tools return.

Usage:
    export ANTHROPIC_API_KEY=...
    python assistant.py "My order A100 never arrived"
"""

import json
import os
import sys

ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "claude-haiku-5-5")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 8            # hard stop so the agent loop can never run away
REFUND_LIMIT = 200.00    # guardrail enforced in code, not in the prompt

# --- Fake backend (stands in for an order database and a carrier API) -------

ORDERS = {
    "A100": {"item": "Wireless headphones", "total": 89.99, "tracking": "TRK-1"},
    "A200": {"item": "Standing desk", "total": 549.00, "tracking": "TRK-2"},
    "A300": {"item": "USB-C cable", "total": 12.50, "tracking": "TRK-3"},
}
CARRIER = {
    "TRK-1": {"status": "lost", "detail": "No scans for 14 days"},
    "TRK-2": {"status": "lost", "detail": "Damaged in transit, discarded"},
    "TRK-3": {"status": "delivered", "detail": "Left at front door 2 days ago"},
}
FAQ = """\
- Shipping takes 3-5 business days within the US.
- Returns are accepted within 30 days of delivery, unused, in original packaging.
- Support hours are 9am-6pm Eastern, Monday to Friday.
- We ship to the US and Canada only.
"""

# --- Tools the agent can call ------------------------------------------------


def lookup_order(order_id):
    order = ORDERS.get(order_id.upper())
    return order or {"error": f"No order found with id {order_id}"}


def check_carrier(tracking_number):
    return CARRIER.get(tracking_number, {"error": "Unknown tracking number"})


def issue_refund(order_id, reason):
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"No order found with id {order_id}"}
    if order["total"] > REFUND_LIMIT:
        return {"error": f"Refunds over ${REFUND_LIMIT:.0f} need a human. Escalate instead."}
    return {"refunded": order["total"], "order_id": order_id.upper(), "reason": reason}


def reship_order(order_id):
    if order_id.upper() not in ORDERS:
        return {"error": f"No order found with id {order_id}"}
    return {"reshipped": True, "order_id": order_id.upper(), "eta_days": 4}


def escalate_to_human(order_id, summary):
    return {"escalated": True, "ticket": f"ESC-{order_id.upper()}", "summary": summary}


TOOL_FUNCS = {
    "lookup_order": lookup_order,
    "check_carrier": check_carrier,
    "issue_refund": issue_refund,
    "reship_order": reship_order,
    "escalate_to_human": escalate_to_human,
}


def _tool(name, description, **props):
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
            "required": list(props),
        },
    }


TOOLS = [
    _tool("lookup_order", "Get an order's item, total and tracking number.",
          order_id="Order id, e.g. A100"),
    _tool("check_carrier", "Get the carrier's delivery status for a tracking number.",
          tracking_number="Tracking number from the order"),
    _tool("issue_refund", "Refund an order in full. Fails above the refund limit.",
          order_id="Order id", reason="Why the refund is being issued"),
    _tool("reship_order", "Send a free replacement for an order.",
          order_id="Order id"),
    _tool("escalate_to_human", "Hand the case to a human agent.",
          order_id="Order id", summary="What happened and what is needed"),
]

AGENT_SYSTEM = (
    "You are a customer-support agent. Investigate before acting: look up the "
    "order, then check the carrier. If the package is lost, refund it; if the "
    "refund tool refuses, escalate to a human. If it was delivered, tell the "
    "customer where and do not refund. If no order id is given, ask for it. "
    "Finish with a short, friendly reply to the customer."
)

# --- LLM helpers -------------------------------------------------------------


def _text(response):
    return "".join(b.text for b in response.content if b.type == "text").strip()


def route(client, message):
    """Plain LLM call: classify the message. No tools, no loop."""
    response = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=10,
        system=(
            "Classify the customer message. Reply with exactly one word: "
            "'faq' for a general question about policies, shipping or hours, "
            "or 'problem' for an issue with a specific order."
        ),
        messages=[{"role": "user", "content": message}],
    )
    return "faq" if "faq" in _text(response).lower() else "problem"


def answer_faq(client, message):
    """Plain LLM call: answer from the FAQ text placed in the prompt."""
    response = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=300,
        system=(
            "Answer the customer using only this FAQ. If the answer is not "
            f"there, say you will pass the question to the team.\n\n{FAQ}"
        ),
        messages=[{"role": "user", "content": message}],
    )
    return _text(response)


def run_agent(client, message, log=print):
    """Agent: the LLM decides which tool to call next until it is done.

    Returns (reply_to_customer, list_of_actions_taken).
    """
    messages = [{"role": "user", "content": message}]
    actions = []
    for _ in range(MAX_STEPS):
        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=1024,
            system=AGENT_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return _text(response), actions  # the model chose to stop

        # The model asked for tools: run them and feed the results back in.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = TOOL_FUNCS[block.name](**block.input)
            except Exception as exc:  # bad tool name or arguments from the model
                result = {"error": str(exc)}
            log(f"  [tool] {block.name}({json.dumps(block.input)}) -> {json.dumps(result)}")
            actions.append({"tool": block.name, "input": block.input, "result": result})
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": results})

    return "I couldn't resolve this automatically, so I've passed it to our team.", actions


def summarize(client, message, reply, actions):
    """Plain LLM call: write case notes for the ticket system."""
    response = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=200,
        system="Write two-sentence internal case notes: the issue, then the resolution.",
        messages=[{
            "role": "user",
            "content": f"Customer: {message}\nActions: {json.dumps(actions)}\nReply sent: {reply}",
        }],
    )
    return _text(response)


def handle(client, message, log=print):
    """Full pipeline for one customer message."""
    kind = route(client, message)
    log(f"[router] {kind}")
    if kind == "faq":
        return {"route": kind, "reply": answer_faq(client, message), "actions": [], "notes": None}
    reply, actions = run_agent(client, message, log)
    notes = summarize(client, message, reply, actions)
    return {"route": kind, "reply": reply, "actions": actions, "notes": notes}


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python assistant.py "customer message"')
    import anthropic  # imported here so the tests run without the package

    result = handle(anthropic.Anthropic(), " ".join(sys.argv[1:]))
    print(f"\nReply to customer:\n{result['reply']}")
    if result["notes"]:
        print(f"\nCase notes:\n{result['notes']}")


if __name__ == "__main__":
    main()
