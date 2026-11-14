from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """RAG service configuration, read from RAG_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="RAG_")

    # ":memory:" runs Qdrant in-process (tests, quick demos); otherwise a server URL.
    qdrant_url: str = ":memory:"
    collection: str = "docs"

    # "hashing" needs no model download; "openai" calls an /v1/embeddings endpoint.
    embedder: str = "hashing"
    embedding_dim: int = 1024
    embedding_url: str = "http://localhost:8000"
    embedding_model: str = "default"

    # Any OpenAI-compatible chat endpoint, e.g. the gateway from llm-inference-k8s.
    llm_url: str = "http://localhost:8000"
    llm_model: str = "default"
    llm_api_key: str = ""

    chunk_chars: int = 800
    chunk_overlap: int = 120
    top_k: int = 4
