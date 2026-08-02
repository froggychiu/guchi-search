"""The vocabulary glossary: recurring transcription fixes, applied at ingest.

See VocabRule in models/episode.py for why this exists rather than a longer
Whisper prompt, and why rules are reviewed before they are applied.
"""

import difflib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.episode import Correction, VocabRule

# A proposed rule wider than this is a rewrite of the sentence, not a glossary
# entry, and would be both unsafe and useless to apply everywhere.
MAX_RULE_LEN = 20

# How many independent approved corrections must make the same substitution
# before it is proposed as a rule. Two could be one person fixing the same
# episode twice; three across the corpus is a pattern.
MIN_EVIDENCE = 3

# The hallucination scanner files corrections under this name. Its edits are
# "（疑似幻覺，建議刪除）", which must never become a glossary rule.
BOT_SUBMITTER = "系統自動偵測"


async def load_active_rules(db: AsyncSession) -> list[tuple[str, str]]:
    """Active rules, longest `wrong_text` first.

    Order matters: with both 瓜子 -> 呱吉 and 瓜 -> 呱 active, applying the
    short rule first turns 瓜子 into 呱子, which the long rule then no longer
    matches. Longest-first makes the outcome independent of insertion order.
    """
    rows = await db.execute(
        select(VocabRule.wrong_text, VocabRule.right_text)
        .where(VocabRule.status == "active")
    )
    rules = [(w, r) for w, r in rows if w]
    rules.sort(key=lambda pair: len(pair[0]), reverse=True)
    return rules


def apply_rules(text: str, rules: list[tuple[str, str]]) -> str:
    """Apply glossary rules to one segment.

    Plain substring replacement — the rules are literal spellings, and the
    review step is what decides whether a given replacement is safe.
    """
    for wrong, right in rules:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


async def mine_rules_from_corrections(
    db: AsyncSession,
    min_evidence: int = MIN_EVIDENCE,
) -> list[tuple[str, str, int, int]]:
    """Propose glossary rules from repeated approved corrections.

    A proofreader ticking the box on the correction form is one signal. This
    is the stronger one: when the same substitution has been made and approved
    independently `min_evidence` times, it is a recurring mistranscription
    rather than one person's preference, whether or not anybody remembered to
    tick anything.

    Runs on every ingest, so the glossary keeps growing from work the
    proofreaders were already doing. Only ever creates *pending* rules —
    nothing reaches transcripts without review.

    A pair already on file is skipped whatever its status, so a rejected rule
    stays rejected instead of being re-proposed every night. Existing pending
    rules do get their evidence count refreshed.

    Returns (wrong, right, evidence, distinct_submitters) for each new
    proposal.
    """
    rows = (await db.execute(
        select(Correction.original_text, Correction.suggested_text,
               Correction.submitter_name)
        .where(Correction.status == "approved",
               Correction.submitter_name != BOT_SUBMITTER)
    )).all()

    evidence: dict[tuple[str, str], int] = {}
    submitters: dict[tuple[str, str], set[str]] = {}
    for original, suggested, submitter in rows:
        pair = derive_rule(original or "", suggested or "")
        if not pair:
            continue
        evidence[pair] = evidence.get(pair, 0) + 1
        submitters.setdefault(pair, set()).add(submitter or "匿名")

    existing = {
        (w, r): rule
        for w, r, rule in (
            (rule.wrong_text, rule.right_text, rule)
            for rule in (await db.execute(select(VocabRule))).scalars().all()
        )
    }

    proposed = []
    for pair, count in sorted(evidence.items(), key=lambda kv: -kv[1]):
        if count < min_evidence:
            continue
        wrong, right = pair
        known = existing.get(pair)
        if known is not None:
            # Keep the count fresh so the review queue reflects new evidence.
            if known.status == "pending" and known.evidence_count != count:
                known.evidence_count = count
            continue
        db.add(VocabRule(
            wrong_text=wrong,
            right_text=right,
            source="mined",
            submitter_name="校對紀錄探勘",
            evidence_count=count,
            note=f"{count} 筆已批准校對做過相同修改",
        ))
        proposed.append((wrong, right, count, len(submitters[pair])))

    await db.commit()
    return proposed


def derive_rule(original: str, suggested: str) -> tuple[str, str] | None:
    """Extract a reusable (wrong, right) pair from a single correction.

    Returns None when the edit is not glossary-shaped: multiple separate
    changes, a pure insertion or deletion, or a span long enough that it is
    really a rewrite of that one sentence. Those are still valid corrections,
    they just do not generalize to other episodes.
    """
    if not original or not suggested or original == suggested:
        return None

    edits = [
        op for op in difflib.SequenceMatcher(
            None, original, suggested, autojunk=False
        ).get_opcodes()
        if op[0] != "equal"
    ]
    if len(edits) != 1:
        return None

    _, i1, i2, j1, j2 = edits[0]
    wrong, right = original[i1:i2].strip(), suggested[j1:j2].strip()
    if not wrong or not right:
        return None
    if len(wrong) > MAX_RULE_LEN or len(right) > MAX_RULE_LEN:
        return None
    return wrong, right
