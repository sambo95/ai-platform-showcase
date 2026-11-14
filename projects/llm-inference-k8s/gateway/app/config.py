from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway configuration, read from GATEWAY_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    upstream_url: str = "http://model-server:8080"
    # Comma-separated API keys. Empty disables auth (local development only).
    api_keys: str = ""
    rate_limit_rpm: int = 120
    # Requests allowed in flight before the gateway sheds load with a 429.
    max_inflight: int = 32
    request_timeout_s: float = 300.0

    @property
    def key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())
