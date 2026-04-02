from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.episode import Correction, Segment, Episode
from app.services.indexer import index_episode_segments

router = APIRouter(prefix="/api/corrections", tags=["corrections"])


class CorrectionSubmit(BaseModel):
    segment_id: int
    suggested_text: str
    submitter_name: str = "匿名"


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
    await db.commit()

    return {"status": "submitted", "id": correction.id}


@router.get("")
async def list_corrections(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List correction suggestions (for admin review)."""
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


@router.post("/{correction_id}/approve")
async def approve_correction(
    correction_id: int,
    x_ingest_secret: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Approve a correction and update the segment text."""
    if not settings.ingest_secret or x_ingest_secret != settings.ingest_secret:
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
    if not settings.ingest_secret or x_ingest_secret != settings.ingest_secret:
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
