"""The agent loop: let the model investigate with tools, then report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .tools import ToolRunner

SYSTEM_PROMPT = """You are an on-call SRE assistant triaging a Kubernetes incident.

You have read-only tools. Investigate before concluding: list pods, then look at
the unhealthy ones (describe, events, previous logs), and check metrics or
runbooks when they would confirm or rule out a cause. Base every claim on tool
output you actually saw. You cannot change the cluster; never say you did.

When you have enough evidence, stop calling tools and reply with:

Summary: one sentence on what is wrong.
Evidence: the specific observations that support it.
Root cause: the most likely cause, and how confident you are.
Recommended fix: concrete next steps for a human to take.
"""


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """Return the assistant message for an OpenAI-style chat request."""


class OpenAIChat:
    """Any OpenAI-compatible endpoint that supports tool calling."""

    def __init__(self, url: str, model: str, api_key: str = ""):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=url, headers=headers, timeout=120)
        self._model = model

    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        payload: dict = {"model": self._model, "messages": messages, "temperature": 0}
        if tools:
            payload["tools"] = tools
        response = self._client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]


@dataclass(frozen=True)
class Step:
    tool: str
    arguments: dict
    output: str


@dataclass
class Result:
    report: str
    steps: list[Step] = field(default_factory=list)
    stopped: str = "completed"


class Agent:
    def __init__(self, model: ChatModel, tools: ToolRunner, max_steps: int = 8):
        self._model = model
        self._tools = tools
        self._max_steps = max_steps

    def run(self, task: str) -> Result:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        result = Result(report="")

        # The step budget bounds cost and guarantees termination if the model loops.
        for _ in range(self._max_steps):
            reply = self._model.complete(messages, self._tools.specs)
            calls = reply.get("tool_calls") or []
            if not calls:
                result.report = reply.get("content") or ""
                return result
            messages.append(
                {"role": "assistant", "content": reply.get("content"), "tool_calls": calls}
            )
            for call in calls:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError
                    output = self._tools.run(name, arguments)
                except ValueError:
                    arguments, output = {}, "error: arguments were not a JSON object"
                # Every tool call is recorded: the trace is the audit log.
                result.steps.append(Step(name, arguments, output))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        messages.append(
            {
                "role": "user",
                "content": "Tool budget exhausted. Give your report now from the evidence so far, "
                "and say what you could not verify.",
            }
        )
        result.report = self._model.complete(messages, None).get("content") or ""
        result.stopped = "max_steps"
        return result
