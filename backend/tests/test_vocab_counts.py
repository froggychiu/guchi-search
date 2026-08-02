"""Cached corpus counts for the glossary review queue.

Run with:  python backend/tests/test_vocab_counts.py

The review page needs, per rule, how many segments contain each spelling.
Measuring that per request was a pattern match across 2.6M rows for every
spelling on the page: 14 seconds one spelling at a time, still 11 after
folding them into a single scan, because the cost is the matching rather than
the I/O — against a frontend that gives up at 15. The numbers are now computed
by the nightly mining run and stored on the rule.

These check the stored numbers are right, that a spelling containing LIKE
wildcards is matched literally, and that a rule the reviewer has already
judged is left alone.
"""

import asyncio
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "vocab_counts_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
sys.path.insert(0, os.path.dirname(HERE))

from sqlalchemy import select  # noqa: E402

from app.core.database import Base, async_session, engine  # noqa: E402
from app.models.episode import Episode, Segment, VocabRule  # noqa: E402
from app.services.vocab import refresh_corpus_counts  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


SEGMENTS = [
    "因為他很吃香",
    "吃香的東西",
    "完全無關的一句",
    "然後采翎說話",
    "采翎又說了一次",
    "折扣是 100% 喔",
    "變數叫 a_b 底線",
]

# (wrong, right, status, expected wrong_hits, expected right_hits)
RULES = [
    ("彩玲", "采翎", "pending", 0, 2),
    ("吃香", "喜歡", "pending", 2, 0),
    ("100%", "全部", "pending", 1, 0),      # literal %, not a wildcard
    ("a_b", "ab", "pending", 1, 0),         # literal _, not a wildcard
    ("無關", "有關", "active", 1, 0),        # already judged; not refreshed
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as s:
        s.add(Episode(id=1, title="t", show="直播", audio_url="x",
                      published_at=datetime(2020, 1, 1), transcription_status="done"))
        for i, text in enumerate(SEGMENTS, start=1):
            s.add(Segment(id=i, episode_id=1, start_time=i, end_time=i + 1, text=text))
        for i, (w, r, status, _, _) in enumerate(RULES, start=1):
            s.add(VocabRule(id=i, wrong_text=w, right_text=r, status=status))
        await s.commit()

    async with async_session() as s:
        n = await refresh_corpus_counts(s)
    check("refreshes only the pending rules", n, 4)

    async with async_session() as s:
        rules = {
            r.wrong_text: r
            for r in (await s.execute(select(VocabRule))).scalars().all()
        }

    for wrong, _, status, want_wrong, want_right in RULES:
        rule = rules[wrong]
        if status != "pending":
            check(f"{wrong}: judged rule left at zero", rule.wrong_hits, 0)
            check(f"{wrong}: not stamped", rule.counts_updated_at, None)
            continue
        check(f"{wrong}: wrong_hits", rule.wrong_hits, want_wrong)
        check(f"{wrong}: right_hits", rule.right_hits, want_right)
        check(f"{wrong}: stamped", rule.counts_updated_at is not None, True)

    # A spelling shared by several rules is counted once and applied to each.
    async with async_session() as s:
        s.add(VocabRule(id=99, wrong_text="採靈", right_text="采翎", status="pending"))
        await s.commit()
    async with async_session() as s:
        await refresh_corpus_counts(s)
        shared = (await s.execute(
            select(VocabRule).where(VocabRule.wrong_text == "採靈")
        )).scalar_one()
    check("a shared correct form still counts right", shared.right_hits, 2)
    check("and its own form counts zero", shared.wrong_hits, 0)

    # Nothing pending must not blow up.
    async with async_session() as s:
        empty = await refresh_corpus_counts(s, status="rejected")
    check("no rules of that status is a no-op", empty, 0)

    await engine.dispose()
    print("\n" + "=" * 60)
    if failures:
        print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
        sys.exit(1)
    print("\033[32mall corpus-count checks passed\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
