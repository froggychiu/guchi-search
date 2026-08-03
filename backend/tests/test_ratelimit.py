"""Rate limiting and input bounds on the public correction endpoint (SEC-09).

Run with:  python backend/tests/test_ratelimit.py

POST /api/corrections is unauthenticated on purpose — anyone reading a
transcript can fix a line. Before this, nothing stopped a script from walking
2.6M segments and filing one correction against each, and nothing bounded how
much text a single request could store.

Also covers SEC-08: a null byte in a public query parameter returned 500,
because Postgres TEXT cannot hold one and asyncpg raised before the query ran.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "ratelimit_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402

from app.api import corrections as corrections_api  # noqa: E402
from app.core.database import Base, async_session, engine  # noqa: E402
from app.core.ratelimit import SlidingWindow, client_key  # noqa: E402
from app.main import app  # noqa: E402
from app.models.episode import Correction, Episode, Segment  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


class FakeRequest:
    def __init__(self, ip):
        self.headers = {"x-forwarded-for": ip}
        self.client = None


def window_checks():
    print("\n--- the sliding window itself ---")
    w = SlidingWindow(limit=3, window_seconds=60)
    check("first three allowed", [w.check("a") is None for _ in range(3)], [True] * 3)
    check("fourth blocked", w.check("a") is not None, True)
    check("a different client is unaffected", w.check("b") is None, True)

    retry = w.check("a")
    check("blocked response says when to retry", retry is not None and retry > 0, True)

    print("\n--- keys do not accumulate forever ---")
    # Without pruning, one entry per distinct IP is its own slow leak.
    w2 = SlidingWindow(limit=1, window_seconds=0)
    for i in range(2000):
        w2.check(f"ip-{i}")
    check("expired keys are pruned", len(w2._hits) < 2000, True)

    print("\n--- client identity ---")
    check("uses the forwarded address", client_key(FakeRequest("203.0.113.7")), "203.0.113.7")
    check("takes the leftmost of a chain",
          client_key(FakeRequest("203.0.113.7, 10.0.0.1")), "203.0.113.7")


async def endpoint_checks():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        s.add(Episode(id=1, title="t", show="直播", audio_url="x",
                      published_at=datetime(2020, 1, 1), transcription_status="done"))
        for i in range(1, 60):
            s.add(Segment(id=i, episode_id=1, start_time=i, end_time=i + 1,
                          text=f"第 {i} 段原始內容"))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        def body(seg, text="修正後的內容", name="校對者"):
            return {"segment_id": seg, "suggested_text": text, "submitter_name": name}

        print("\n--- input bounds ---")
        r = await c.post("/api/corrections", json=body(1, name="長" * 60))
        check("over-long submitter_name rejected as 422, not 500", r.status_code, 422)
        r = await c.post("/api/corrections", json=body(2, text="長" * 2001))
        check("over-long suggested_text rejected", r.status_code, 422)
        r = await c.post("/api/corrections", json=body(3, text=""))
        check("empty suggestion rejected", r.status_code, 422)
        r = await c.post("/api/corrections", json=body(4, name="長" * 50))
        check("a name at the limit is accepted", r.status_code, 200)

        print("\n--- per-client rate limit ---")
        corrections_api._per_client = SlidingWindow(limit=5, window_seconds=60)
        headers = {"x-forwarded-for": "203.0.113.9"}
        codes = []
        for seg in range(10, 18):
            r = await c.post("/api/corrections", json=body(seg), headers=headers)
            codes.append(r.status_code)
        check("stops after the limit", codes.count(429) > 0, True)
        check("allowed exactly the limit", codes.count(200), 5)
        r = await c.post("/api/corrections", json=body(30),
                         headers={"x-forwarded-for": "203.0.113.10"})
        check("another client is unaffected", r.status_code, 200)

        print("\n--- global ceiling holds even with a spoofed address ---")
        corrections_api._per_client = SlidingWindow(limit=1000, window_seconds=60)
        original_limit = corrections_api.GLOBAL_HOURLY_LIMIT
        async with async_session() as s:
            existing = len((await s.execute(
                __import__("sqlalchemy").select(Correction)
            )).scalars().all())
        corrections_api.GLOBAL_HOURLY_LIMIT = existing  # already at the ceiling
        r = await c.post("/api/corrections", json=body(40),
                         headers={"x-forwarded-for": "198.51.100.1"})
        check("blocked at the ceiling", r.status_code, 429)
        r = await c.post("/api/corrections", json=body(41),
                         headers={"x-forwarded-for": "198.51.100.2"})
        check("a fresh address does not get around it", r.status_code, 429)
        corrections_api.GLOBAL_HOURLY_LIMIT = original_limit

        print("\n--- the ceiling only counts the recent window ---")
        async with async_session() as s:
            old = (await s.execute(__import__("sqlalchemy").select(Correction))).scalars().all()
            for correction in old:
                correction.created_at = datetime.utcnow() - timedelta(hours=3)
            await s.commit()
        corrections_api.GLOBAL_HOURLY_LIMIT = 5
        r = await c.post("/api/corrections", json=body(42),
                         headers={"x-forwarded-for": "198.51.100.3"})
        check("old corrections do not count against it", r.status_code, 200)
        corrections_api.GLOBAL_HOURLY_LIMIT = original_limit

        print("\n--- SEC-08: null byte in a public query parameter ---")
        r = await c.get("/api/search", params={"q": "hi\x00bye"})
        check("/api/search rejects it as 422, not 500", r.status_code, 422)
        r = await c.get("/api/text-count", params={"q": "hi\x00bye"})
        check("/api/text-count rejects it too", r.status_code, 422)
        r = await c.get("/api/search", params={"q": "原始"})
        check("a normal query still works", r.status_code, 200)

    await engine.dispose()


window_checks()
asyncio.run(endpoint_checks())

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall rate-limit checks passed\033[0m")
