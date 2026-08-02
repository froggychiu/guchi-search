"""Admin API for the transcription glossary.

Rules are proposed by proofreaders through the correction form and reviewed
here. The review response carries each rule's corpus occurrence count, because
that number is the whole decision: 彩玲 appears in 38 segments and is always a
mistake, while 瓜子 appears in 519 and usually means melon seeds.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_secret
from app.models.episode import Segment, VocabRule

router = APIRouter(prefix="/api/vocab", tags=["vocab"])

VALID_STATUSES = ("pending", "active", "rejected")


class VocabRuleCreate(BaseModel):
    wrong_text: str = Field(min_length=1, max_length=100)
    right_text: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=300)
    submitter_name: str = Field(default="匿名", max_length=100)


class VocabRuleReview(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=300)


def _ilike_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _corpus_hits(db: AsyncSession, text: str) -> int:
    """How many segments currently contain this spelling."""
    stmt = select(func.count(Segment.id)).where(
        Segment.text.ilike(f"%{_ilike_literal(text)}%", escape="\\")
    )
    return (await db.execute(stmt)).scalar() or 0


@router.get("", dependencies=[Depends(require_secret)])
async def list_rules(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    with_counts: bool = Query(True, description="Include corpus occurrence counts"),
    db: AsyncSession = Depends(get_db),
):
    """List glossary rules for review, most-evidenced first.

    Paginated because each corpus count is a sequential scan over 2.6M
    segments. Mining the existing corrections produced 278 pending rules at
    once; counting them all would have meant 556 scans and a request nobody
    would wait for. `per_page` is capped for the same reason.
    """
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    total = (await db.execute(
        select(func.count(VocabRule.id)).where(VocabRule.status == status)
    )).scalar() or 0

    rows = (await db.execute(
        select(VocabRule)
        .where(VocabRule.status == status)
        # Strongest evidence first: that is the order a reviewer wants to
        # work through a 278-item queue in.
        .order_by(VocabRule.evidence_count.desc(), VocabRule.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )).scalars().all()

    items = []
    for rule in rows:
        item = {
            "id": rule.id,
            "wrong_text": rule.wrong_text,
            "right_text": rule.right_text,
            "status": rule.status,
            "note": rule.note,
            "submitter_name": rule.submitter_name,
            "source": rule.source,
            "evidence_count": rule.evidence_count,
            "applied_count": rule.applied_count,
            "created_at": rule.created_at.isoformat(),
        }
        if with_counts:
            # Both numbers matter. A high wrong_hits count next to an
            # already-common right_hits usually means wrong_text is a real
            # word rather than a mishearing.
            item["wrong_hits"] = await _corpus_hits(db, rule.wrong_text)
            item["right_hits"] = await _corpus_hits(db, rule.right_text)
        items.append(item)

    return {
        "status": status,
        "total": total,
        "page": page,
        "per_page": per_page,
        "rules": items,
    }


@router.post("", dependencies=[Depends(require_secret)])
async def create_rule(body: VocabRuleCreate, db: AsyncSession = Depends(get_db)):
    """Add a rule directly. Still starts as pending, never auto-applied."""
    if body.wrong_text == body.right_text:
        raise HTTPException(status_code=400, detail="Rule is a no-op")

    existing = await db.execute(
        select(VocabRule).where(
            VocabRule.wrong_text == body.wrong_text,
            VocabRule.right_text == body.right_text,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Rule already exists")

    rule = VocabRule(
        wrong_text=body.wrong_text,
        right_text=body.right_text,
        note=body.note,
        submitter_name=body.submitter_name,
    )
    db.add(rule)
    await db.commit()
    return {"status": "created", "id": rule.id}


@router.post("/{rule_id}/review", dependencies=[Depends(require_secret)])
async def review_rule(
    rule_id: int,
    body: VocabRuleReview,
    db: AsyncSession = Depends(get_db),
):
    """Approve (`active`) or reject a proposed rule.

    Approving only affects transcription from here on. Rewriting the existing
    2.6M segments is a separate, explicit maintenance run.
    """
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    rule = await db.get(VocabRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.status = body.status
    if body.note is not None:
        rule.note = body.note
    rule.reviewed_at = datetime.utcnow()
    await db.commit()
    return {"status": rule.status, "id": rule.id}
