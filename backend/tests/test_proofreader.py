"""Tests for AI proofreading (services/proofreader.py) and what it touches.

Run with:  python backend/tests/test_proofreader.py

No network: the language model is replaced by a fake that answers from fixed
tables, so what is under test is everything around the model — the pinyin
guard, which verdicts reach a transcript, how results land in the
corrections table, and that the rest of the site treats AI corrections as
software rather than as a very prolific person:

  1. Only verifier-approved fixes change a segment. Unsure ones wait in the
     queue, rejected ones are recorded and never applied.
  2. A segment with a human correction already pending is not touched.
  3. AI corrections never reach the leaderboard, never count as evidence for
     glossary rules, and never use up the public submission limit.
  4. --revert-proofread restores the original text, except where someone has
     edited the segment since.
"""

import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DB_PATH = os.path.join(HERE, "proofreader_test.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
os.environ["GUCHI_INGEST_SECRET"] = "test-secret-value"

sys.path.insert(0, BACKEND)

from datetime import datetime  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import Base, async_session, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.episode import AI_PROOFREADER, Correction, Episode, Segment, VocabRule  # noqa: E402
from app.services import proofreader  # noqa: E402
from app.services.proofreader import (  # noqa: E402
    PROPOSE_SYSTEM,
    proofread_episode,
    revert_episode,
    review_episode,
    sounds_alike,
)
from app.services.vocab import mine_rules_from_corrections  # noqa: E402

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


# What the fake proposer suggests wherever it sees the left side, and what the
# fake verifier says about it. 瓜子 -> 呱吉 is proposed but does not sound
# alike, so the guard must drop it before the verifier ever sees it.
PROPOSALS = {
    "旁客": "龐克",
    "藍雨": "蘭嶼",
    "完人": "萬人",
    "冬夜": "東燁",
    "瓜子": "呱吉",
}
VERDICTS = {
    ("旁客", "龐克"): "approve",
    ("藍雨", "蘭嶼"): "approve",
    ("完人", "萬人"): "reject",
    ("冬夜", "東燁"): "unsure",
}

verify_calls = []


def fake_complete(model, system, user):
    if system is PROPOSE_SYSTEM:
        fixes = []
        for m in re.finditer(r"^(\d+) \[可改\] (.*)$", user, re.M):
            line, text = int(m.group(1)), m.group(2)
            for wrong, right in PROPOSALS.items():
                if wrong in text:
                    fixes.append({"line": line, "from": wrong, "to": right, "why": "同音"})
        # Malformed proposals the guard must discard: a line outside the
        # window, and a quote that is not in the line.
        fixes.append({"line": 9999, "from": "旁客", "to": "龐克"})
        fixes.append({"line": 0, "from": "不存在", "to": "布村在"})
        return {"fixes": fixes}
    verify_calls.append(user)
    verdicts = []
    for m in re.finditer(r"^\[(\d+)\].*?^修改：(\S+) → (\S+)$", user, re.M | re.S):
        verdicts.append({"k": int(m.group(1)),
                         "v": VERDICTS[(m.group(2), m.group(3))], "why": "測試"})
    return {"verdicts": verdicts}


# Episode 1 segments, by index.
EP1 = [
    "這也是旁客嗎",           # 0 approve
    "我在玩旁客的旁客",       # 1 approve, both occurrences
    "太原五百完人紀念館",     # 2 reject
    "冬夜說這很旁客",         # 3 approve + unsure on one line
    "藍雨很旁客",             # 4 a human correction is already pending
    "瓜子很好吃",             # 5 not a homophone fix -> dropped by the guard
    "今天天氣很好",           # 6 nothing to fix
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        for ep in (1, 2):
            s.add(Episode(
                id=ep, title=f"台北討厭龐克 {ep}", description="",
                show="直播", audio_url=f"https://example.invalid/{ep}.mp3",
                published_at=datetime(2026, 1, ep), duration_seconds=600,
                transcription_status="done",
            ))
        for i, text in enumerate(EP1):
            s.add(Segment(id=100 + i, episode_id=1, speaker="",
                          start_time=float(i), end_time=i + 1.0, text=text))
        s.add(Segment(id=200, episode_id=2, speaker="",
                      start_time=0.0, end_time=1.0, text="我們去藍雨玩"))
        s.add(Correction(segment_id=104, original_text="藍雨很旁客",
                         suggested_text="蘭嶼很龐克", submitter_name="阿明",
                         status="pending"))
        await s.commit()


async def texts(episode_id):
    async with async_session() as s:
        rows = await s.execute(select(Segment.text).where(Segment.episode_id == episode_id)
                               .order_by(Segment.start_time))
        return list(rows.scalars())


async def ai_corrections():
    async with async_session() as s:
        rows = await s.execute(select(Correction).where(Correction.submitter_name == AI_PROOFREADER)
                               .order_by(Correction.id))
        return rows.scalars().all()


def unit_checks():
    print("--- the pinyin guard ---")
    check("旁客 / 龐克 sound alike", sounds_alike("旁客", "龐克"), True)
    check("完人 / 萬人 sound alike (the guard cannot judge meaning)",
          sounds_alike("完人", "萬人"), True)
    check("瓜子 / 呱吉 do not", sounds_alike("瓜子", "呱吉"), False)
    check("Taiwanese merges count: 是 / 四", sounds_alike("是", "四"), True)
    check("length change is never a homophone fix", sounds_alike("沃馬", "沃爾瑪"), False)
    check("empty is rejected", sounds_alike("", ""), False)

    print("\n--- a failed verifier call sends fixes to a human, not to the transcript ---")

    def verifier_down(model, system, user):
        if system is PROPOSE_SYSTEM:
            return fake_complete(model, system, user)
        raise RuntimeError("503")

    fixes, usage = review_episode("t", "", ["這也是旁客嗎"], verifier_down, "p", "v")
    check("verdicts", [f.verdict for f in fixes], ["unsure"])
    check("the failure is counted", usage.failed, 1)


async def main():
    unit_checks()
    await seed()

    print("\n--- dry run writes nothing ---")
    async with async_session() as s:
        ep = await s.get(Episode, 1)
        result = await proofread_episode(s, ep, fake_complete, "p", "v", dry_run=True)
        check("fixes found", len(result.fixes) > 0, True)
    check("transcript unchanged", await texts(1), EP1)
    check("no AI corrections", len(await ai_corrections()), 0)
    async with async_session() as s:
        check("episode not marked", (await s.get(Episode, 1)).proofread_at, None)

    print("\n--- a real run ---")
    async with async_session() as s:
        ep = await s.get(Episode, 1)
        result = await proofread_episode(s, ep, fake_complete, "p", "v")
    check("guard dropped 瓜子->呱吉 and the malformed ones",
          sorted({(f.wrong, f.right) for f in result.fixes}),
          sorted(VERDICTS))
    check("segments changed", result.applied, 3)
    check("left for review", result.pending, 1)
    check("rejected", result.rejected, 1)
    check("skipped (human edit pending)", result.skipped, 1)
    check("transcript", await texts(1), [
        "這也是龐克嗎",
        "我在玩龐克的龐克",
        "太原五百完人紀念館",
        "冬夜說這很龐克",
        "藍雨很旁客",
        "瓜子很好吃",
        "今天天氣很好",
    ])

    rows = await ai_corrections()
    by_seg = {}
    for c in rows:
        by_seg.setdefault(c.segment_id, []).append(c)
    check("approved fix is recorded with the original kept",
          [(c.status, c.original_text, c.suggested_text) for c in by_seg[100]],
          [("approved", "這也是旁客嗎", "這也是龐克嗎")])
    check("unsure fix stacks on the applied one",
          [(c.status, c.original_text, c.suggested_text) for c in by_seg[103]],
          [("approved", "冬夜說這很旁客", "冬夜說這很龐克"),
           ("pending", "冬夜說這很龐克", "東燁說這很龐克")])
    check("rejected fix is recorded, not applied",
          [(c.status, c.suggested_text) for c in by_seg[102]],
          [("rejected", "太原五百萬人紀念館")])
    check("the human's segment is untouched by AI", 104 in by_seg, False)
    check("note says what changed", by_seg[100][0].note, "旁客→龐克（測試）")
    async with async_session() as s:
        check("episode marked proofread", (await s.get(Episode, 1)).proofread_at is not None, True)

    print("\n--- auto-approve off: approved fixes wait for a human too ---")
    async with async_session() as s:
        ep = await s.get(Episode, 2)
        result = await proofread_episode(s, ep, fake_complete, "p", "v", auto_approve=False)
    check("nothing applied", (result.applied, result.pending), (0, 1))
    check("transcript unchanged", await texts(2), ["我們去藍雨玩"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        auth = {"X-Ingest-Secret": "test-secret-value"}

        print("\n--- the review queue splits AI from people ---")
        ai = (await c.get("/api/corrections?status=pending&source=ai", headers=auth)).json()
        human = (await c.get("/api/corrections?status=pending&source=human", headers=auth)).json()
        both = (await c.get("/api/corrections?status=pending", headers=auth)).json()
        check("AI pending", ai["total"], 2)
        check("human pending", [x["submitter_name"] for x in human["corrections"]], ["阿明"])
        check("default shows both", both["total"], 3)
        check("counts", both["counts"], {"all": 3, "ai": 2, "human": 1})
        check("note is served", sorted(x["note"] for x in ai["corrections"]),
              ["冬夜→東燁（測試）", "藍雨→蘭嶼（測試）"])
        bad = await c.get("/api/corrections?source=bots", headers=auth)
        check("unknown source is refused", bad.status_code, 422)

        print("\n--- AI is not a contributor ---")
        board = (await c.get("/api/corrections/contributors")).json()
        check("leaderboard", [x["name"] for x in board["contributors"]], [])
        ep1 = (await c.get("/api/episodes/1")).json()
        check("episode credit line", ep1["contributors"], [])

        print("\n--- AI corrections do not use up the public limit ---")
        async with async_session() as s:
            for i in range(600):
                s.add(Correction(segment_id=106, original_text="今天天氣很好",
                                 suggested_text="今天天氣很好。", submitter_name=AI_PROOFREADER,
                                 status="rejected"))
            await s.commit()
        r = await c.post("/api/corrections", json={
            "segment_id": 105, "suggested_text": "呱吉很好吃", "submitter_name": "小美"})
        check("a person can still submit", r.status_code, 200)

    print("\n--- AI corrections are not glossary evidence ---")
    async with async_session() as s:
        for i in range(5):
            s.add(Correction(segment_id=106, original_text="他是旁客",
                             suggested_text="他是龐克", submitter_name=AI_PROOFREADER,
                             status="approved"))
        await s.commit()
        proposed = await mine_rules_from_corrections(s)
        rules = (await s.execute(select(VocabRule))).scalars().all()
    check("no rule proposed", (proposed, len(rules)), ([], 0))

    print("\n--- revert ---")
    async with async_session() as s:
        # Someone fixed segment 1 by hand after the AI did: keep theirs.
        seg = await s.get(Segment, 101)
        seg.text = "我在玩龐克的龐克啦"
        await s.commit()
        # The five fake approvals above sit on segment 106, whose text never
        # matched them; they must be left alone rather than "restored".
        reverted, left_alone = await revert_episode(s, 1)
    check("reverted / left alone", (reverted, left_alone), (2, 6))
    check("transcript", await texts(1), [
        "這也是旁客嗎",
        "我在玩龐克的龐克啦",
        "太原五百完人紀念館",
        "冬夜說這很旁客",
        "藍雨很旁客",
        "瓜子很好吃",
        "今天天氣很好",
    ])
    rows = await ai_corrections()
    check("pending AI correction withdrawn",
          [c.status for c in rows if c.segment_id == 103], ["rejected", "rejected"])
    async with async_session() as s:
        human = (await s.execute(select(Correction).where(Correction.submitter_name == "阿明"))).scalar_one()
        check("阿明's correction", human.status, "pending")

    await engine.dispose()
    print("\n" + ("=" * 60))
    if failures:
        print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
        sys.exit(1)
    print("\033[32mall checks passed\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
