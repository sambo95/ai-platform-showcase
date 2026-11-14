"""OpenAI-compatible gateway that sits in front of a model server (llama.cpp or vLLM).

It adds what a raw model server lacks for multi-tenant use: API-key auth,
per-tenant rate limiting, load shedding, and inference-specific metrics
(time to first token, token throughput, in-flight requests).
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .config import Settings
from .ratelimit import TokenBucketLimiter

PROXIED_ROUTES = frozenset({"chat/completions", "completions", "embeddings"})
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)

REQUESTS = Counter("gateway_requests_total", "Proxied requests", ["route", "status", "tenant"])
REJECTED = Counter("gateway_rejected_total", "Requests rejected before proxying", ["reason"])
DURATION = Histogram(
    "gateway_request_duration_seconds", "End-to-end latency", ["route"], buckets=LATENCY_BUCKETS
)
TTFT = Histogram(
    "gateway_time_to_first_token_seconds",
    "Time until the first streamed chunk",
    ["model"],
    buckets=LATENCY_BUCKETS,
)
TOKENS = Counter("gateway_tokens_total", "Tokens processed", ["model", "kind"])
INFLIGHT = Gauge("gateway_inflight_requests", "Requests currently being served")


def _tenant_id(api_key: str) -> str:
    """Stable, non-reversible label so raw keys never reach metrics or logs."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:8]


def _usage_from_sse(line: bytes) -> dict | None:
    if not line.startswith(b"data:") or b'"usage"' not in line:
        return None
    try:
        usage = json.loads(line[5:].strip()).get("usage")
    except (ValueError, AttributeError):
        return None
    return usage if isinstance(usage, dict) else None


def _record_usage(model: str, usage: dict | None) -> None:
    if not usage:
        return
    TOKENS.labels(model, "prompt").inc(usage.get("prompt_tokens") or 0)
    TOKENS.labels(model, "completion").inc(usage.get("completion_tokens") or 0)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()
    limiter = TokenBucketLimiter(settings.rate_limit_rpm)
    inflight = 0

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            base_url=settings.upstream_url,
            timeout=settings.request_timeout_s,
            transport=transport,
        )
        yield
        await app.state.client.aclose()

    app = FastAPI(title="llm-gateway", lifespan=lifespan)

    def authenticate(request: Request) -> str:
        if not settings.key_set:
            return "anonymous"
        scheme, _, key = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or key not in settings.key_set:
            REJECTED.labels("unauthorized").inc()
            raise HTTPException(401, "invalid or missing API key")
        return _tenant_id(key)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request):
        """Ready only when the model server is: keeps traffic off pods still loading weights."""
        try:
            upstream = await request.app.state.client.get("/health", timeout=3.0)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "model server unreachable") from exc
        if upstream.status_code != 200:
            raise HTTPException(503, "model server not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def models(request: Request):
        authenticate(request)
        try:
            upstream = await request.app.state.client.get("/v1/models")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "model server unavailable") from exc
        return Response(upstream.content, upstream.status_code, media_type="application/json")

    @app.post("/v1/{route:path}")
    async def proxy(route: str, request: Request):
        nonlocal inflight
        if route not in PROXIED_ROUTES:
            raise HTTPException(404, "unknown route")
        tenant = authenticate(request)
        if not limiter.allow(tenant):
            REJECTED.labels("rate_limited").inc()
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "1"})
        if inflight >= settings.max_inflight:
            REJECTED.labels("overloaded").inc()
            raise HTTPException(429, "gateway at capacity", headers={"Retry-After": "2"})
        try:
            payload = json.loads(await request.body())
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be a JSON object")

        model = str(payload.get("model", "unknown"))[:64]
        stream = bool(payload.get("stream"))
        if stream:
            # Ask the model server to append token usage to the stream so
            # throughput metrics work for streamed responses too.
            payload.setdefault("stream_options", {"include_usage": True})

        started = time.perf_counter()
        inflight += 1
        INFLIGHT.inc()

        def finish(status: int, usage: dict | None) -> None:
            nonlocal inflight
            inflight -= 1
            INFLIGHT.dec()
            REQUESTS.labels(route, str(status), tenant).inc()
            DURATION.labels(route).observe(time.perf_counter() - started)
            _record_usage(model, usage)

        client: httpx.AsyncClient = request.app.state.client
        try:
            upstream = await client.send(
                client.build_request("POST", f"/v1/{route}", json=payload), stream=True
            )
        except httpx.HTTPError as exc:
            finish(502, None)
            raise HTTPException(502, "model server unavailable") from exc

        if not stream:
            try:
                body = await upstream.aread()
            except httpx.HTTPError as exc:
                finish(502, None)
                raise HTTPException(502, "model server dropped the response") from exc
            finally:
                await upstream.aclose()
            usage = None
            if upstream.status_code == 200:
                try:
                    usage = json.loads(body).get("usage")
                except (ValueError, AttributeError):
                    usage = None
            finish(upstream.status_code, usage)
            return Response(
                body,
                upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        async def relay():
            usage, buffer, first = None, b"", True
            try:
                async for chunk in upstream.aiter_bytes():
                    if first:
                        TTFT.labels(model).observe(time.perf_counter() - started)
                        first = False
                    *lines, buffer = (buffer + chunk).split(b"\n")
                    for line in lines:
                        usage = _usage_from_sse(line) or usage
                    yield chunk
            finally:
                await upstream.aclose()
                finish(upstream.status_code, usage or _usage_from_sse(buffer))

        return StreamingResponse(
            relay(), status_code=upstream.status_code, media_type="text/event-stream"
        )

    return app
