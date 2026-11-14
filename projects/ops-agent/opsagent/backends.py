"""Where the agent's observations come from: a live cluster or recorded fixtures."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


class ClusterBackend(Protocol):
    def list_pods(self, namespace: str) -> str: ...
    def describe_pod(self, namespace: str, pod: str) -> str: ...
    def events(self, namespace: str) -> str: ...
    def logs(self, namespace: str, pod: str, previous: bool, tail: int) -> str: ...
    def query_prometheus(self, query: str) -> str: ...


def _run_kubectl(args: list[str]) -> str:
    # argv list, never a shell string: model-supplied values cannot inject commands.
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=20)
    return result.stdout if result.returncode == 0 else f"kubectl error: {result.stderr.strip()}"


class KubectlBackend:
    """Read-only access to a live cluster.

    Every method maps to a fixed, read-only kubectl verb. There is no generic
    "run kubectl" tool, so the model cannot delete, patch, or exec. Pair this
    with a read-only ClusterRole for defense in depth (see deploy/rbac.yaml).
    """

    def __init__(
        self,
        prometheus_url: str | None = None,
        runner: Callable[[list[str]], str] = _run_kubectl,
    ):
        self._prometheus_url = prometheus_url
        self._run = runner

    def list_pods(self, namespace: str) -> str:
        return self._run(["get", "pods", "-n", namespace, "-o", "wide"])

    def describe_pod(self, namespace: str, pod: str) -> str:
        return self._run(["describe", "pod", pod, "-n", namespace])

    def events(self, namespace: str) -> str:
        return self._run(["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"])

    def logs(self, namespace: str, pod: str, previous: bool, tail: int) -> str:
        args = ["logs", pod, "-n", namespace, f"--tail={tail}", "--all-containers"]
        return self._run(args + (["--previous"] if previous else []))

    def query_prometheus(self, query: str) -> str:
        if not self._prometheus_url:
            return "error: no Prometheus URL configured"
        try:
            response = httpx.get(
                f"{self._prometheus_url}/api/v1/query", params={"query": query}, timeout=15
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"error: prometheus query failed: {exc}"
        return json.dumps(response.json().get("data", {}).get("result", []))[:4000]


class FixtureBackend:
    """Replays a recorded incident, for demos and tests without a cluster."""

    def __init__(self, scenario: str):
        path = FIXTURES / f"{scenario}.json"
        if not path.is_file():
            available = ", ".join(sorted(p.stem for p in FIXTURES.glob("*.json")))
            raise ValueError(f"unknown scenario {scenario!r}; available: {available}")
        self._data = json.loads(path.read_text())

    def list_pods(self, namespace: str) -> str:
        return self._data["pods"]

    def describe_pod(self, namespace: str, pod: str) -> str:
        return self._data["describe"].get(pod, f'Error: pods "{pod}" not found')

    def events(self, namespace: str) -> str:
        return self._data["events"]

    def logs(self, namespace: str, pod: str, previous: bool, tail: int) -> str:
        key = f"{pod}:previous" if previous else pod
        return self._data["logs"].get(key, "(no log output)")

    def query_prometheus(self, query: str) -> str:
        for needle, result in self._data.get("prometheus", {}).items():
            if needle in query:
                return json.dumps(result)
        return "[]"
