"""The vocabulary glossary: recurring transcription fixes, applied at ingest.

See VocabRule in models/episode.py for why this exists rather than a longer
Whisper prompt, and why rules are reviewed before they are applied.
"""

import difflib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.episode import VocabRule

# A proposed rule wider than this is a rewrite of the sentence, not a glossary
# entry, and would be both unsafe and useless to apply everywhere.
MAX_RULE_LEN = 20


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
