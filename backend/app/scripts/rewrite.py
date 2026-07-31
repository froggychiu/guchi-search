"""Batched rewrite of the segments table.

Both the variant repair and the glossary backfill touch every one of ~2.6M
segments. Loading them as ORM objects in one query needs on the order of a
gigabyte on a box already sharing RAM with Meilisearch, so this walks the
table in id-ordered batches, selects only the two columns involved, and issues
one executemany UPDATE per batch.

Progress prints as it goes. The maintenance API runs this as a detached
subprocess and only logs its output on exit, so a run that prints nothing at
all is indistinguishable from a hang.
"""

from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.episode import Segment

BATCH_SIZE = 5000


def diff_substitutions(before: str, after: str) -> list[tuple[str, str]]:
    """Character substitutions between two versions of a segment.

    Both rewrites are overwhelmingly 1:1 character mappings, so the common
    case is a cheap zip. difflib is reserved for the rare length-changing
    edit — running it on every one of ~190k changed segments is what made the
    first version of this too slow to finish.
    """
    if len(before) == len(after):
        return [(a, b) for a, b in zip(before, after) if a != b]

    import difflib

    out = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            out.append((before[i1:i2], after[j1:j2]))
    return out


# ORM bulk update by primary key: `update(Segment)` with no WHERE, executed
# against a list of dicts that each carry the pk. SQLAlchemy turns the batch
# into one executemany rather than 5000 statements, and nothing is loaded into
# the identity map, so memory stays flat across batches.
_UPDATE_TEXT = update(Segment)


async def rewrite_segments(
    session: AsyncSession,
    transform: Callable[[str], str],
    *,
    dry_run: bool,
    label: str,
) -> tuple[int, int, dict[tuple[str, str], int]]:
    """Apply `transform` to every segment's text, in batches.

    Returns (scanned, changed, substitution counts). Writes nothing when
    dry_run is set.
    """
    scanned = changed = 0
    subs: dict[tuple[str, str], int] = {}
    last_id = 0

    while True:
        rows = (await session.execute(
            select(Segment.id, Segment.text)
            .where(Segment.id > last_id)
            .order_by(Segment.id)
            .limit(BATCH_SIZE)
        )).all()
        if not rows:
            break

        updates = []
        for seg_id, text in rows:
            last_id = seg_id
            scanned += 1
            original = text or ""
            new_text = transform(original)
            if new_text == original:
                continue
            changed += 1
            for pair in diff_substitutions(original, new_text):
                subs[pair] = subs.get(pair, 0) + 1
            updates.append({"id": seg_id, "text": new_text})

        if updates and not dry_run:
            # executemany, so a batch is one round trip rather than 5000.
            await session.execute(_UPDATE_TEXT, updates)
            await session.commit()

        print(f"  [{label}] scanned {scanned}, changed {changed}", flush=True)

    return scanned, changed, subs


def print_report(
    label: str,
    scanned: int,
    changed: int,
    subs: dict[tuple[str, str], int],
    dry_run: bool,
    limit: int = 40,
) -> None:
    pct = (changed / scanned * 100) if scanned else 0
    print(f"[{'DRY-RUN' if dry_run else 'OK'}] {label}: "
          f"{changed} / {scanned} segments affected ({pct:.1f}%)")
    for (old, new), n in sorted(subs.items(), key=lambda kv: -kv[1])[:limit]:
        print(f"    {n:>7}x  {old} -> {new}")
    if len(subs) > limit:
        print(f"    ... and {len(subs) - limit} more distinct substitutions")
    if dry_run:
        print("[DRY-RUN] nothing written. Re-run without dry_run to apply.")
