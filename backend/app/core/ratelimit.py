"""In-process rate limiting for the public correction endpoint (SEC-09).

POST /api/corrections takes no authentication by design — anyone reading a
transcript can fix a line, and that is the point. But nothing stopped a script
from walking all 2.6M segments and filing one correction against each, which
would bury the review queue and bloat the database.

No dependency for this. `fastapi-limiter` needs Redis, which would mean
another Railway service and another bill. `slowapi` is well maintained but its
in-memory backend offers exactly the same guarantees as the code below — good
within one process, reset on restart — so it would buy an extra dependency and
nothing else. The value in slowapi is its Redis backend, which is the part we
are avoiding.

Two layers, because they fail differently:

  Per client   Stops the obvious script. Identified by forwarded IP, which a
               determined attacker can spoof, so it is a speed bump rather
               than a wall.
  Global       Bounds how fast the queue can grow no matter who is filing or
               what headers they send. This is the layer that actually holds.
"""

import time
from collections import defaultdict, deque


class SlidingWindow:
    """Per-key request counter over a moving time window.

    State lives in this process only. Railway runs a single backend container,
    so that is the whole picture today; if it is ever scaled out, each replica
    enforces its own share and the effective limit multiplies by the replica
    count. That is a reason to keep the global DB-backed check below, not a
    reason to reach for Redis.
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until retry."""
        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            return max(1.0, self.window - (now - hits[0]))

        hits.append(now)
        self._prune(now)
        return None

    def _prune(self, now: float) -> None:
        """Drop keys whose windows have fully expired.

        Without this the dict grows one entry per distinct IP forever, which
        is its own slow denial of service.
        """
        if len(self._hits) < 1024:
            return
        cutoff = now - self.window
        for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
            del self._hits[key]


def client_key(request) -> str:
    """Best-effort client identity for rate limiting.

    Railway terminates TLS upstream, so request.client.host is the proxy and
    the real address is in X-Forwarded-For. The leftmost entry is the client
    and is trivially spoofable — which is exactly why this is only the first
    of two layers.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
