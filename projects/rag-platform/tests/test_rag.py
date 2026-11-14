import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.chunking import chunk_document
from app.config import Settings
from app.embeddings import HashingEmbedder
from app.main import create_app
from app.pipeline import RagPipeline, build_prompt
from eval.run_eval import evaluate

DOC = """# Deploys

## Rollback

Run kubectl rollout undo to return to the previous ReplicaSet.

## Canary

Shift ten percent of traffic to the new version and watch error rates.
"""


def fake_llm(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert "[1]" in body["messages"][1]["content"]
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "Use rollout undo [1]."}}]}
    )


@pytest.fixture
def pipeline() -> RagPipeline:
    return RagPipeline(Settings(qdrant_url=":memory:"), llm_transport=httpx.MockTransport(fake_llm))


def test_chunks_follow_headings():
    chunks = chunk_document("deploys.md", DOC)
    assert [c.heading for c in chunks] == ["Rollback", "Canary"]
    assert chunks[0].text.startswith("Rollback\n")
    assert [c.index for c in chunks] == [0, 1]


def test_long_sections_split_with_overlap():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(8))
    chunks = chunk_document("long.md", body, max_chars=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c.text) <= 500 for c in chunks)
    assert chunks[1].text.startswith(chunks[0].text[-50:].strip())


def test_oversized_paragraph_is_hard_split():
    chunks = chunk_document("blob.md", "x" * 2000, max_chars=500, overlap=50)
    assert all(len(c.text) <= 500 for c in chunks)
    assert len(chunks) >= 4


def test_overlap_must_be_smaller_than_chunk():
    with pytest.raises(ValueError):
        chunk_document("a.md", "text", max_chars=100, overlap=100)


def test_hashing_embedder_is_deterministic_and_normalized():
    embedder = HashingEmbedder(256)
    [a], [b] = embedder.embed(["kubectl rollout undo"]), embedder.embed(["kubectl rollout undo"])
    assert a == b
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9


def test_retrieves_the_relevant_chunk(pipeline):
    pipeline.ingest("deploys.md", DOC)
    hits = pipeline.retrieve("how do I rollback with kubectl rollout undo?", top_k=1)
    assert hits[0].heading == "Rollback"


def test_reingest_replaces_instead_of_duplicating(pipeline):
    assert pipeline.ingest("deploys.md", DOC) == 2
    assert pipeline.ingest("deploys.md", "# Deploys\n\n## Rollback\n\nOnly one section now.") == 1
    assert pipeline.store.count() == 1


def test_prompt_numbers_sources(pipeline):
    pipeline.ingest("deploys.md", DOC)
    messages = build_prompt("q", pipeline.retrieve("canary traffic", top_k=2))
    assert "[1] (source: deploys.md)" in messages[1]["content"]
    assert "[2] (source: deploys.md)" in messages[1]["content"]


def test_api_round_trip(pipeline):
    client = TestClient(create_app(pipeline))
    assert client.post("/ingest", json={"source": "deploys.md", "text": DOC}).json()["chunks"] == 2
    assert client.get("/healthz").json()["chunks"] == 2

    search = client.post("/search", json={"question": "canary traffic shift", "top_k": 1}).json()
    assert search["hits"][0]["heading"] == "Canary"

    answer = client.post("/query", json={"question": "how do I rollback?"}).json()
    assert answer["answer"] == "Use rollout undo [1]."
    assert answer["sources"][0]["source"] == "deploys.md"
    assert "rag_queries_total" in client.get("/metrics").text


def test_api_validates_input_and_reports_llm_outage():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    pipeline = RagPipeline(Settings(qdrant_url=":memory:"), llm_transport=httpx.MockTransport(down))
    client = TestClient(create_app(pipeline))
    assert client.post("/query", json={"question": ""}).status_code == 422
    assert client.post("/search", json={"question": "x", "top_k": 99}).status_code == 422
    client.post("/ingest", json={"source": "deploys.md", "text": DOC})
    assert client.post("/query", json={"question": "rollback"}).status_code == 502


def test_empty_store_answers_without_calling_llm(pipeline):
    assert "do not know" in pipeline.answer("anything").text


def test_golden_set_meets_quality_bar(pipeline):
    report = evaluate(pipeline, k=3)
    assert report["recall_at_k"] >= 0.9, report["misses"]
    assert report["mrr"] >= 0.8
