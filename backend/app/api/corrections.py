from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_secret, require_secret
from app.models.episode import Correction, Segment, Episode, VocabRule
from app.services.indexer import index_episode_segments
from app.services.vocab import derive_rule

router = APIRouter(prefix="/api/corrections", tags=["corrections"])


@router.get("/verify-secret")
async def verify_secret(x_ingest_secret: str = Header(None)):
    """Verify the admin secret (sent via X-Ingest-Secret header).

    SEC-05 / SEC-06: header-only (no query param), constant-time compare.
    """
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return {"status": "ok"}


class BatchApproveRequest(BaseModel):
    ids: list[int]


class CorrectionSubmit(BaseModel):
    segment_id: int
    suggested_text: str
    submitter_name: str = "匿名"
    # When set, the edit is also proposed as a glossary rule so the same
    # mistake gets fixed in every other episode. Proposed only — an admin
    # reviews it before it is ever applied (see services/vocab.py).
    add_to_vocab: bool = False


@router.post("")
async def submit_correction(
    body: CorrectionSubmit,
    db: AsyncSession = Depends(get_db),
):
    """Submit a correction suggestion for a segment."""
    segment = await db.get(Segment, body.segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    # Check for duplicate pending correction on same segment by same submitter
    existing = await db.execute(
        select(Correction).where(
            Correction.segment_id == body.segment_id,
            Correction.status == "pending",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="已有待審核的修正建議")

    correction = Correction(
        segment_id=body.segment_id,
        original_text=segment.text,
        suggested_text=body.suggested_text,
        submitter_name=body.submitter_name or "匿名",
    )
    db.add(correction)

    # A glossary proposal, if the edit is a single self-contained substitution
    # and no equivalent rule is already on file.
    vocab_rule = None
    if body.add_to_vocab:
        derived = derive_rule(segment.text, body.suggested_text)
        if derived:
            wrong, right = derived
            existing = await db.execute(
                select(VocabRule).where(
                    VocabRule.wrong_text == wrong,
                    VocabRule.right_text == right,
                )
            )
            if existing.scalar_one_or_none() is None:
                vocab_rule = VocabRule(
                    wrong_text=wrong,
                    right_text=right,
                    submitter_name=body.submitter_name or "匿名",
                )
                db.add(vocab_rule)

    await db.commit()

    return {
        "status": "submitted",
        "id": correction.id,
        # Tells the UI whether the glossary checkbox actually produced
        # anything — the edit may not have been glossary-shaped.
        "vocab_rule": (
            {"wrong": vocab_rule.wrong_text, "right": vocab_rule.right_text}
            if vocab_rule else None
        ),
    }


@router.get("/contributors")
async def list_contributors(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Leaderboard of named contributors who submitted approved corrections.

    Excludes:
      - "匿名" / empty submitter_name — lumps multiple distinct people into
        one bucket, so counting them on a leaderboard would be misleading.
      - "系統自動偵測" — bot-generated entries from the hallucination scanner
        in ingest.py; not a human contributor.

    Sorted by adopted-correction count desc, then earliest submission asc
    (whoever reached that count first wins the tie).
    """
    stmt = (
        select(
            Correction.submitter_name.label("name"),
            func.count(Correction.id).label("count"),
            func.min(Correction.created_at).label("first_at"),
        )
        .where(
            Correction.status == "approved",
            Correction.submitter_name != "匿名",
            Correction.submitter_name != "",
            Correction.submitter_name != "系統自動偵測",
        )
        .group_by(Correction.submitter_name)
        .order_by(func.count(Correction.id).desc(), func.min(Correction.created_at).asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return {
        "contributors": [
            {
                "name": row.name,
                "count": row.count,
                "first_at": row.first_at.isoformat() if row.first_at else None,
            }
            for row in rows
        ],
    }


@router.get("", dependencies=[Depends(require_secret)])
async def list_corrections(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List correction suggestions (for admin review). Admin-only."""
    count_query = select(func.count(Correction.id)).where(Correction.status == status)
    total = (await db.execute(count_query)).scalar() or 0

    query = (
        select(Correction)
        .where(Correction.status == status)
        .order_by(Correction.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(query)
    corrections = result.scalars().all()

    items = []
    for c in corrections:
        segment = await db.get(Segment, c.segment_id)
        episode = await db.get(Episode, segment.episode_id) if segment else None
        items.append({
            "id": c.id,
            "segment_id": c.segment_id,
            "episode_id": segment.episode_id if segment else None,
            "episode_title": episode.title if episode else "",
            "start_time": segment.start_time if segment else 0,
            "original_text": c.original_text,
            "suggested_text": c.suggested_text,
            "submitter_name": c.submitter_name,
            "status": c.status,
            "created_at": c.created_at.isoformat(),
        })

    return {"total": total, "page": page, "per_page": per_page, "corrections": items}


@router.post("/batch-approve")
async def batch_approve(
    body: BatchApproveRequest,
    x_ingest_secret: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Batch approve multiple corrections at once."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    approved = 0
    episodes_to_reindex = set()
    for cid in body.ids:
        correction = await db.get(Correction, cid)
        if not correction or correction.status != "pending":
            continue

        segment = await db.get(Segment, correction.segment_id)
        if segment:
            segment.text = correction.suggested_text
            episodes_to_reindex.add(segment.episode_id)

        correction.status = "approved"
        correction.reviewed_at = datetime.utcnow()
        approved += 1

    await db.commit()

    # Re-index affected episodes
    for episode_id in episodes_to_reindex:
        try:
            await index_episode_segments(db, episode_id)
        except Exception:
            pass

    return {"status": "ok", "approved": approved}


@router.post("/{correction_id}/approve")
async def approve_correction(
    correction_id: int,
    x_ingest_secret: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Approve a correction and update the segment text."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    correction = await db.get(Correction, correction_id)
    if not correction:
        raise HTTPException(status_code=404, detail="Correction not found")
    if correction.status != "pending":
        raise HTTPException(status_code=400, detail="Correction already reviewed")

    # Update segment text
    segment = await db.get(Segment, correction.segment_id)
    if segment:
        segment.text = correction.suggested_text

    correction.status = "approved"
    correction.reviewed_at = datetime.utcnow()
    await db.commit()

    # Re-index the episode
    if segment:
        try:
            await index_episode_segments(db, segment.episode_id)
        except Exception:
            pass

    return {"status": "approved"}


@router.post("/{correction_id}/reject")
async def reject_correction(
    correction_id: int,
    x_ingest_secret: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Reject a correction suggestion."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    correction = await db.get(Correction, correction_id)
    if not correction:
        raise HTTPException(status_code=404, detail="Correction not found")
    if correction.status != "pending":
        raise HTTPException(status_code=400, detail="Correction already reviewed")

    correction.status = "rejected"
    correction.reviewed_at = datetime.utcnow()
    await db.commit()

    return {"status": "rejected"}
