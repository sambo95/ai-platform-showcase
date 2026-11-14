"""Ingest, retrieve, and generate grounded answers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .chunking import chunk_document
from .config import Settings
from .embeddings import Embedder, HashingEmbedder, OpenAIEmbedder
from .store import Hit, VectorStore

SYSTEM_PROMPT = (
    "You are a support engineer. Answer the question in two to four complete sentences, "
    "using only the numbered context passages. After each fact, add the number of the "
    "passage it came from in brackets, like [1]. "
    "If the context does not contain the answer, say you do not know."
)


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[Hit]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "hashing":
        return HashingEmbedder(settings.embedding_dim)
    if settings.embedder == "openai":
        return OpenAIEmbedder(
            settings.embedding_url,
            settings.embedding_model,
            settings.embedding_dim,
            settings.llm_api_key,
        )
    raise ValueError(f"unknown embedder: {settings.embedder}")


def build_prompt(question: str, hits: list[Hit]) -> list[dict]:
    context = "\n\n".join(
        f"[{i}] (source: {hit.source})\n{hit.text}" for i, hit in enumerate(hits, start=1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer in full sentences:"},
    ]


class RagPipeline:
    def __init__(
        self,
        settings: Settings,
        embedder: Embedder | None = None,
        store: VectorStore | None = None,
        llm_transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.embedder = embedder or build_embedder(settings)
        self.store = store or VectorStore(
            settings.qdrant_url, settings.collection, self.embedder.dim
        )
        headers = (
            {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        )
        self._llm = httpx.Client(
            base_url=settings.llm_url, headers=headers, timeout=120, transport=llm_transport
        )

    def ingest(self, source: str, text: str) -> int:
        chunks = chunk_document(
            source, text, self.settings.chunk_chars, self.settings.chunk_overlap
        )
        vectors = self.embedder.embed([c.text for c in chunks]) if chunks else []
        self.store.replace_document(source, chunks, vectors)
        return len(chunks)

    def retrieve(self, question: str, top_k: int | None = None) -> list[Hit]:
        [vector] = self.embedder.embed([question])
        return self.store.search(vector, top_k or self.settings.top_k)

    def answer(self, question: str, top_k: int | None = None) -> Answer:
        hits = self.retrieve(question, top_k)
        if not hits:
            return Answer("I do not know: nothing relevant has been ingested.", [])
        response = self._llm.post(
            "/v1/chat/completions",
            json={
                "model": self.settings.llm_model,
                "messages": build_prompt(question, hits),
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        return Answer(response.json()["choices"][0]["message"]["content"], hits)
