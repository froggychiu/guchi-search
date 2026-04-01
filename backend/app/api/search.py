import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.search import get_search_index
from app.models.episode import Episode, Segment

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    show: str | None = Query(None, description="Filter by show name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across all transcripts."""
    index = get_search_index()

    filters = []
    if show:
        filters.append(f'show = "{show}"')

    search_params = {
        "limit": per_page,
        "offset": (page - 1) * per_page,
        "filter": " AND ".join(filters) if filters else None,
        "attributesToHighlight": ["text"],
        "highlightPreTag": "<mark>",
        "highlightPostTag": "</mark>",
        "attributesToCrop": ["text"],
        "cropLength": 80,
        "showMatchesPosition": True,
    }

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, partial(index.search, q, search_params))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Search service unavailable: {str(e)}")

    # Enrich results with episode info
    episode_ids = list({hit["episode_id"] for hit in results["hits"]})
    episodes_map = {}
    if episode_ids:
        result = await db.execute(
            select(Episode).where(Episode.id.in_(episode_ids))
        )
        episodes_map = {ep.id: ep for ep in result.scalars().all()}

    hits = []
    for hit in results["hits"]:
        ep = episodes_map.get(hit["episode_id"])
        hits.append({
            "segment_id": hit["id"],
            "episode_id": hit["episode_id"],
            "episode_title": ep.title if ep else "",
            "show": hit.get("show", ""),
            "published_at": ep.published_at.isoformat() if ep and ep.published_at else None,
            "speaker": hit.get("speaker", ""),
            "start_time": hit["start_time"],
            "end_time": hit["end_time"],
            "text": hit["text"],
            "highlighted_text": hit.get("_formatted", {}).get("text", hit["text"]),
        })

    return {
        "query": q,
        "total_hits": results["estimatedTotalHits"],
        "page": page,
        "per_page": per_page,
        "hits": hits,
    }


@router.get("/episodes")
async def list_episodes(
    show: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all episodes with pagination."""
    query = select(Episode).order_by(Episode.published_at.desc())
    count_query = select(func.count(Episode.id))

    if show:
        query = query.where(Episode.show == show)
        count_query = count_query.where(Episode.show == show)

    # Total count
    total = (await db.execute(count_query)).scalar() or 0

    # Paginated results
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    episodes = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "episodes": [
            {
                "id": ep.id,
                "title": ep.title,
                "show": ep.show,
                "description": ep.description,
                "published_at": ep.published_at.isoformat() if ep.published_at else None,
                "duration_seconds": ep.duration_seconds,
                "transcription_status": ep.transcription_status,
            }
            for ep in episodes
        ],
    }


@router.get("/episodes/{episode_id}")
async def get_episode(episode_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single episode with its full transcript."""
    episode = await db.get(Episode, episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    result = await db.execute(
        select(Segment)
        .where(Segment.episode_id == episode_id)
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()

    return {
        "id": episode.id,
        "title": episode.title,
        "show": episode.show,
        "description": episode.description,
        "published_at": episode.published_at.isoformat() if episode.published_at else None,
        "duration_seconds": episode.duration_seconds,
        "segments": [
            {
                "id": seg.id,
                "speaker": seg.speaker,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "text": seg.text,
            }
            for seg in segments
        ],
    }


@router.get("/shows")
async def list_shows(db: AsyncSession = Depends(get_db)):
    """List all shows with episode counts."""
    result = await db.execute(
        select(Episode.show, func.count(Episode.id))
        .group_by(Episode.show)
    )
    shows = [{"name": row[0], "episode_count": row[1]} for row in result.all()]
    return {"shows": shows}


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    """Get system stats."""
    total_episodes = (await db.execute(select(func.count(Episode.id)))).scalar() or 0
    total_segments = (await db.execute(select(func.count(Segment.id)))).scalar() or 0
    done_episodes = (
        await db.execute(
            select(func.count(Episode.id)).where(Episode.transcription_status == "done")
        )
    ).scalar() or 0

    return {
        "total_episodes": total_episodes,
        "transcribed_episodes": done_episodes,
        "total_segments": total_segments,
    }
