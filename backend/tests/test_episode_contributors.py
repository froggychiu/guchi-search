"""Tests for the proofreader credits on GET /api/episodes/{id}.

Run with:  python backend/tests/test_episode_contributors.py

Postgres is swapped for a throwaway SQLite file — the endpoint reads only the
database. The fixture pins the three things the credit line has to get right:

  1. Only adopted corrections count. A pending or rejected suggestion is a
     claim, not a contribution, and must not put a name on the page.
  2. "匿名" / blank / "系統自動偵測" are never credited — the first two are
     many different people behind one label, the third is ingest.py's
     hallucination scanner.
  3. Credits are scoped to the episode. A prolific proofreader on episode 2
     must not appear on episode 1.
"""

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DB_PATH = os.path.join(HERE, "episode_contributors_test.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"

sys.path.insert(0, BACKEND)

from datetime import datetime  # noqa: E402

import httpx  # noqa: E402

from app.core.database import Base, async_session, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.episode import Correction, Episode, Segment  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


# (submitter, status, episode) — 阿明 has 3 adopted edits on ep 1, 小美 has 1,
# so 阿明 sorts first. Everyone else is here to be excluded.
CORRECTIONS = [
    ("阿明", "approved", 1),
    ("阿明", "approved", 1),
    ("阿明", "approved", 1),
    ("小美", "approved", 1),
    ("待審中的人", "pending", 1),
    ("被退回的人", "rejected", 1),
    ("匿名", "approved", 1),
    ("", "approved", 1),
    ("系統自動偵測", "approved", 1),
    ("別集的人", "approved", 2),
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        for ep in (1, 2, 3):
            s.add(Episode(
                id=ep, title=f"【呱吉】新資料夾({ep})", description="",
                show="新資料夾", audio_url=f"https://example.invalid/{ep}.mp3",
                published_at=datetime(2020, 1, ep), duration_seconds=600,
                transcription_status="done",
            ))
            s.add(Segment(
                id=ep, episode_id=ep, speaker="",
                start_time=0.0, end_time=5.0, text=f"第 {ep} 集的一句話。",
            ))
        for i, (name, status, ep) in enumerate(CORRECTIONS, start=1):
            s.add(Correction(
                id=i, segment_id=ep, original_text="原句", suggested_text="修正後",
                submitter_name=name, status=status,
            ))
        await s.commit()


async def main():
    await seed()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        print("\n--- episode 1: adopted, named corrections only ---")
        ep1 = (await c.get("/api/episodes/1")).json()
        check("names, most corrections first",
              [x["name"] for x in ep1["contributors"]], ["阿明", "小美"])
        check("counts are per-person", [x["count"] for x in ep1["contributors"]], [3, 1])

        print("\n--- episode 2: credits do not leak across episodes ---")
        ep2 = (await c.get("/api/episodes/2")).json()
        check("only that episode's proofreader",
              [x["name"] for x in ep2["contributors"]], ["別集的人"])

        print("\n--- episode 3: no corrections at all ---")
        ep3 = (await c.get("/api/episodes/3")).json()
        check("empty list, not missing key", ep3["contributors"], [])

        print("\n--- the leaderboard applies the same exclusions ---")
        board = (await c.get("/api/corrections/contributors")).json()
        check("named contributors across all episodes",
              [x["name"] for x in board["contributors"]], ["阿明", "小美", "別集的人"])

    await engine.dispose()
    print("\n" + ("=" * 60))
    if failures:
        print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
        sys.exit(1)
    print("\033[32mall checks passed\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
