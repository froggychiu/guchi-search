from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.ratelimit import SlidingWindow, client_key
from app.core.security import (
    check_review_credential,
    check_secret,
    issue_session_token,
    require_review,
)
from app.models.episode import (
    NON_ATTRIBUTABLE_SUBMITTERS,
    Correction,
    Segment,
    Episode,
    VocabRule,
)
from app.services.indexer import index_episode_segments
from app.services.vocab import derive_rule

router = APIRouter(prefix="/api/corrections", tags=["corrections"])

# Generous enough that a person working through an episode never notices, tight
# enough that a script cannot walk the corpus. Real proofreaders average a few
# corrections a minute at most.
_per_client = SlidingWindow(limit=20, window_seconds=60)

# Ceiling on corrections filed site-wide in an hour. The busiest genuine hour
# on record is far below this; crossing it means something automated is running.
GLOBAL_HOURLY_LIMIT = 500


# Brute-forcing the secret should be slow. Separate from the correction
# limiter so a busy proofreading session cannot lock out a login.
_login_attempts = SlidingWindow(limit=10, window_seconds=300)


@router.get("/verify-secret")
async def verify_secret(x_ingest_secret: str = Header(None)):
    """Check a credential without minting anything. Accepts either kind."""
    if not check_review_credential(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return {"status": "ok"}


@router.post("/session")
async def create_session(request: Request, x_ingest_secret: str = Header(None)):
    """Exchange the raw secret for a short-lived, review-scoped token.

    The browser holds the token from here on, never the secret. See
    core/security.py for what the token deliberately cannot do.
    """
    retry_after = _login_attempts.check(client_key(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="嘗試次數過多，請稍後再試",
            headers={"Retry-After": str(int(retry_after))},
        )

    # The raw secret only: a token cannot mint a fresh token, so a stolen one
    # expires when it says it will rather than renewing itself indefinitely.
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    token, expires_at = issue_session_token()
    return {"token": token, "expires_at": expires_at}


class BatchApproveRequest(BaseModel):
    ids: list[int]


class CorrectionSubmit(BaseModel):
    segment_id: int
    # A transcript line is a spoken sentence; 2000 characters is far more than
    # any of them and still bounds what one request can store. Unbounded, this
    # was a free write of arbitrary size into the database (SEC-09).
    suggested_text: str = Field(min_length=1, max_length=2000)
    # The column is String(100). Without a matching limit here, a longer name
    # reached Postgres and came back as a 500.
    submitter_name: str = Field(default="匿名", max_length=50)
    # When set, the edit is also proposed as a glossary rule so the same
    # mistake gets fixed in every other episode. Proposed only — an admin
    # reviews it before it is ever applied (see services/vocab.py).
    add_to_vocab: bool = False


@router.post("")
async def submit_correction(
    request: Request,
    body: CorrectionSubmit,
    db: AsyncSession = Depends(get_db),
):
    """Submit a correction suggestion for a segment.

    Deliberately unauthenticated — anyone reading a transcript can fix a line.
    Rate limited instead, at two levels; see core/ratelimit.py for why one is
    not enough (SEC-09).
    """
    retry_after = _per_client.check(client_key(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="送出太頻繁，請稍後再試",
            headers={"Retry-After": str(int(retry_after))},
        )

    # Bounds how fast the queue can grow regardless of who is filing. Unlike
    # the per-client window this cannot be sidestepped with a spoofed header.
    recent = (await db.execute(
        select(func.count(Correction.id)).where(
            Correction.created_at >= datetime.utcnow() - timedelta(hours=1)
        )
    )).scalar() or 0
    if recent >= GLOBAL_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="目前校對送出量過大，請稍後再試",
            headers={"Retry-After": "600"},
        )

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
            Correction.submitter_name.not_in(NON_ATTRIBUTABLE_SUBMITTERS),
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


@router.get("", dependencies=[Depends(require_review)])
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
    if not check_review_credential(x_ingest_secret):
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
    if not check_review_credential(x_ingest_secret):
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
    if not check_review_credential(x_ingest_secret):
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
