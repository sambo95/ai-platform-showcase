"""Command line entry point.

    python -m opsagent "Why is the model server failing in namespace llm?" \
        --backend fixture --scenario crashloop-oom --llm-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

import httpx

from .agent import Agent, OpenAIChat
from .backends import FixtureBackend, KubectlBackend
from .tools import ToolRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsagent", description="Kubernetes incident triage")
    parser.add_argument("task", help="What to investigate, including the namespace")
    parser.add_argument("--backend", choices=["kubectl", "fixture"], default="kubectl")
    parser.add_argument("--scenario", default="crashloop-oom", help="Fixture scenario name")
    parser.add_argument(
        "--llm-url", default=os.environ.get("OPSAGENT_LLM_URL", "http://localhost:8000")
    )
    parser.add_argument("--model", default=os.environ.get("OPSAGENT_MODEL", "default"))
    parser.add_argument("--prometheus-url", default=os.environ.get("OPSAGENT_PROMETHEUS_URL"))
    parser.add_argument(
        "--runbook-url",
        default=os.environ.get("OPSAGENT_RUNBOOK_URL"),
        help="Base URL of the rag-platform service",
    )
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="Emit the report and trace as JSON")
    args = parser.parse_args(argv)

    try:
        backend = (
            FixtureBackend(args.scenario)
            if args.backend == "fixture"
            else KubectlBackend(args.prometheus_url)
        )
    except ValueError as exc:
        parser.error(str(exc))

    model = OpenAIChat(args.llm_url, args.model, os.environ.get("OPSAGENT_API_KEY", ""))
    agent = Agent(model, ToolRunner(backend, args.runbook_url), args.max_steps)
    try:
        result = agent.run(args.task)
    except httpx.HTTPError as exc:
        print(f"LLM request failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(result), indent=2))
        return 0
    for number, step in enumerate(result.steps, start=1):
        print(f"[{number}] {step.tool}({json.dumps(step.arguments)})", file=sys.stderr)
    if result.stopped != "completed":
        print(f"(stopped: {result.stopped})", file=sys.stderr)
    print(result.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
