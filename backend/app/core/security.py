"""Centralized secret-comparison helpers for admin / ingest endpoints.

Uses `secrets.compare_digest` (constant-time) instead of `==` to address
SEC-06. Also exposes a FastAPI dependency that 403s before body parsing,
which closes SEC-12 (info disclosure via 422 vs 403).
"""

import secrets

from fastapi import Header, HTTPException

from app.core.config import settings


def check_secret(provided: str | None) -> bool:
    """Constant-time comparison against the configured ingest secret.

    Returns False if the server has no secret configured (mis-deployment) or
    the caller provided None / empty — never raises.
    """
    if not settings.ingest_secret or not provided:
        return False
    return secrets.compare_digest(provided, settings.ingest_secret)


def require_secret(x_ingest_secret: str | None = Header(None)) -> bool:
    """FastAPI dependency: 403 unless the request carries a valid header.

    Use as `Depends(require_secret)` on protected routes. Runs *before*
    body parsing, so unauthenticated callers get 403 rather than 422 — that
    avoids leaking endpoint shape information.
    """
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return True
