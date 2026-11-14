import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.ratelimit import TokenBucketLimiter

SSE = (
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
    b"data: [DONE]\n\n"
)


def upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"data": [{"id": "tiny"}]})
    body = json.loads(request.content)
    if body.get("stream"):
        assert body["stream_options"] == {"include_usage": True}
        return httpx.Response(200, content=SSE, headers={"content-type": "text/event-stream"})
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )


def make_client(**overrides) -> TestClient:
    settings = Settings(upstream_url="http://upstream", **overrides)
    return TestClient(create_app(settings, transport=httpx.MockTransport(upstream)))


def metric(client: TestClient, name: str) -> float:
    total = 0.0
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name):
            total += float(line.rsplit(" ", 1)[1])
    return total


CHAT = {"model": "tiny", "messages": [{"role": "user", "content": "hello"}]}


def test_proxies_chat_completion_and_counts_tokens():
    with make_client() as client:
        before = metric(client, 'gateway_tokens_total{kind="completion"')
        response = client.post("/v1/chat/completions", json=CHAT)
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "hi"
        assert metric(client, 'gateway_tokens_total{kind="completion"') - before == 2


def test_streams_and_records_usage_and_ttft():
    with make_client() as client:
        before_tokens = metric(client, 'gateway_tokens_total{kind="prompt"')
        before_ttft = metric(client, "gateway_time_to_first_token_seconds_count")
        response = client.post("/v1/chat/completions", json={**CHAT, "stream": True})
        assert response.status_code == 200
        assert response.content == SSE
        assert metric(client, 'gateway_tokens_total{kind="prompt"') - before_tokens == 7
        assert metric(client, "gateway_time_to_first_token_seconds_count") - before_ttft == 1
        assert metric(client, "gateway_inflight_requests") == 0


def test_requires_api_key_when_configured():
    with make_client(api_keys="secret-a, secret-b") as client:
        assert client.post("/v1/chat/completions", json=CHAT).status_code == 401
        bad = client.post(
            "/v1/chat/completions", json=CHAT, headers={"Authorization": "Bearer nope"}
        )
        assert bad.status_code == 401
        good = client.post(
            "/v1/chat/completions", json=CHAT, headers={"Authorization": "Bearer secret-b"}
        )
        assert good.status_code == 200
        assert "secret-b" not in client.get("/metrics").text


def test_rate_limit_returns_429():
    with make_client(rate_limit_rpm=2) as client:
        codes = [client.post("/v1/chat/completions", json=CHAT).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_sheds_load_when_at_capacity():
    with make_client(max_inflight=0) as client:
        response = client.post("/v1/chat/completions", json=CHAT)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "2"


def test_rejects_unknown_route_and_bad_body():
    with make_client() as client:
        assert client.post("/v1/fine_tuning/jobs", json={}).status_code == 404
        assert client.post("/v1/chat/completions", content=b"not json").status_code == 400


def test_readiness_follows_model_server():
    with make_client() as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/models").json()["data"][0]["id"] == "tiny"


def test_upstream_failure_is_502_and_releases_slot():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    app = create_app(Settings(upstream_url="http://upstream"), httpx.MockTransport(down))
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json=CHAT).status_code == 502
        assert client.get("/readyz").status_code == 503
        assert metric(client, "gateway_inflight_requests") == 0


@pytest.mark.parametrize("elapsed,expected", [(0.0, False), (30.0, True)])
def test_token_bucket_refills_over_time(elapsed, expected):
    now = [0.0]
    limiter = TokenBucketLimiter(rate_per_min=2, clock=lambda: now[0])
    assert limiter.allow("t") and limiter.allow("t")
    now[0] = elapsed
    assert limiter.allow("t") is expected
