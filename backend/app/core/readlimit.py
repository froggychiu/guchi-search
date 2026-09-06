"""Rate limits for the public read API.

Until now only POST /api/corrections was limited, because it was the only
endpoint an anonymous caller could use to cause lasting damage. Opening the
read endpoints to other people's agents changes the threat: nothing is
written, but /api/search is a sequential scan over ~2.6M segments (0.37-0.40s
of server CPU, measured on production), and an agent loop can issue those far
faster than a human clicking a search box. Enough of them in parallel and the
database has nothing left for anyone else.

Two tiers, because the endpoints are not equally expensive:

  SEARCH   /api/search and /api/text-count. Full scan per call. Kept tight.
  READ     Everything else — episode lists, single episodes, shows, stats.
           All indexed lookups, cheap, so the limit only needs to stop
           something pathological.

Both tiers are also bounded globally. The per-client layer is keyed on a
forwarded IP and is therefore spoofable; the global layer is what actually
caps total load, and it holds regardless of what headers arrive.

State is per process, like the correction limiter. Railway runs one backend
container today; if that is ever scaled out, each replica enforces its own
share. See core/ratelimit.py.
"""

import secrets

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.ratelimit import SlidingWindow, client_key

# Limits come from settings; see config.py for how the numbers were chosen.
_search_per_client = SlidingWindow(settings.rate_limit_search_per_client, 60)
_read_per_client = SlidingWindow(settings.rate_limit_read_per_client, 60)
_search_global = SlidingWindow(settings.rate_limit_search_global, 60)
_read_global = SlidingWindow(settings.rate_limit_read_global, 60)

_GLOBAL_KEY = "*"


def _is_internal(request: Request) -> bool:
    """True for calls from our own frontend's server-side rendering.

    Every server-rendered episode page fetches from this API, and they all
    arrive from one container IP. A crawler walking 832 episode pages would
    otherwise trip the per-client limit and get 429s baked into the cached
    HTML — the exact opposite of why the pages were made crawlable.

    Unset by default, in which case there is no bypass and nothing changes.
    """
    token = settings.internal_token
    if not token:
        return False
    provided = request.headers.get("x-internal-token")
    if not provided:
        return False
    return secrets.compare_digest(provided, token)


def _enforce(request: Request, per_client: SlidingWindow, glob: SlidingWindow) -> None:
    if _is_internal(request):
        return

    retry_after = per_client.check(client_key(request))
    if retry_after is None:
        retry_after = glob.check(_GLOBAL_KEY)

    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Slow down and retry shortly.",
            headers={"Retry-After": str(int(retry_after))},
        )


def search_guard(request: Request) -> None:
    """Limit for the endpoints that sequentially scan the segments table."""
    _enforce(request, _search_per_client, _search_global)


def read_guard(request: Request) -> None:
    """Limit for the cheap, indexed read endpoints."""
    _enforce(request, _read_per_client, _read_global)
