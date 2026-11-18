# Agentic support demo: plain LLM calls + an agent in one system

A small customer-support assistant that shows when to use a single LLM call
and when to use an agent.

```
customer message
      |
  [router]  plain LLM call: "faq" or "problem"
      |
      +-- faq -----> [answer_faq]  plain LLM call, answers from FAQ text
      |
      +-- problem -> [agent]       LLM in a loop with tools:
                        lookup_order -> check_carrier -> issue_refund
                        (or reship_order / escalate_to_human)
                         |
                     [summarize]   plain LLM call, writes case notes
```

## Why it is split this way

| Step | Type | Reason |
|---|---|---|
| Route | Single call, small model | Fixed task, one answer, cheap and fast |
| FAQ answer | Single call, small model | The path is known in advance |
| Order problem | Agent, larger model | Next step depends on what the tools return |
| Case notes | Single call, small model | Fixed task |

The agent is the only part where the model controls the flow. Everything
else is ordinary code calling the model once.

## What to look at in the code

- `run_agent()` is the whole agent: call the model, run the tools it asks
  for, feed results back, repeat until it stops asking.
- `MAX_STEPS` caps the loop so it cannot run away.
- The refund limit lives in `issue_refund()`, not in the prompt. The model
  can ask for a large refund, but the tool refuses and the agent escalates.
- A bad tool name or bad arguments comes back to the model as an error
  result instead of crashing the run.
- Router and agent models are set separately (`ROUTER_MODEL`, `AGENT_MODEL`).

## Run it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key

python assistant.py "How long does shipping take?"         # FAQ path
python assistant.py "My order A100 never arrived"          # agent refunds
python assistant.py "Order A200 never showed up"           # over limit, escalates
python assistant.py "Where is order A300?"                 # delivered, no refund
```

## Test it without an API key

The tests swap the LLM for a scripted fake client, so they check the
routing, the loop, the guardrail and the step limit offline.

```bash
python -m unittest test_assistant -v
```

The order database and carrier API are in-memory fakes in `assistant.py`.
