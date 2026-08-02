"""Corpus counts for the glossary review queue.

Run with:  python backend/tests/test_vocab_counts.py

The review page needs, per rule, how many segments contain each spelling.
Counting them one at a time was a sequential scan over 2.6M rows per
spelling — 40 scans and 14 seconds for a page of 20, against a 15s frontend
timeout. Folding them into one SELECT makes it a single scan, so these check
the fast version still agrees with the obvious one, including on the LIKE
wildcards that escaping has to survive.
"""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "vocab_counts_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))
from datetime import datetime
from sqlalchemy import func, select
from app.core.database import Base, async_session, engine
from app.models.episode import Episode, Segment
from app.api.vocab import _corpus_hits, _ilike_literal

TEXTS = ["因為他很吃香","吃香的東西","完全無關","采翎說話","采翎又說","100% 折扣","a_b 底線",""]
PROBE = ["吃香","采翎","無關","不存在","100%","a_b","因為"]

async def main():
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        s.add(Episode(id=1,title="t",show="直播",audio_url="x",
                      published_at=datetime(2020,1,1),transcription_status="done"))
        for i,t in enumerate(TEXTS,1):
            s.add(Segment(id=i,episode_id=1,start_time=i,end_time=i+1,text=t))
        await s.commit()

    async with async_session() as s:
        bulk = await _corpus_hits(s, PROBE)
        ok=True
        for t in PROBE:
            one = (await s.execute(select(func.count(Segment.id)).where(
                Segment.text.ilike(f"%{_ilike_literal(t)}%", escape="\\")))).scalar() or 0
            match = bulk.get(t) == one
            ok &= match
            print(f"  {'\033[32mPASS\033[0m' if match else '\033[31mFAIL\033[0m'}  {t!r}: bulk={bulk.get(t)} single={one}")
        dup = await _corpus_hits(s, ["吃香","吃香","采翎"])
        d_ok = dup == {"吃香":2,"采翎":2}
        ok &= d_ok
        print(f"  {'\033[32mPASS\033[0m' if d_ok else '\033[31mFAIL\033[0m'}  duplicates deduped: {dup}")
        e_ok = await _corpus_hits(s, []) == {}
        ok &= e_ok
        print(f"  {'\033[32mPASS\033[0m' if e_ok else '\033[31mFAIL\033[0m'}  empty input safe")
    await engine.dispose()
    print("\033[32mall bulk-count checks passed\033[0m" if ok else "\033[31mFAILURES\033[0m")
    sys.exit(0 if ok else 1)

asyncio.run(main())
