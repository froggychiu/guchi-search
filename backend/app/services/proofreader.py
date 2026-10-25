"""AI proofreading: catch the homophone mistakes Whisper makes, in two passes.

Whisper hears the sound right and picks the wrong characters — 旁客 for 龐克,
藍雨 for 蘭嶼, 冬夜 for 東燁. Neither the prompt nor the glossary can fix this:
the prompt holds ~200 characters, and the glossary only learns a mistake after
three proofreaders have fixed it by hand, while most of these appear a handful
of times in one episode. What fixes them is reading the sentence in context,
which is what a language model is for.

Two passes, because one model on its own was not trustworthy enough to write
to transcripts. Measured on five unproofread episodes (2026-10-25):

  proposer   gpt-5.4-mini  ~460 suggestions, 256 survived the pinyin guard,
                           ~95% of those correct
  verifier   gpt-5.4       rejected the wrong ones it was shown — all four
                           太原五百完人 → 五百萬人, 藍雨 → 爛雨, a made-up
                           councillor's name — and corrected the proposer's
                           own mistakes (邱薇姐 is 邱威傑, not 邱薇潔)

So the proposer suggests, a pinyin check throws out anything that does not
sound like the original, and the verifier decides:

  approve  -> applied, recorded as an approved correction (reversible)
  unsure   -> pending, for a human in /admin
  reject   -> recorded as rejected, never applied

Every write goes through the corrections table with original_text kept, so
`ingest --revert-proofread --episode-id N` can put an episode back.
"""

import asyncio
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from pypinyin import Style, pinyin
from sqlalchemy import select

from app.models.episode import AI_PROOFREADER as AI_SUBMITTER, Correction, Segment


# Segments the proposer may edit per call, and read-only context either side.
# Segments are short (~7 characters), so a window is roughly a minute or two
# of speech — enough for the model to tell what the conversation is about.
WINDOW = 120
CONTEXT = 20
# Proposed fixes judged per verifier call.
VERIFY_BATCH = 15
# Parallel API calls. Both providers rate limit per key, and ingest is not in a
# hurry; this keeps a 2-hour live stream to well under a minute.
WORKERS = 6

SHOW_CONTEXT = (
    "節目是台灣的呱吉 Podcast／直播（新資料夾、呱吉電台、人生晚長、陪你下班等系列），台灣華語口語，"
    "常夾雜英文與台語。固定人物：呱吉（本名邱威傑，主持人）、采翎（新資料夾共同主持人）、"
    "東燁（采翎的男友）、佳佳（辦公室主任、呱吉助理）、祐先（呱吉的寫手）。"
)

PROPOSE_SYSTEM = """你是台灣華語 Podcast 逐字稿的校對員。逐字稿由語音辨識（Whisper）產生，最常見的錯誤是「同音或近音字選錯」，例如把「龐克」辨識成「旁客」、把「采翎」辨識成「彩玲」、把「只是」轉成「隻是」。

你的工作：只找出這類「聽對了音、寫錯了字」的錯誤並修正。

嚴格規則：
1. 只修改讀音相同或相近、但用字明顯錯誤的詞。修正後的讀音必須和原文幾乎一樣。
2. 不要潤稿、不要改語序、不要補字刪字、不要改標點、不要把口語改成書面語、不要修正說話者本人的口誤或文法。
3. 不要改「台／臺」這類異體字選擇。
4. 沒有把握就不要改。寧可漏改，不可錯改。一個詞在上下文裡講得通就保持原樣。
5. 人名、團名、作品名請依節目標題、簡介與上下文判斷正確寫法。
6. 只能修改標記為 [可改] 區段內的行；[僅供參考] 的行是前後文，不可修改。

輸出 JSON：{"fixes":[{"line":行號,"from":"原文中要替換的最短片段","to":"修正後片段","why":"十字內理由"}]}
from 必須逐字出現在該行原文中，盡量只包含錯字所在的詞（2到6字）。沒有錯誤就輸出 {"fixes":[]}。"""

VERIFY_SYSTEM = """你是台灣華語 Podcast 逐字稿的資深審稿人。另一個校對員針對語音辨識的同音錯字提出了修改，你要逐條判斷是否採納。

判斷原則：
- approve：原文明顯是錯字，修改後在上下文中正確，且你確定修改後的寫法是真實存在的正確用法（人名、地名、品牌、專有名詞要你確定存在）。
- reject：原文本身就是正確或說得通的詞（包括冷僻的專有名詞、歷史名詞、口語、俚語、異體字），或修改改錯了、改壞了語意。
- unsure：你無法確定。專有名詞你不確定是否存在時一律 unsure，不要猜。

輸出 JSON：{"verdicts":[{"k":編號,"v":"approve|reject|unsure","why":"十五字內"}]}"""


# A completion call: (model, system prompt, user message) -> parsed JSON reply.
# Injected so tests and dry runs never need a network.
Complete = Callable[[str, str, str], dict]


@dataclass
class Fix:
    index: int          # position in the episode's segment list
    wrong: str
    right: str
    why: str = ""
    verdict: str = ""   # approve / reject / unsure, set by the verifier
    verdict_why: str = ""


@dataclass
class Usage:
    calls: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


# --- the pinyin guard ---------------------------------------------------------

# Merges Taiwanese Mandarin makes in casual speech, which Whisper then
# inherits: 四/是, 牛/流, 心/星, 飛/揮. A fix that differs only by one of these
# still "sounds the same" to a listener.
_INITIAL_MERGE = [("zh", "z"), ("ch", "c"), ("sh", "s"), ("n", "l"), ("f", "h"), ("r", "l")]
_FINAL_MERGE = [("ing", "in"), ("eng", "en"), ("ang", "an")]


def _norm(syllable: str) -> str:
    for a, b in _INITIAL_MERGE:
        if syllable.startswith(a):
            syllable = b + syllable[len(a):]
            break
    for a, b in _FINAL_MERGE:
        if syllable.endswith(a):
            syllable = syllable[: -len(a)] + b
            break
    return syllable


def _readings(text: str) -> list[set[str]]:
    return [
        {_norm(r) for r in readings}
        for readings in pinyin(text, style=Style.NORMAL, heteronym=True,
                               errors=lambda chars: list(chars))
    ]


def sounds_alike(wrong: str, right: str) -> bool:
    """Whether `right` could be what was said when Whisper wrote `wrong`.

    Same length, and every character pair shares at least one reading after
    the merges above. Deliberately blunt: it cannot tell a good fix from a bad
    one (完人 and 萬人 sound identical), only throw out the edits that are not
    homophone fixes at all — rewrites, insertions, English spellings. Those go
    nowhere; a human can still fix them through the normal form.
    """
    if not wrong or len(wrong) != len(right):
        return False
    a, b = _readings(wrong), _readings(right)
    return len(a) == len(b) and all(x & y for x, y in zip(a, b))


# --- pass 1: propose ---------------------------------------------------------

def _episode_header(title: str, description: str | None) -> list[str]:
    desc = re.sub(r"\s+", " ", description or "")[:800]
    return [f"節目背景：{SHOW_CONTEXT}", f"本集標題：{title}", f"本集簡介：{desc}", ""]


def _propose_window(complete: Complete, model: str, header: list[str],
                    texts: list[str], lo: int, hi: int) -> list[Fix]:
    lines = list(header)
    for i in range(max(0, lo - CONTEXT), min(len(texts), hi + CONTEXT)):
        tag = "[可改]" if lo <= i < hi else "[僅供參考]"
        lines.append(f"{i} {tag} {texts[i]}")
    reply = complete(model, PROPOSE_SYSTEM, "\n".join(lines))

    fixes = []
    for f in reply.get("fixes") or []:
        i, wrong, right = f.get("line"), f.get("from") or "", f.get("to") or ""
        # The model is told which lines it may touch and to quote the text it
        # replaces. Anything else is a misread of the instructions, not a fix.
        if not isinstance(i, int) or not lo <= i < hi:
            continue
        if not wrong or wrong == right or wrong not in texts[i]:
            continue
        if not sounds_alike(wrong, right):
            continue
        fixes.append(Fix(index=i, wrong=wrong, right=right, why=str(f.get("why") or "")[:40]))
    return fixes


# --- pass 2: verify ----------------------------------------------------------

def _verify_batch(complete: Complete, model: str, title: str,
                  texts: list[str], batch: list[tuple[int, Fix]]) -> None:
    lines = [f"節目背景：{SHOW_CONTEXT}", ""]
    for k, fix in batch:
        i = fix.index
        context = "／".join(texts[max(0, i - 6): i + 7])
        lines.append(f"[{k}] 本集：{title}\n上下文：{context}\n"
                     f"原句：{texts[i]}\n修改：{fix.wrong} → {fix.right}\n")
    reply = complete(model, VERIFY_SYSTEM, "\n".join(lines))

    by_key = {}
    for v in reply.get("verdicts") or []:
        if isinstance(v, dict) and isinstance(v.get("k"), int):
            by_key[v["k"]] = v
    for k, fix in batch:
        v = by_key.get(k) or {}
        verdict = v.get("v")
        # Anything the verifier did not clearly approve or reject — a missing
        # entry, a typo in the verdict — goes to a human rather than either
        # silently applying or silently vanishing.
        fix.verdict = verdict if verdict in ("approve", "reject") else "unsure"
        fix.verdict_why = str(v.get("why") or "")[:40]


def review_episode(
    title: str,
    description: str | None,
    texts: list[str],
    complete: Complete,
    propose_model: str,
    verify_model: str,
) -> tuple[list[Fix], Usage]:
    """Run both passes over one episode's segment texts. No database access.

    A failed API call loses that window or batch rather than the episode: its
    proposals are simply not made, or its verdicts default to unsure.
    """
    usage = Usage()
    header = _episode_header(title, description)

    def guarded(fn, *args):
        usage.calls += 1
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 — one bad call must not sink the episode
            usage.failed += 1
            usage.errors.append(f"{type(e).__name__}: {e}"[:200])
            return None

    windows = [(lo, min(lo + WINDOW, len(texts))) for lo in range(0, len(texts), WINDOW)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(
            lambda w: guarded(_propose_window, complete, propose_model, header, texts, *w),
            windows,
        )
        fixes = [f for found in results if found for f in found]

    # The same fix proposed twice for one line (overlapping reasoning, not
    # overlapping windows — those cannot happen) is judged once.
    seen, unique = set(), []
    for f in fixes:
        key = (f.index, f.wrong, f.right)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    fixes = unique

    keyed = list(enumerate(fixes))
    batches = [keyed[j:j + VERIFY_BATCH] for j in range(0, len(keyed), VERIFY_BATCH)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(
            lambda b: guarded(_verify_batch, complete, verify_model, title, texts, b),
            batches,
        ))
    for f in fixes:
        if not f.verdict:  # its verifier call failed
            f.verdict = "unsure"
            f.verdict_why = "複審呼叫失敗"
    return fixes, usage


# --- grouping fixes into corrections ----------------------------------------

def apply_fixes(text: str, fixes: list[Fix]) -> str:
    """Apply fixes to one segment's text, skipping any no longer applicable.

    Every occurrence on the line is replaced: a word misheard twice in one
    sentence is the same mistake twice, and the model quotes it once. Two
    fixes can overlap (旁客 → 龐克 and 很旁客 → 很龐克); once the first lands
    the second no longer matches, which is the right outcome.
    """
    for f in fixes:
        if f.wrong in text:
            text = text.replace(f.wrong, f.right)
    return text


def describe(fixes: list[Fix]) -> str:
    """The note shown to a reviewer: what changed and why, in one line."""
    parts = []
    for f in fixes:
        reason = f.verdict_why or f.why
        parts.append(f"{f.wrong}→{f.right}" + (f"（{reason}）" if reason else ""))
    return "；".join(parts)[:300]


def openai_complete(api_key: str) -> Complete:
    """The production completion function: OpenAI chat completions, JSON mode."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, max_retries=3, timeout=120)

    def complete(model: str, system: str, user: str) -> dict:
        kwargs = {}
        if model.startswith("gpt-5"):
            # The settings the 2026-10-25 measurement used: the proposer is
            # cheap and plentiful, the verifier is where care matters.
            kwargs["reasoning_effort"] = "medium" if system is VERIFY_SYSTEM else "low"
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            **kwargs,
        )
        return json.loads(resp.choices[0].message.content or "{}")

    return complete


# --- writing results ---------------------------------------------------------

@dataclass
class EpisodeResult:
    applied: int = 0      # segments changed
    pending: int = 0      # corrections left for a human
    rejected: int = 0     # corrections recorded as rejected
    skipped: int = 0      # segments passed over: a human edit is already pending
    usage: Usage = field(default_factory=Usage)
    fixes: list[Fix] = field(default_factory=list)


async def proofread_episode(
    db,
    episode,
    complete: Complete,
    propose_model: str,
    verify_model: str,
    *,
    auto_approve: bool = True,
    dry_run: bool = False,
) -> EpisodeResult:
    """Proofread one episode and file the results as corrections.

    Per segment, at most one correction of each kind: the approved fixes as
    one approved correction (already applied), the unsure ones as one pending
    correction on top of that, the rejected ones as one rejected record. A
    segment that already has a pending correction is skipped — someone is
    working on it, and approving theirs later would overwrite ours or the
    reverse.

    With auto_approve off, approved fixes join the unsure ones in the pending
    queue instead of being applied.
    """
    segments = (await db.execute(
        select(Segment).where(Segment.episode_id == episode.id).order_by(Segment.start_time)
    )).scalars().all()
    texts = [s.text or "" for s in segments]

    fixes, usage = await asyncio.to_thread(
        review_episode, episode.title, episode.description, texts,
        complete, propose_model, verify_model,
    )
    result = EpisodeResult(usage=usage, fixes=fixes)
    if dry_run:
        return result

    busy = set((await db.execute(
        select(Correction.segment_id).where(
            Correction.status == "pending",
            Correction.segment_id.in_([s.id for s in segments]),
        )
    )).scalars().all())

    by_segment: dict[int, list[Fix]] = {}
    for f in fixes:
        by_segment.setdefault(f.index, []).append(f)

    now = datetime.utcnow()
    for index, seg_fixes in sorted(by_segment.items()):
        segment = segments[index]
        if segment.id in busy:
            result.skipped += 1
            continue

        approve = [f for f in seg_fixes if f.verdict == "approve"]
        unsure = [f for f in seg_fixes if f.verdict == "unsure"]
        reject = [f for f in seg_fixes if f.verdict == "reject"]
        if not auto_approve:
            unsure, approve = approve + unsure, []

        if approve:
            before = segment.text
            after = apply_fixes(before, approve)
            if after != before:
                db.add(Correction(
                    segment_id=segment.id, original_text=before, suggested_text=after,
                    submitter_name=AI_SUBMITTER, status="approved", reviewed_at=now,
                    note=describe(approve),
                ))
                segment.text = after
                result.applied += 1

        if unsure:
            before = segment.text
            after = apply_fixes(before, unsure)
            if after != before:
                db.add(Correction(
                    segment_id=segment.id, original_text=before, suggested_text=after,
                    submitter_name=AI_SUBMITTER, status="pending",
                    note=describe(unsure),
                ))
                result.pending += 1

        if reject:
            # Kept so a reviewer can see what the verifier stopped, and so the
            # verifier's judgement can be audited — never applied.
            before = segment.text
            after = apply_fixes(before, reject)
            if after != before:
                db.add(Correction(
                    segment_id=segment.id, original_text=before, suggested_text=after,
                    submitter_name=AI_SUBMITTER, status="rejected", reviewed_at=now,
                    note=describe(reject),
                ))
                result.rejected += 1

    episode.proofread_at = now
    await db.commit()
    return result


async def revert_episode(db, episode_id: int) -> tuple[int, int]:
    """Undo every applied AI correction on an episode.

    Restores original_text only where the segment still holds exactly what AI
    proofreading wrote. A segment someone has since edited by hand is left
    alone and counted, because restoring it would throw their edit away.
    Pending AI corrections on the episode are withdrawn too.

    Returns (reverted, left_alone).
    """
    rows = (await db.execute(
        select(Correction, Segment)
        .join(Segment, Segment.id == Correction.segment_id)
        .where(
            Segment.episode_id == episode_id,
            Correction.submitter_name == AI_SUBMITTER,
            Correction.status.in_(("approved", "pending")),
        )
        # Newest first, so stacked corrections unwind in the right order.
        .order_by(Correction.id.desc())
    )).all()

    reverted = left_alone = 0
    now = datetime.utcnow()
    for correction, segment in rows:
        if correction.status == "approved":
            if segment.text != correction.suggested_text:
                left_alone += 1
                continue
            segment.text = correction.original_text
            reverted += 1
        correction.status = "rejected"
        correction.reviewed_at = now
        correction.note = ((correction.note or "") + "（已還原）")[:300]
    await db.commit()
    return reverted, left_alone
