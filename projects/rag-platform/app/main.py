"""HTTP API for the RAG pipeline."""

from __future__ import annotations

import time
from dataclasses import asdict

import httpx
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from .config import Settings
from .pipeline import RagPipeline

INGESTED = Counter("rag_chunks_ingested_total", "Chunks written to the vector store")
QUERIES = Counter("rag_queries_total", "Queries served", ["endpoint", "outcome"])
STAGE = Histogram("rag_stage_duration_seconds", "Latency per pipeline stage", ["stage"])
TOP_SCORE = Histogram(
    "rag_top_hit_score",
    "Similarity of the best hit; a falling trend means retrieval drift",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
)


class IngestRequest(BaseModel):
    source: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)


def create_app(pipeline: RagPipeline | None = None) -> FastAPI:
    pipeline = pipeline or RagPipeline(Settings())
    app = FastAPI(title="rag-platform")

    def timed_retrieve(request: QueryRequest):
        started = time.perf_counter()
        hits = pipeline.retrieve(request.question, request.top_k)
        STAGE.labels("retrieve").observe(time.perf_counter() - started)
        if hits:
            TOP_SCORE.observe(hits[0].score)
        return hits

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "chunks": pipeline.store.count()}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/ingest")
    def ingest(request: IngestRequest):
        started = time.perf_counter()
        count = pipeline.ingest(request.source, request.text)
        STAGE.labels("ingest").observe(time.perf_counter() - started)
        INGESTED.inc(count)
        return {"source": request.source, "chunks": count}

    @app.post("/search")
    def search(request: QueryRequest):
        """Retrieval only. No LLM call, so it is cheap and usable as an agent tool."""
        hits = timed_retrieve(request)
        QUERIES.labels("search", "ok").inc()
        return {"hits": [asdict(h) for h in hits]}

    @app.post("/query")
    def query(request: QueryRequest):
        started = time.perf_counter()
        try:
            answer = pipeline.answer(request.question, request.top_k)
        except httpx.HTTPError as exc:
            QUERIES.labels("query", "llm_error").inc()
            raise HTTPException(502, "LLM backend unavailable") from exc
        STAGE.labels("answer").observe(time.perf_counter() - started)
        QUERIES.labels("query", "ok").inc()
        return {"answer": answer.text, "sources": [asdict(h) for h in answer.sources]}

    return app
