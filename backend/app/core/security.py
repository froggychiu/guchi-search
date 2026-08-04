"""Credentials for admin and ingest endpoints.

Two kinds of credential, deliberately not equivalent:

  The raw secret   Also the ingest secret. It can trigger /api/replace-text,
                   which rewrites every one of ~2.6M segments. Used by the
                   nightly cron and by hand from a terminal. Never sent to a
                   browser.

  A session token  Signed with the raw secret, expires, and is accepted ONLY
                   by the review screens — listing and judging corrections and
                   glossary rules. This is what the admin UI holds.

The split is the point. Before it, reviewing a correction in the browser meant
keeping a key capable of destroying the corpus in browser memory, and moving
between the two review pages meant re-entering it. A stolen session token can
approve or reject things a human can already undo; it cannot rewrite the
database, and it stops working on its own.

Tokens are stateless: the expiry travels in the token and is covered by the
signature, so there is no session table and rotating the secret invalidates
every outstanding token at once.

Constant-time comparison throughout (SEC-06), and require_* run before body
parsing so unauthenticated callers get 403 rather than a 422 that confirms an
endpoint's shape (SEC-12).
"""

import hashlib
import hmac
import secrets
import time

from fastapi import Header, HTTPException

from app.core.config import settings

# Long enough to review a queue across a working day, short enough that a
# leaked token is not a standing key.
SESSION_TTL_SECONDS = 8 * 60 * 60

_TOKEN_PREFIX = "s1"
_TOKEN_SCOPE = "review"


def check_secret(provided: str | None) -> bool:
    """True only for the raw secret. Session tokens are NOT accepted.

    Guards the destructive endpoints: ingest, reindex, maintenance and
    replace-text. Those run from cron and the terminal, so nothing is gained
    by letting a browser credential reach them.
    """
    if not settings.ingest_secret or not provided:
        return False
    return secrets.compare_digest(provided, settings.ingest_secret)


def _sign(payload: str) -> str:
    return hmac.new(
        settings.ingest_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def issue_session_token(ttl_seconds: int = SESSION_TTL_SECONDS) -> tuple[str, int]:
    """Mint a review-scoped token. Returns (token, unix expiry)."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{_TOKEN_PREFIX}.{_TOKEN_SCOPE}.{expires_at}"
    return f"{payload}.{_sign(payload)}", expires_at


def check_session_token(provided: str | None) -> bool:
    """Validate a token's signature, scope and expiry."""
    if not settings.ingest_secret or not provided:
        return False
    parts = provided.split(".")
    if len(parts) != 4:
        return False
    prefix, scope, expires_raw, signature = parts
    if prefix != _TOKEN_PREFIX or scope != _TOKEN_SCOPE:
        return False
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False

    # Signature first: an expired token and a forged one should be
    # indistinguishable from outside.
    payload = f"{prefix}.{scope}.{expires_raw}"
    if not secrets.compare_digest(signature, _sign(payload)):
        return False
    return expires_at > int(time.time())


def check_review_credential(provided: str | None) -> bool:
    """Accept either credential. For the review screens only."""
    return check_secret(provided) or check_session_token(provided)


def require_secret(x_ingest_secret: str | None = Header(None)) -> bool:
    """Dependency for destructive endpoints — raw secret only."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return True


def require_review(x_ingest_secret: str | None = Header(None)) -> bool:
    """Dependency for the review screens — raw secret or session token."""
    if not check_review_credential(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid or expired credential")
    return True
