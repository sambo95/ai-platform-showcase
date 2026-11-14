"""Embedding backends behind one small interface."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by do does for from has have how i if in is it of on or "
    "that the this to was what when where which why will with my".split()
)


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Feature-hashed bag of unigrams and bigrams, L2-normalized.

    Lexical, not semantic: it will not match "pod keeps restarting" to
    "CrashLoopBackOff". It exists so the whole pipeline, the tests and the CI
    retrieval gate run offline with zero model downloads. Swap in
    OpenAIEmbedder for real semantic retrieval.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = [w for w in TOKEN.findall(text.lower()) if w not in STOPWORDS]
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature in self._features(text):
            # hashlib, not hash(): Python's hash() is salted per process.
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OpenAIEmbedder:
    """Calls an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, url: str, model: str, dim: int, api_key: str = ""):
        self.dim = dim
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=url, headers=headers, timeout=60)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.post("/v1/embeddings", json={"model": self._model, "input": texts})
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]
