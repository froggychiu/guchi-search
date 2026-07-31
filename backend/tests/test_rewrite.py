"""Tests for the batched segment rewrite.

Run with:  python backend/tests/test_rewrite.py

normalize-tw and apply-vocab both touch ~2.6M rows. The first version loaded
them all as ORM objects and ran difflib on every changed one; it was still
running with no output after ten minutes on production and had to be replaced.
These checks pin the batched replacement: that it spans batch boundaries, that
a dry run really writes nothing, that applying twice is a no-op, and that the
substitution report is accurate.
"""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "rewrite_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))

from app.core.database import Base, async_session, engine
from app.models.episode import Episode, Segment
from app.scripts.rewrite import rewrite_segments, diff_substitutions, BATCH_SIZE
from app.services.text_normalize import normalize_variants
from datetime import datetime

N = BATCH_SIZE * 2 + 137   # spans several batches plus a partial one

async def main():
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        s.add(Episode(id=1, title="t", show="直播", audio_url="x",
                      published_at=datetime(2020,1,1), transcription_status="done"))
        for i in range(1, N + 1):
            # every 3rd row needs repair
            text = "因爲他很喫香" if i % 3 == 0 else "這一段完全正確"
            s.add(Segment(id=i, episode_id=1, start_time=float(i), end_time=float(i)+1, text=text))
        await s.commit()

    expect_changed = N // 3
    async with async_session() as s:
        sc, ch, subs = await rewrite_segments(s, normalize_variants, dry_run=True, label="dry")
    ok = []
    ok.append(("scanned all rows", sc, N))
    ok.append(("changed count", ch, expect_changed))
    ok.append(("爲->為 count", subs.get(("爲","為")), expect_changed))
    ok.append(("喫->吃 count", subs.get(("喫","吃")), expect_changed))

    async with async_session() as s:
        from sqlalchemy import select, func
        n = (await s.execute(select(func.count(Segment.id)).where(Segment.text.like("%爲%")))).scalar()
    ok.append(("dry run wrote nothing", n, expect_changed))

    async with async_session() as s:
        sc2, ch2, _ = await rewrite_segments(s, normalize_variants, dry_run=False, label="apply")
    ok.append(("applied count", ch2, expect_changed))

    async with async_session() as s:
        from sqlalchemy import select, func
        left = (await s.execute(select(func.count(Segment.id)).where(Segment.text.like("%爲%")))).scalar()
        fixed = (await s.execute(select(func.count(Segment.id)).where(Segment.text == "因為他很吃香"))).scalar()
    ok.append(("no variants remain", left, 0))
    ok.append(("rows correctly rewritten", fixed, expect_changed))

    async with async_session() as s:
        _, ch3, _ = await rewrite_segments(s, normalize_variants, dry_run=False, label="again")
    ok.append(("idempotent second run", ch3, 0))

    ok.append(("diff same-length", diff_substitutions("因爲", "因為"), [("爲","為")]))
    ok.append(("diff length-changing", diff_substitutions("abc", "axxc"), [("b","xx")]))

    fails = [l for l, got, want in ok if got != want]
    for l, got, want in ok:
        mark = "\033[32mPASS\033[0m" if got == want else "\033[31mFAIL\033[0m"
        print(f"  {mark}  {l}: got {got!r}, want {want!r}")
    await engine.dispose()
    print("\033[32mall batch checks passed\033[0m" if not fails else f"\033[31m{fails}\033[0m")
    sys.exit(1 if fails else 0)

asyncio.run(main())
