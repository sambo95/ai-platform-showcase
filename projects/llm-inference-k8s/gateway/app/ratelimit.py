import time
from collections.abc import Callable


class TokenBucketLimiter:
    """Per-tenant token bucket.

    State is in-process, so the effective limit is rate_per_min x replicas.
    Swap for a Redis-backed bucket if limits must be exact across replicas.
    """

    def __init__(self, rate_per_min: int, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(max(rate_per_min, 1))
        self.refill_per_s = self.capacity / 60.0
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, tenant: str) -> bool:
        now = self._clock()
        tokens, last = self._buckets.get(tenant, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
        if tokens < 1.0:
            self._buckets[tenant] = (tokens, now)
            return False
        self._buckets[tenant] = (tokens - 1.0, now)
        return True
