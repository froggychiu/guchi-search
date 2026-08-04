"""Admin API for the transcription glossary.

Rules are proposed by proofreaders through the correction form, or mined from
repeated approved corrections, and reviewed here. Each rule carries its corpus
occurrence count because that number is the whole decision: 彩玲 appears in 41
segments and is always a mistake, while 瓜子 appears in 519 and usually means
melon seeds.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_review
from app.models.episode import VocabRule

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


@router.get("", dependencies=[Depends(require_review)])
async def list_rules(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List glossary rules for review, most-evidenced first.

    Corpus counts are read from the rule row, not measured here. Measuring
    them per request meant a pattern match across 2.6M segments for every
    spelling on the page — 11 seconds against a 15-second frontend timeout,
    even after folding them into a single scan, because the cost is the
    matching rather than the I/O. They are refreshed by the nightly mining
    run; `counts_updated_at` says when.
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
            # Both numbers matter. A high wrong_hits count next to an
            # already-common right_hits usually means wrong_text is a real
            # word rather than a mishearing.
            "wrong_hits": rule.wrong_hits,
            "right_hits": rule.right_hits,
            "counts_updated_at": (
                rule.counts_updated_at.isoformat() if rule.counts_updated_at else None
            ),
        }
        items.append(item)

    return {
        "status": status,
        "total": total,
        "page": page,
        "per_page": per_page,
        "rules": items,
    }


@router.post("", dependencies=[Depends(require_review)])
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


@router.post("/{rule_id}/review", dependencies=[Depends(require_review)])
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
