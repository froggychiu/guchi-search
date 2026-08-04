"""Admin session tokens, and the boundary they must not cross.

Run with:  python backend/tests/test_session.py

The admin UI used to hold the raw secret in browser memory to review
corrections. That secret is also the ingest secret: it can call
/api/replace-text, which rewrites all ~2.6M segments. Reviewing a typo should
not require holding a key that can destroy the corpus.

So the browser now holds a signed, expiring, review-scoped token instead. The
test that matters most is the last block: a token must be refused by the
destructive endpoints. If that ever passes, the split has bought nothing.
"""

import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "session_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["GUCHI_INGEST_SECRET"] = "test-secret-value"
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402

from app.core.database import Base, engine  # noqa: E402
from app.core.security import (  # noqa: E402
    check_review_credential,
    check_secret,
    check_session_token,
    issue_session_token,
)
import app.main as app_module  # noqa: E402
from app.main import app  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []
SECRET = "test-secret-value"


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("--- token shape and validation ---")
token, expires_at = issue_session_token()
check("a fresh token validates", check_session_token(token), True)
check("expiry is in the future", expires_at > int(time.time()), True)
check("the raw secret is not a token", check_session_token(SECRET), False)
check("garbage is rejected", check_session_token("nonsense"), False)
check("empty is rejected", check_session_token(""), False)
check("None is rejected", check_session_token(None), False)

print("\n--- forgery ---")
prefix, scope, exp, sig = token.split(".")
check("a tampered expiry fails the signature",
      check_session_token(f"{prefix}.{scope}.{int(exp) + 99999}.{sig}"), False)
check("a tampered signature fails",
      check_session_token(f"{prefix}.{scope}.{exp}.{'0' * len(sig)}"), False)
check("a different scope fails",
      check_session_token(f"{prefix}.admin.{exp}.{sig}"), False)

print("\n--- expiry ---")
expired, _ = issue_session_token(ttl_seconds=-1)
check("an expired token is refused", check_session_token(expired), False)
check("...and is refused for review too", check_review_credential(expired), False)

print("\n--- which credential opens which door ---")
check("secret passes the destructive check", check_secret(SECRET), True)
check("token FAILS the destructive check", check_secret(token), False)
check("secret passes the review check", check_review_credential(SECRET), True)
check("token passes the review check", check_review_credential(token), True)


async def endpoint_checks():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        print("\n--- exchanging the secret for a token ---")
        r = await c.post("/api/corrections/session",
                         headers={"X-Ingest-Secret": SECRET})
        check("the secret mints a token", r.status_code, 200)
        issued = r.json()["token"]
        check("the token works", check_session_token(issued), True)

        r = await c.post("/api/corrections/session",
                         headers={"X-Ingest-Secret": "wrong"})
        check("a wrong secret does not", r.status_code, 403)

        # A token minting further tokens would renew itself forever, which is
        # the opposite of what an expiry is for.
        r = await c.post("/api/corrections/session",
                         headers={"X-Ingest-Secret": issued})
        check("a token cannot mint another token", r.status_code, 403)

        print("\n--- the review screens accept the token ---")
        for path in ("/api/corrections?status=pending", "/api/vocab?status=pending"):
            r = await c.get(path, headers={"X-Ingest-Secret": issued})
            check(f"GET {path.split('?')[0]}", r.status_code, 200)
            r = await c.get(path)
            check(f"...and refuses no credential", r.status_code, 403)

        print("\n--- the destructive endpoints do NOT ---")
        # This is the whole point of the split.
        for path in ("/api/ingest", "/api/reindex",
                     "/api/maintenance/setup", "/api/replace-text"):
            r = await c.post(path, headers={"X-Ingest-Secret": issued},
                             json={"old_text": "a", "new_text": "b"})
            check(f"POST {path} rejects a token", r.status_code, 403)

        # Stub the runner: we are checking the credential, not running a real
        # maintenance job. Without this the background task shells out and
        # fails on a missing interpreter, taking the test with it.
        app_module._run_script = lambda *a, **k: None
        r = await c.post("/api/maintenance/setup",
                         headers={"X-Ingest-Secret": SECRET})
        check("but still accepts the raw secret", r.status_code, 200)

    await engine.dispose()


asyncio.run(endpoint_checks())

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall session checks passed\033[0m")
