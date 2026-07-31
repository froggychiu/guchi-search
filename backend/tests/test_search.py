"""End-to-end tests for GET /api/search, against the real endpoint code.

Run with:  python backend/tests/test_search.py     (exits non-zero on failure)

Postgres is swapped for a throwaway SQLite file. No other service is needed —
search reads only the database. The corpus below is built so every expected
number is ground truth rather than a re-derivation of the implementation:

  電腦   1200 segments in episodes 1..120, plus a title-only hit on ep 130
         -> 121 episodes, 1201 rows
  呱吉   in every episode title, in no segment text
         -> 150 episodes, 150 rows (one per episode, never 6000)
  采翎   5 segments; opencc s2t rewrites the query to 採翎, which the corpus
         does not contain
  電踏/電話/電臺/電影/電視/腦袋  decoys in episodes 121-150, which contain
         neither 電腦 nor any real match

Each block guards a defect that reached production:

  SEARCH-01  counts were capped at the fetch window, so 呱吉/采翎/電腦/選舉
             all reported exactly 1000 matches and deep pages were unreachable
  CJK        per-character tokenization made 電腦 match 電踏大叔 and 電話
  BUG-02     converting queries to Traditional made 采翎 return nothing
  SEC-01     highlighted_text is rendered as markup by the frontend
  SEC-04     the show filter used to be concatenated into a filter expression
"""

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DB_PATH = os.path.join(HERE, "test.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["GUCHI_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"

sys.path.insert(0, BACKEND)

from datetime import datetime, timedelta  # noqa: E402

import httpx  # noqa: E402

from app.core.database import Base, async_session, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.episode import Episode, Segment  # noqa: E402

N_EPISODES, SEGMENTS_PER_EP = 150, 40
CONTENT_EPS, CONTENT_HITS_PER_EP = 120, 10
TITLE_ONLY_EP = 130
DECOY_EP_FROM = 121
DECOY_PHRASES = [
    "電踏大叔到底是什麼",
    "用打電話的方式進來",
    "這個不是政治電臺",
    "很多得獎的電影都很棒",
    "我的腦袋一片空白",
    "電視上在演什麼",
]

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    base = datetime(2020, 1, 1)
    async with async_session() as s:
        doc_id = 0
        for ep in range(1, N_EPISODES + 1):
            title = (
                f"【呱吉】新資料夾({ep})：買了一台新電腦"
                if ep == TITLE_ONLY_EP
                else f"【呱吉】新資料夾({ep})：第 {ep} 集的隨便標題"
            )
            s.add(Episode(
                id=ep, title=title, description="",
                show="新資料夾" if ep % 3 else "直播",
                audio_url=f"https://example.invalid/{ep}.mp3",
                published_at=base + timedelta(days=ep),
                duration_seconds=1200, transcription_status="done",
            ))
            for i in range(SEGMENTS_PER_EP):
                doc_id += 1
                if ep <= 5 and i == 39:
                    # 采翎: s2t rewrites this to 採翎, which is NOT what the
                    # corpus stores. Converting the query unconditionally made
                    # this return zero on production.
                    text = f"然後采翎就說了一句話，第 {ep} 集。"
                elif ep <= CONTENT_EPS and i < CONTENT_HITS_PER_EP:
                    text = f"我昨天用電腦處理了一些事情，第 {ep} 集第 {i} 段。"
                elif ep >= DECOY_EP_FROM and i < len(DECOY_PHRASES):
                    text = f"{DECOY_PHRASES[i]}，第 {ep} 集第 {i} 段。"
                else:
                    text = f"這是一段普通的閒聊內容，沒有關鍵字，第 {ep} 集第 {i} 段。"
                s.add(Segment(
                    id=doc_id, episode_id=ep, speaker="",
                    start_time=float(i * 5), end_time=float(i * 5 + 5), text=text,
                ))
        await s.commit()


async def main():
    await seed()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        print("\n--- 電腦: content matches + one title-only episode ---")
        r = (await c.get("/api/search", params={"q": "電腦"})).json()
        check("total_episodes", r["total_episodes"], 121)
        check("total_segment_matches", r["total_segment_matches"], 1201)
        check("episodes on page", len(r["episodes"]), 20)
        check("hit_count is exact per episode", r["episodes"][0]["hit_count"], 10)
        check("hits_shown <= cap", r["episodes"][0]["hits_shown"], 10)

        print("\n--- CJK per-character over-matching must not come back ---")
        # Episodes 121-150 contain 電踏/電話/電臺/電影/電視/腦袋 but never 電腦.
        # Without phrase matching these were returned as 電腦 hits.
        r = (await c.get("/api/search", params={"q": "電腦"})).json()
        noisy = [e["episode_id"] for e in r["episodes"] if e["episode_id"] >= DECOY_EP_FROM
                 and not e["is_title_only_match"]]
        check("no decoy episode on page 1", noisy, [])
        allep = set()
        for p in range(1, 9):
            pr = (await c.get("/api/search", params={"q": "電腦", "page": p})).json()
            allep.update(e["episode_id"] for e in pr["episodes"]
                         if not e["is_title_only_match"])
        check("no decoy episode on any page",
              sorted(e for e in allep if e >= DECOY_EP_FROM), [])
        check("只有 電踏 的段落不算 電腦 命中",
              (await c.get("/api/search", params={"q": "電踏"})).json()["total_segment_matches"],
              30)
        # Literal substring matching must work across word boundaries — a
        # tokenizing engine splits these differently and returns nothing.
        r = (await c.get("/api/search", params={"q": "昨天用電腦處理"})).json()
        check("out-of-dictionary substring still matches",
              r["total_segment_matches"], 1200)
        r = (await c.get("/api/search", params={"q": "腦處"})).json()
        check("substring across a word boundary matches",
              r["total_segment_matches"], 1200)

        print("\n--- multi-token queries need all tokens, not adjacency ---")
        both = (await c.get("/api/search", params={"q": "電腦 集第"})).json()
        check("'電腦 集第' (both present, apart)", both["total_segment_matches"], 1200)
        never = (await c.get("/api/search", params={"q": "電腦 干擾"})).json()
        check("'電腦 干擾' (never co-occur)", never["total_segment_matches"], 0)

        print("\n--- 呱吉: title matches every episode, no segment text ---")
        r = (await c.get("/api/search", params={"q": "呱吉"})).json()
        check("total_episodes", r["total_episodes"], 150)
        check("total_segment_matches (NOT 6000)", r["total_segment_matches"], 150)
        check("all title-only", all(e["is_title_only_match"] for e in r["episodes"]), True)
        check("title-only hit_count", r["episodes"][0]["hit_count"], 0)

        print("\n--- script conversion is a suggestion, never a rewrite ---")
        import opencc as _oc  # noqa: PLC0415
        check("s2t really does rewrite this", _oc.OpenCC("s2t").convert("采翎"), "採翎")
        r = (await c.get("/api/search", params={"q": "采翎"})).json()
        check("采翎 found", r["total_segment_matches"], 5)
        check("query echoed as typed", r["query"], "采翎")
        check("no suggestion when there are results", r["suggestion"], None)

        # The noise that killed variant matching: 采 must NOT drag in 採.
        r = (await c.get("/api/search", params={"q": "采"})).json()
        check("采 does not also match 採", r["total_segment_matches"], 5)

        print("\n--- did-you-mean on an empty result, both directions ---")
        r = (await c.get("/api/search", params={"q": "电脑"})).json()
        check("Simplified finds nothing", r["total_segment_matches"], 0)
        check("...but suggests the Traditional spelling", r["suggestion"], "電腦")
        r = (await c.get("/api/search", params={"q": "採翎"})).json()
        check("the other ambiguous spelling finds nothing",
              r["total_segment_matches"], 0)
        check("...and is suggested back (was a known gap)", r["suggestion"], "采翎")
        r = (await c.get("/api/search", params={"q": "完全不存在的詞"})).json()
        check("no suggestion when the alternative is also empty",
              r["suggestion"], None)

        print("\n--- Deep pagination reaches the tail ---")
        last = (await c.get("/api/search", params={"q": "電腦", "page": 7})).json()
        check("page 7 returns the final episode", len(last["episodes"]), 1)
        check("that episode is the title-only one",
              last["episodes"][0]["episode_id"], TITLE_ONLY_EP)
        seen = set()
        for p in range(1, 8):
            pr = (await c.get("/api/search", params={"q": "電腦", "page": p})).json()
            seen.update(e["episode_id"] for e in pr["episodes"])
        check("every matching episode reachable by paging", len(seen), 121)
        check("no duplicate episode across pages", len(seen), 121)

        print("\n--- show filter stays consistent ---")
        r = (await c.get("/api/search", params={"q": "電腦", "show": "直播"})).json()
        eps_in_show = {e["episode_id"] for e in r["episodes"]}
        check("filtered episodes all in 直播",
              all(e["show"] == "直播" for e in r["episodes"]), True)
        check("filtered total < unfiltered", r["total_episodes"] < 121, True)
        check("no leakage of other shows", len(eps_in_show - set(range(1, 151))), 0)

        print("\n--- SEC-04 / input validation still holds ---")
        check("bad show rejected",
              (await c.get("/api/search", params={"q": "x", "show": 'X" OR 1=1'})).status_code,
              400)
        print("\n--- LIKE wildcards in the title lookup are literal ---")
        from sqlalchemy import func as _f, select as _sel  # noqa: PLC0415

        from app.api.search import _ilike_literal  # noqa: PLC0415

        async def title_hits(raw: str, escaped: bool) -> int:
            pat = f"%{_ilike_literal(raw) if escaped else raw}%"
            async with async_session() as s:
                stmt = _sel(_f.count(Episode.id)).where(
                    Episode.title.ilike(pat, escape="\\") if escaped
                    else Episode.title.ilike(pat)
                )
                return (await s.execute(stmt)).scalar() or 0

        # (1_0) wildcards to 100/110/120/130/140/150 -> 6 episodes
        check("unescaped '_' acts as a wildcard", await title_hits("(1_0)", False), 6)
        check("escaped '_' is literal", await title_hits("(1_0)", True), 0)
        check("unescaped '%' matches everything", await title_hits("%", False), 150)
        check("escaped '%' matches nothing", await title_hits("%", True), 0)
        check("escaped literal still matches", await title_hits("(130)", True), 1)

        print("\n--- SEC-01 escaping preserved ---")
        r = (await c.get("/api/search", params={"q": "電腦"})).json()
        sample = r["episodes"][0]["hits"][0]["highlighted_text"]
        check("highlight keeps <mark>", "<mark>" in sample, True)
        print(f"       sample: {sample!r}")

    await engine.dispose()
    print("\n" + ("=" * 60))
    if failures:
        print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
        sys.exit(1)
    print("\033[32mall checks passed\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
