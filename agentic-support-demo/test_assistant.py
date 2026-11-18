"""Offline tests: a scripted fake client replaces the LLM, so no API key is needed.

Run with:  python -m unittest test_assistant -v
"""

import unittest
from types import SimpleNamespace as NS

import assistant


def text(s):
    return NS(content=[NS(type="text", text=s)], stop_reason="end_turn")


def tool_call(name, **inputs):
    block = NS(type="tool_use", id=f"id-{name}", name=name, input=inputs)
    return NS(content=[block], stop_reason="tool_use")


class FakeClient:
    """Returns pre-scripted responses in order and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        # Snapshot messages: the agent keeps appending to the same list.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.script.pop(0)


def quiet(_):
    pass


class AssistantTests(unittest.TestCase):
    def test_faq_takes_two_plain_calls_and_no_tools(self):
        client = FakeClient([text("faq"), text("Shipping takes 3-5 business days.")])
        result = assistant.handle(client, "How long is shipping?", quiet)
        self.assertEqual(result["route"], "faq")
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all("tools" not in call for call in client.calls))

    def test_lost_package_is_refunded_by_the_agent(self):
        client = FakeClient([
            text("problem"),
            tool_call("lookup_order", order_id="A100"),
            tool_call("check_carrier", tracking_number="TRK-1"),
            tool_call("issue_refund", order_id="A100", reason="lost in transit"),
            text("Sorry about that. I've refunded $89.99."),
            text("Package lost. Refunded in full."),
        ])
        result = assistant.handle(client, "Order A100 never arrived", quiet)
        self.assertEqual([a["tool"] for a in result["actions"]],
                         ["lookup_order", "check_carrier", "issue_refund"])
        self.assertEqual(result["actions"][-1]["result"]["refunded"], 89.99)
        self.assertEqual(result["notes"], "Package lost. Refunded in full.")
        # Tool results were fed back to the model on the next turn.
        fed_back = client.calls[2]["messages"][-1]["content"][0]
        self.assertEqual(fed_back["type"], "tool_result")
        self.assertIn("TRK-1", fed_back["content"])

    def test_refund_guardrail_blocks_large_orders(self):
        client = FakeClient([
            tool_call("issue_refund", order_id="A200", reason="lost"),
            tool_call("escalate_to_human", order_id="A200", summary="Lost, over limit"),
            text("I've passed this to a specialist."),
        ])
        _, actions = assistant.run_agent(client, "Refund A200", quiet)
        self.assertIn("error", actions[0]["result"])
        self.assertTrue(actions[1]["result"]["escalated"])

    def test_bad_tool_call_becomes_an_error_result_not_a_crash(self):
        client = FakeClient([tool_call("delete_database"), text("Sorry, I can't do that.")])
        _, actions = assistant.run_agent(client, "hi", quiet)
        self.assertIn("error", actions[0]["result"])

    def test_loop_stops_at_max_steps(self):
        client = FakeClient([tool_call("lookup_order", order_id="A100")] * 50)
        reply, actions = assistant.run_agent(client, "loop forever", quiet)
        self.assertEqual(len(actions), assistant.MAX_STEPS)
        self.assertIn("passed it to our team", reply)


if __name__ == "__main__":
    unittest.main()
