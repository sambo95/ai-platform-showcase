"""Tool schemas the model sees, and the validated dispatch behind them."""

from __future__ import annotations

import json
import re

import httpx

from .backends import ClusterBackend

K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
MAX_OUTPUT_CHARS = 4000


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


NAMESPACE = {"type": "string", "description": "Kubernetes namespace"}
POD = {"type": "string", "description": "Exact pod name from list_pods"}

TOOL_SPECS = [
    _tool(
        "list_pods",
        "List pods with status and restart counts. Start here.",
        {"namespace": NAMESPACE},
        ["namespace"],
    ),
    _tool(
        "describe_pod",
        "Show why a pod is unhealthy: container state, last termination reason (for example "
        "OOMKilled) and exit code, resource limits and pod events. Call this for every pod that "
        "is not Running and Ready.",
        {"namespace": NAMESPACE, "pod": POD},
        ["namespace", "pod"],
    ),
    _tool(
        "get_events",
        "List recent events in a namespace, oldest first.",
        {"namespace": NAMESPACE},
        ["namespace"],
    ),
    _tool(
        "get_logs",
        "Fetch recent container logs for a pod.",
        {
            "namespace": NAMESPACE,
            "pod": POD,
            "previous": {
                "type": "boolean",
                "description": "Logs of the previous, crashed container instance",
            },
            "tail": {"type": "integer", "description": "Lines to return (1-500, default 100)"},
        },
        ["namespace", "pod"],
    ),
    _tool(
        "query_prometheus",
        "Run an instant PromQL query, for example "
        'container_memory_working_set_bytes{pod="POD_NAME"}.',
        {"query": {"type": "string", "description": "PromQL expression"}},
        ["query"],
    ),
    _tool(
        "search_runbooks",
        "Search the team's runbooks for known fixes. Ask about the symptom you observed (status, "
        "termination reason, exit code, error text), not the pod name.",
        {"question": {"type": "string", "description": "What you want to know"}},
        ["question"],
    ),
]


class ToolError(ValueError):
    """Bad arguments from the model. Reported back so it can correct itself."""


def _name(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not K8S_NAME.match(value):
        raise ToolError(f"{key} must be a valid Kubernetes name")
    return value


def _format_prometheus(query: str, raw: str) -> str:
    """Turn Prometheus' [timestamp, value] JSON into one readable line per series.

    Models misread the raw form: the timestamp looks like the measurement.
    """
    try:
        series = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(series, list):
        return raw
    if not series:
        return "no data: the query matched no series"
    lines = []
    for item in series[:20]:
        labels = ", ".join(f'{k}="{v}"' for k, v in sorted(item.get("metric", {}).items()))
        value = str(item.get("value", [None, "?"])[1])
        human = ""
        if "bytes" in query:
            try:
                human = f" ({float(value) / 2**30:.2f} GiB)"
            except ValueError:
                pass
        lines.append(f"{{{labels}}} = {value}{human}")
    return "\n".join(lines)


class ToolRunner:
    def __init__(self, backend: ClusterBackend, runbook_url: str | None = None):
        self._backend = backend
        self._runbook_url = runbook_url

    @property
    def specs(self) -> list[dict]:
        if self._runbook_url:
            return TOOL_SPECS
        return [s for s in TOOL_SPECS if s["function"]["name"] != "search_runbooks"]

    def run(self, name: str, args: dict) -> str:
        try:
            output = self._dispatch(name, args)
        except ToolError as exc:
            return f"error: {exc}"
        if len(output) > MAX_OUTPUT_CHARS:
            # Keep the tail: in logs and events the newest lines matter most.
            output = "[truncated]\n" + output[-MAX_OUTPUT_CHARS:]
        return output or "(empty)"

    def _dispatch(self, name: str, args: dict) -> str:
        backend = self._backend
        if name == "list_pods":
            return backend.list_pods(_name(args, "namespace"))
        if name == "describe_pod":
            return backend.describe_pod(_name(args, "namespace"), _name(args, "pod"))
        if name == "get_events":
            return backend.events(_name(args, "namespace"))
        if name == "get_logs":
            tail = args.get("tail", 100)
            if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= 500:
                raise ToolError("tail must be an integer between 1 and 500")
            return backend.logs(
                _name(args, "namespace"), _name(args, "pod"), bool(args.get("previous")), tail
            )
        if name == "query_prometheus":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > 1000:
                raise ToolError("query must be a non-empty PromQL string under 1000 chars")
            return _format_prometheus(query, backend.query_prometheus(query))
        if name == "search_runbooks" and self._runbook_url:
            return self._search_runbooks(args.get("question"))
        raise ToolError(f"unknown tool {name!r}")

    def _search_runbooks(self, question: object) -> str:
        if not isinstance(question, str) or not question.strip():
            raise ToolError("question must be a non-empty string")
        try:
            response = httpx.post(
                f"{self._runbook_url}/search",
                json={"question": question[:1000], "top_k": 3},
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"error: runbook search failed: {exc}"
        hits = response.json().get("hits", [])
        return "\n\n".join(f"[{h['source']}] {h['text']}" for h in hits) or "no runbook matched"
