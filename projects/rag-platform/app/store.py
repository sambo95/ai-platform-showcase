"""Qdrant-backed vector store."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .chunking import Chunk

NAMESPACE = uuid.UUID("6f1c2a0e-9a51-4c53-8a57-2d3c2f0e7a11")


@dataclass(frozen=True)
class Hit:
    source: str
    heading: str
    text: str
    score: float


class VectorStore:
    def __init__(self, url: str, collection: str, dim: int):
        self._client = (
            QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url)
        )
        self._collection = collection
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
            )

    def replace_document(
        self, source: str, chunks: list[Chunk], vectors: list[list[float]]
    ) -> None:
        """Delete then insert, so re-ingesting a shorter document leaves no stale chunks."""
        self._client.delete(
            self._collection,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))])
            ),
        )
        if not chunks:
            return
        points = [
            PointStruct(
                # Deterministic IDs make ingestion idempotent.
                id=str(uuid.uuid5(NAMESPACE, f"{chunk.source}#{chunk.index}")),
                vector=vector,
                payload={"source": chunk.source, "heading": chunk.heading, "text": chunk.text},
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(self._collection, points=points)

    def search(self, vector: list[float], top_k: int) -> list[Hit]:
        result = self._client.query_points(self._collection, query=vector, limit=top_k)
        return [
            Hit(p.payload["source"], p.payload["heading"], p.payload["text"], float(p.score))
            for p in result.points
        ]

    def count(self) -> int:
        return self._client.count(self._collection).count
