import json

import pytest

from opsagent.__main__ import main
from opsagent.agent import Agent
from opsagent.backends import FixtureBackend, KubectlBackend
from opsagent.tools import ToolRunner

POD = "model-server-5f6b7c8d9-hv4zt"


def call(name: str, call_id: str = "c1", **arguments) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class ScriptedModel:
    """Plays back assistant messages and records what it was sent."""

    def __init__(self, replies: list[dict]):
        self._replies = iter(replies)
        self.requests: list[tuple[list[dict], list[dict] | None]] = []

    def complete(self, messages, tools):
        self.requests.append(([dict(m) for m in messages], tools))
        return next(self._replies)


def make_agent(replies, scenario="crashloop-oom", **kwargs):
    model = ScriptedModel(replies)
    return Agent(model, ToolRunner(FixtureBackend(scenario)), **kwargs), model


def test_investigates_then_reports():
    agent, model = make_agent(
        [
            {"content": None, "tool_calls": [call("list_pods", namespace="llm")]},
            {
                "content": None,
                "tool_calls": [
                    call("describe_pod", "c2", namespace="llm", pod=POD),
                    call("get_logs", "c3", namespace="llm", pod=POD, previous=True),
                ],
            },
            {"content": "Summary: model-server is OOMKilled."},
        ]
    )
    result = agent.run("Why is the model server down in namespace llm?")

    assert result.report == "Summary: model-server is OOMKilled."
    assert result.stopped == "completed"
    assert [s.tool for s in result.steps] == ["list_pods", "describe_pod", "get_logs"]
    assert "OOMKilled" in result.steps[1].output
    assert "KV cache" in result.steps[2].output

    final_messages, _ = model.requests[-1]
    tool_messages = [m for m in final_messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3"]


def test_step_budget_forces_a_final_report():
    looping = {"content": None, "tool_calls": [call("list_pods", namespace="llm")]}
    agent, model = make_agent([looping, looping, {"content": "Partial report."}], max_steps=2)
    result = agent.run("triage llm")

    assert result.stopped == "max_steps"
    assert result.report == "Partial report."
    assert len(result.steps) == 2
    assert model.requests[-1][1] is None  # the final call offers no tools


def test_bad_tool_input_is_returned_to_the_model_not_raised():
    agent, _ = make_agent(
        [
            {
                "content": None,
                "tool_calls": [
                    call("list_pods", "c1", namespace="llm; rm -rf /"),
                    call("delete_pod", "c2", namespace="llm", pod=POD),
                    {
                        "id": "c3",
                        "type": "function",
                        "function": {"name": "list_pods", "arguments": "{not json"},
                    },
                    call("get_logs", "c4", namespace="llm", pod=POD, tail=100000),
                ],
            },
            {"content": "done"},
        ]
    )
    outputs = [s.output for s in agent.run("triage").steps]
    assert outputs[0] == "error: namespace must be a valid Kubernetes name"
    assert outputs[1] == "error: unknown tool 'delete_pod'"
    assert outputs[2] == "error: arguments were not a JSON object"
    assert outputs[3] == "error: tail must be an integer between 1 and 500"


def test_kubectl_backend_only_issues_read_only_argv():
    seen: list[list[str]] = []

    def runner(args: list[str]) -> str:
        seen.append(args)
        return "ok"

    tools = ToolRunner(KubectlBackend(runner=runner))
    tools.run("list_pods", {"namespace": "llm"})
    tools.run("describe_pod", {"namespace": "llm", "pod": POD})
    tools.run("get_events", {"namespace": "llm"})
    tools.run("get_logs", {"namespace": "llm", "pod": POD, "previous": True, "tail": 50})

    assert {args[0] for args in seen} == {"get", "describe", "logs"}
    assert seen[3] == ["logs", POD, "-n", "llm", "--tail=50", "--all-containers", "--previous"]
    assert tools.run("query_prometheus", {"query": "up"}).startswith("error: no Prometheus")


def test_long_output_keeps_the_tail():
    class Noisy(FixtureBackend):
        def events(self, namespace):
            return "old\n" * 5000 + "newest line"

    output = ToolRunner(Noisy("crashloop-oom")).run("get_events", {"namespace": "llm"})
    assert output.startswith("[truncated]")
    assert output.endswith("newest line")
    assert len(output) < 4100


def test_runbook_tool_is_only_offered_when_configured():
    backend = FixtureBackend("gpu-pending")
    names = lambda runner: [s["function"]["name"] for s in runner.specs]  # noqa: E731
    assert "search_runbooks" not in names(ToolRunner(backend))
    assert "search_runbooks" in names(ToolRunner(backend, "http://rag"))
    assert ToolRunner(backend).run("search_runbooks", {"question": "x"}).startswith("error")


def test_fixture_prometheus_matches_on_metric_name():
    backend = FixtureBackend("gpu-pending")
    assert '"19"' in backend.query_prometheus("sum(gateway_inflight_requests)")
    assert backend.query_prometheus("up") == "[]"


def test_unknown_scenario_lists_the_available_ones():
    with pytest.raises(ValueError, match="crashloop-oom"):
        FixtureBackend("nope")


def test_cli_rejects_unknown_scenario(capsys):
    with pytest.raises(SystemExit):
        main(["triage", "--backend", "fixture", "--scenario", "nope"])
    assert "available:" in capsys.readouterr().err
