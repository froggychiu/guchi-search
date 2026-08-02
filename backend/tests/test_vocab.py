"""Tests for the transcription glossary.

Run with:  python backend/tests/test_vocab.py

Covers the two places this can go wrong quietly: rule application order, and
deciding which corrections generalize into a rule at all.
"""

import os
import sys

os.environ["GUCHI_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.text_normalize import (  # noqa: E402
    normalize_variants,
    to_taiwan_traditional,
)
from app.services.vocab import apply_rules, derive_rule  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("\n--- rules apply longest-first, whatever order they arrive in ---")
# 瓜 -> 呱 applied first would turn 瓜子 into 呱子, which 瓜子 -> 呱吉 then
# misses. Sorting by length inside load_active_rules is what prevents that;
# apply_rules is given an already-sorted list.
rules = sorted([("瓜", "呱"), ("瓜子", "呱吉")], key=lambda r: len(r[0]), reverse=True)
check("longer rule wins", apply_rules("今天瓜子有來嗎", rules), "今天呱吉有來嗎")
check("shorter rule still applies elsewhere",
      apply_rules("瓜哥你好", rules), "呱哥你好")

print("\n--- every occurrence in a segment is replaced ---")
check("repeated match", apply_rules("彩玲跟彩玲", [("彩玲", "采翎")]), "采翎跟采翎")
check("no match leaves text alone",
      apply_rules("完全無關的句子", [("彩玲", "采翎")]), "完全無關的句子")
check("empty rule set is a no-op", apply_rules("原文", []), "原文")

print("\n--- deriving a rule from a correction ---")
check("single substitution",
      derive_rule("然後彩玲就說", "然後采翎就說"), ("彩玲", "采翎"))
check("substitution at the start",
      derive_rule("瓜子今天請假", "呱吉今天請假"), ("瓜子", "呱吉"))
check("identical text yields nothing",
      derive_rule("一樣的", "一樣的"), None)
check("empty input yields nothing", derive_rule("", "abc"), None)

print("\n--- corrections that must NOT become rules ---")
# Two separate edits: whatever the proofreader fixed here is specific to this
# sentence, not a spelling that recurs across episodes.
check("two separate edits",
      derive_rule("阿甲跟阿乙去了", "阿丙跟阿丁去了"), None)
# A whole-sentence rewrite would match almost nothing and is not a glossary
# entry even though it is a valid correction.
check("long rewrite rejected",
      derive_rule("這句話整個都被重新寫過了非常長非常長",
                  "這句話整個被換成另外一段完全不同的內容非常長"), None)
check("pure deletion rejected", derive_rule("有多餘的字", "有的字"), None)

print("\n--- ingest conversion uses Taiwan variants (s2tw, not s2t) ---")
for simplified, want in [("吃饭", "吃飯"), ("才刚开始", "才剛開始"),
                         ("群组", "群組"), ("着急", "著急"),
                         ("众人", "眾人"), ("面条", "麵條")]:
    check(f"s2tw {simplified}", to_taiwan_traditional(simplified), want)

print("\n--- repairing text that is already Traditional but wrong-variant ---")
for wrong, want in [("很喫香", "很吃香"), ("這纔是", "這才是"), ("一羣人", "一群人"),
                    ("因爲如此", "因為如此"), ("在哪裏", "在哪裡"),
                    ("看着我", "看著我"), ("觀衆", "觀眾")]:
    check(f"t2tw {wrong}", normalize_variants(wrong), want)

print("\n--- the repair must not touch names, and must be idempotent ---")
for name in ["采翎", "呱吉", "東燁", "佳佳", "祐先", "黃國昌", "恬娃", "薔薔", "黎漾"]:
    check(f"{name} unchanged", normalize_variants(name), name)
once = normalize_variants("因爲他很喫香纔對")
check("idempotent", normalize_variants(once), once)
check("...and correct", once, "因為他很吃香才對")



# ---------------------------------------------------------------------------
# Mining rules out of approved corrections. This is what makes the glossary a
# standing mechanism rather than something that only grows when a proofreader
# remembers to tick a box.
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from datetime import datetime  # noqa: E402


async def _mining_checks():
    import os as _os

    db_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "vocab_test.db")
    if _os.path.exists(db_path):
        _os.remove(db_path)
    _os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

    from app.core.database import Base, async_session, engine
    from app.models.episode import Correction, Episode, Segment, VocabRule
    from app.services.vocab import mine_rules_from_corrections

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as s:
        s.add(Episode(id=1, title="t", show="直播", audio_url="x",
                      published_at=datetime(2020, 1, 1), transcription_status="done"))
        s.add(Segment(id=1, episode_id=1, start_time=0, end_time=1, text="x"))
        cid = 0

        def add(original, suggested, status="approved", who="甲"):
            nonlocal cid
            cid += 1
            s.add(Correction(id=cid, segment_id=1, original_text=original,
                             suggested_text=suggested, submitter_name=who,
                             status=status))

        # Seen 4 times by 3 people -> a pattern.
        for who in ("甲", "乙", "丙", "甲"):
            add("然後彩玲說", "然後采翎說", who=who)
        # Seen twice -> below the threshold of 3.
        for who in ("甲", "乙"):
            add("他是全智隆", "他是權志龍", who=who)
        # Plenty of evidence, but not yet approved.
        for who in ("甲", "乙", "丙"):
            add("這是瓜子", "這是呱吉", status="pending", who=who)
        # The hallucination scanner must never seed the glossary.
        for _ in range(5):
            add("字幕提供", "（疑似幻覺，建議刪除）", who="系統自動偵測")
        await s.commit()

    async with async_session() as s:
        proposed = await mine_rules_from_corrections(s)
    got = {(w, r): n for w, r, n, _ in proposed}
    check("mines the repeated substitution", got.get(("彩玲", "采翎")), 4)
    check("ignores below-threshold pairs", ("全智隆", "權志龍") in got, False)
    check("ignores corrections not yet approved", ("瓜子", "呱吉") in got, False)
    check("ignores the hallucination bot", len(got), 1)

    async with async_session() as s:
        rule = (await s.execute(
            select(VocabRule).where(VocabRule.wrong_text == "彩玲")
        )).scalar_one()
        check("proposed as pending, never active", rule.status, "pending")
        check("records how it was proposed", rule.source, "mined")
        check("records its evidence", rule.evidence_count, 4)
        # An admin rejects it.
        rule.status = "rejected"
        await s.commit()

    async with async_session() as s:
        again = await mine_rules_from_corrections(s)
    check("a rejected rule is not proposed again", again, [])

    async with async_session() as s:
        n = (await s.execute(
            select(func.count(VocabRule.id)).where(VocabRule.wrong_text == "彩玲")
        )).scalar()
    check("and is not duplicated", n, 1)

    # More evidence arrives for the below-threshold pair; it should now qualify.
    async with async_session() as s:
        s.add(Correction(id=999, segment_id=1, original_text="他是全智隆",
                         suggested_text="他是權志龍", submitter_name="丁",
                         status="approved"))
        await s.commit()
    async with async_session() as s:
        third = await mine_rules_from_corrections(s)
    check("qualifies once evidence reaches the threshold",
          [(w, r) for w, r, _, _ in third], [("全智隆", "權志龍")])

    await engine.dispose()


print("\n--- mining rules from approved corrections ---")
from sqlalchemy import func, select  # noqa: E402

asyncio.run(_mining_checks())

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall vocab checks passed\033[0m")
