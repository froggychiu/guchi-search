"""Rate limiting on the public read API.

Run with:  python backend/tests/test_readlimit.py

The read endpoints were unlimited because, unauthenticated or not, they only
read. That reasoning holds for a website and breaks for an MCP endpoint: every
/api/search is a sequential scan of ~2.6M segments, and an agent can issue
those in a loop far faster than a person clicking. The limit is about database
CPU, not about writes.

What matters here, and what this pins down:

  * the expensive tier (search) and the cheap tier (episodes, shows, stats)
    are counted separately, so exhausting one does not lock the other
  * per-client counting is per client, so one heavy caller cannot lock out
    everyone else
  * the global ceiling still holds when the per-client key is spoofed, which
    is the whole reason it exists — x-forwarded-for is attacker-controlled
  * the frontend's server-side rendering is exempt, because every rendered
    page reaches the API from one container IP and would otherwise look like
    a single very abusive client
"""

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "readlimit_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402

from app.core import readlimit  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, async_session, engine  # noqa: E402
from app.core.ratelimit import SlidingWindow  # noqa: E402
from app.main import app  # noqa: E402
from app.models.episode import Episode, Segment  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def set_limits(search_client, search_global, read_client, read_global):
    """Install fresh windows so each scenario starts from zero."""
    readlimit._search_per_client = SlidingWindow(search_client, 60)
    readlimit._search_global = SlidingWindow(search_global, 60)
    readlimit._read_per_client = SlidingWindow(read_client, 60)
    readlimit._read_global = SlidingWindow(read_global, 60)


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        s.add(Episode(id=1, title="測試集", show="新資料夾", audio_url="http://a"))
        s.add(Segment(id=1, episode_id=1, start_time=0, end_time=1, text="電腦與呱吉"))
        await s.commit()


async def run():
    await seed()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        async def search(ip, **headers):
            return await c.get(
                "/api/search",
                params={"q": "電腦"},
                headers={"x-forwarded-for": ip, **headers},
            )

        async def read(ip):
            return await c.get("/api/shows", headers={"x-forwarded-for": ip})

        print("\n--- the expensive tier is capped per client ---")
        set_limits(3, 999, 999, 999)
        codes = [(await search("203.0.113.1")).status_code for _ in range(3)]
        check("first three searches allowed", codes, [200, 200, 200])

        blocked = await search("203.0.113.1")
        check("fourth is refused", blocked.status_code, 429)
        check(
            "and says when to retry",
            blocked.headers.get("retry-after", "").isdigit(),
            True,
        )

        print("\n--- one heavy client does not lock out the rest ---")
        check("a different address is unaffected", (await search("203.0.113.2")).status_code, 200)

        print("\n--- the tiers are counted separately ---")
        # This client has already exhausted its search budget above.
        check("cheap reads still work", (await read("203.0.113.1")).status_code, 200)

        print("\n--- the global ceiling survives a spoofed client key ---")
        # A fresh address for every request defeats per-client counting
        # entirely, which is why the global layer is the one that holds.
        set_limits(999, 4, 999, 999)
        spoofed = [
            (await search(f"198.51.100.{i}")).status_code for i in range(1, 7)
        ]
        check("allowed up to the global ceiling", spoofed[:4], [200] * 4)
        check("then refused despite new addresses each time", spoofed[4:], [429, 429])

        print("\n--- server-side rendering is exempt ---")
        set_limits(1, 999, 999, 999)
        original = settings.internal_token
        settings.internal_token = "sekret"
        me = "203.0.113.9"

        # Exhaust this address's budget as an ordinary caller first. A bypassed
        # request records no hit, so without this the budget would still be
        # untouched and the checks below would pass for the wrong reason.
        check("an ordinary request spends the budget", (await search(me)).status_code, 200)
        check("and the next one is refused", (await search(me)).status_code, 429)

        check(
            "the internal token gets through anyway",
            [(await search(me, **{"x-internal-token": "sekret"})).status_code
             for _ in range(5)],
            [200] * 5,
        )
        check(
            "a wrong token is treated as public",
            (await search(me, **{"x-internal-token": "nope"})).status_code,
            429,
        )

        settings.internal_token = ""
        check(
            "and the bypass is off when no token is configured",
            (await search(me, **{"x-internal-token": "sekret"})).status_code,
            429,
        )
        settings.internal_token = original

    # /api/search logs each query in a fire-and-forget task. Disposing the
    # engine out from under those produces a wall of CancelledError tracebacks
    # after the results, which makes a passing run look broken.
    await asyncio.sleep(0.2)
    await engine.dispose()


asyncio.run(run())

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall read-limit checks passed\033[0m")
