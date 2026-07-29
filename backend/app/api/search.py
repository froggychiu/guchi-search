import asyncio
import html
from datetime import datetime, timedelta
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session, get_db
from app.core.search import get_search_index
from app.models.episode import Episode, SearchLog, Segment

router = APIRouter(prefix="/api", tags=["search"])


# How many raw hits to pull from Meilisearch before dedup.
# Large enough to cover deep pagination after grouping hits by episode —
# popular keywords cluster into few episodes, so we need a wider net than
# the per-page episode count would suggest.
MAX_RAW_HITS = 1000

# Minimum query length to count toward popular-keyword analytics.
# Single-character queries add noise and dominate rankings.
MIN_LOGGABLE_QUERY_LEN = 2

# ---------------------------------------------------------------------------
# Stored-XSS defense for highlighted snippets (SEC-01).
#
# Meilisearch returns matched text with <mark>...</mark> wrapped around the
# query — we configure the pre/post tags below. We must HTML-escape any user-
# generated content (segment text, episode_title via RSS, future neighbor
# context) before sending to the frontend, but PRESERVE our own <mark> tags
# so React can render them. Use null-byte placeholders that html.escape()
# never touches as a swap mechanism.
# ---------------------------------------------------------------------------
HIGHLIGHT_PRE_TAG = "<mark>"
HIGHLIGHT_POST_TAG = "</mark>"
_PRE_PLACEHOLDER = "\x00MARK_OPEN\x00"
_POST_PLACEHOLDER = "\x00MARK_CLOSE\x00"


def _safe_highlight(formatted_text: str) -> str:
    """HTML-escape text from Meilisearch while keeping our own <mark> tags.

    A future maintainer who changes the pre/post tags below MUST also update
    HIGHLIGHT_PRE_TAG / HIGHLIGHT_POST_TAG.
    """
    s = (formatted_text or "")
    s = s.replace(HIGHLIGHT_PRE_TAG, _PRE_PLACEHOLDER).replace(
        HIGHLIGHT_POST_TAG, _POST_PLACEHOLDER
    )
    s = html.escape(s, quote=False)
    s = s.replace(_PRE_PLACEHOLDER, HIGHLIGHT_PRE_TAG).replace(
        _POST_PLACEHOLDER, HIGHLIGHT_POST_TAG
    )
    return s


# Whitelist for the `show` query parameter — fixes SEC-04 (Meilisearch filter
# expression injection via unescaped f-string). Anything outside this set is
# rejected with 400 instead of being concatenated into the filter.
ALLOWED_SHOWS = set(settings.show_keywords.keys()) | {settings.default_show}


async def _log_search_query(query: str) -> None:
    """Fire-and-forget insert into search_logs. Swallows DB errors
    so a logging issue can never break the user's search request."""
    normalized = (query or "").strip()
    if len(normalized) < MIN_LOGGABLE_QUERY_LEN:
        return
    try:
        async with async_session() as session:
            session.add(SearchLog(query=normalized))
            await session.commit()
    except Exception:
        pass  # analytics best-effort


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    show: str | None = Query(None, description="Filter by show name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across all transcripts.

    Title-only matches (where the query appears in the episode title but NOT in
    the segment text) are deduplicated to one hit per episode — otherwise every
    segment in that episode would flood the results.
    """
    if show is not None and show not in ALLOWED_SHOWS:
        raise HTTPException(status_code=400, detail="Invalid show name")

    index = get_search_index()

    filters = []
    if show:
        # Safe: `show` was validated against ALLOWED_SHOWS above (SEC-04).
        filters.append(f'show = "{show}"')

    search_params = {
        "limit": MAX_RAW_HITS,
        "offset": 0,
        "filter": " AND ".join(filters) if filters else None,
        "attributesToHighlight": ["text"],
        "highlightPreTag": HIGHLIGHT_PRE_TAG,
        "highlightPostTag": HIGHLIGHT_POST_TAG,
        "attributesToCrop": ["text"],
        "cropLength": 80,
        "showMatchesPosition": True,
    }

    # Try search up to 3 times with short backoff to handle Meilisearch cold-cache hiccups
    last_err: Exception | None = None
    results = None
    for attempt in range(3):
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, partial(index.search, q, search_params)
            )
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1s
    if results is None:
        raise HTTPException(
            status_code=503,
            detail=f"Search service unavailable: {str(last_err)}",
        )

    # ------------------------------------------------------------------
    # Classify each hit into one of three buckets:
    #   1. content match — query substring appears literally in hit.text
    #   2. title-only match — query substring is in episode_title but NOT in text
    #      (dedup to 1 per episode to prevent flooding)
    #   3. near match — neither contains the literal substring; Meilisearch
    #      matched via CJK character-level tokenization. Treat like content
    #      match (text is still semantically relevant) — keep all.
    # ------------------------------------------------------------------
    q_lower = q.lower().strip()
    seen_title_only_episodes: set[int] = set()
    deduped_raw = []
    for hit in results["hits"]:
        text_lower = (hit.get("text") or "").lower()
        title_lower = (hit.get("episode_title") or "").lower()
        in_text = q_lower in text_lower
        in_title = q_lower in title_lower

        if in_text:
            # Real content match
            hit["_is_title_only"] = False
            deduped_raw.append(hit)
        elif in_title:
            # True title-only match — dedupe per episode
            ep_id = hit["episode_id"]
            if ep_id in seen_title_only_episodes:
                continue
            seen_title_only_episodes.add(ep_id)
            hit["_is_title_only"] = True
            deduped_raw.append(hit)
        else:
            # Near match (CJK partial). Keep as content-style result —
            # the segment text still has related content even though
            # the literal substring isn't there.
            hit["_is_title_only"] = False
            deduped_raw.append(hit)

    total_segment_matches = len(deduped_raw)

    # ------------------------------------------------------------------
    # Group hits by episode_id, preserving Meilisearch's relevance order
    # (the first time an episode appears in deduped_raw determines its
    # rank in the grouped result).
    # ------------------------------------------------------------------
    episode_order: list[int] = []
    grouped: dict[int, list[dict]] = {}
    for hit in deduped_raw:
        ep_id = hit["episode_id"]
        if ep_id not in grouped:
            grouped[ep_id] = []
            episode_order.append(ep_id)
        grouped[ep_id].append(hit)

    total_episodes = len(episode_order)

    # Paginate by episode
    start = (page - 1) * per_page
    end = start + per_page
    page_episode_ids = episode_order[start:end]

    # Enrich with Episode rows
    episodes_map = {}
    if page_episode_ids:
        result = await db.execute(
            select(Episode).where(Episode.id.in_(page_episode_ids))
        )
        episodes_map = {ep.id: ep for ep in result.scalars().all()}

    # ------------------------------------------------------------------
    # Fetch neighbor segments for short content matches (context expansion).
    # A hit whose text is shorter than SHORT_SEGMENT_CHARS gets the
    # previous and next segment concatenated around the highlighted text.
    # Only compute for hits that will actually be returned.
    # ------------------------------------------------------------------
    SHORT_SEGMENT_CHARS = 15
    NEIGHBOR_MAX_CHARS = 40  # trim very long neighbor text

    page_hits_flat: list[dict] = []
    for ep_id in page_episode_ids:
        page_hits_flat.extend(grouped[ep_id])

    neighbors_map: dict[int, tuple[str | None, str | None]] = {}
    short_hits = [
        h
        for h in page_hits_flat
        if not h.get("_is_title_only", False)
        and len((h.get("text") or "").strip()) < SHORT_SEGMENT_CHARS
    ]
    for h in short_hits:
        ep_id = h["episode_id"]
        st = h["start_time"]
        prev_q = await db.execute(
            select(Segment.text)
            .where(Segment.episode_id == ep_id, Segment.start_time < st)
            .order_by(Segment.start_time.desc())
            .limit(1)
        )
        prev_text = prev_q.scalar_one_or_none()
        next_q = await db.execute(
            select(Segment.text)
            .where(Segment.episode_id == ep_id, Segment.start_time > st)
            .order_by(Segment.start_time.asc())
            .limit(1)
        )
        next_text = next_q.scalar_one_or_none()
        neighbors_map[h["id"]] = (prev_text, next_text)

    def _trim_tail(s: str, limit: int) -> str:
        s = (s or "").strip()
        return ("…" + s[-limit:]) if len(s) > limit else s

    def _trim_head(s: str, limit: int) -> str:
        s = (s or "").strip()
        return (s[:limit] + "…") if len(s) > limit else s

    def _format_hit(hit: dict) -> dict:
        # SEC-01: escape user-generated content before returning. _safe_highlight
        # preserves our own <mark> tags; neighbor context is escaped as plain text.
        highlighted = _safe_highlight(hit.get("_formatted", {}).get("text", hit["text"]))
        if hit["id"] in neighbors_map:
            prev_t, next_t = neighbors_map[hit["id"]]
            parts: list[str] = []
            if prev_t:
                parts.append(html.escape(_trim_tail(prev_t, NEIGHBOR_MAX_CHARS), quote=False))
            parts.append(highlighted)
            if next_t:
                parts.append(html.escape(_trim_head(next_t, NEIGHBOR_MAX_CHARS), quote=False))
            highlighted = " ".join(parts)
        return {
            "segment_id": hit["id"],
            "start_time": hit["start_time"],
            "end_time": hit["end_time"],
            "text": hit["text"],
            "highlighted_text": highlighted,
            "is_title_only": hit.get("_is_title_only", False),
        }

    episodes_payload: list[dict] = []
    for ep_id in page_episode_ids:
        ep_hits_raw = grouped[ep_id]
        # Sort within an episode by start_time (chronological inside the audio)
        ep_hits_raw_sorted = sorted(ep_hits_raw, key=lambda h: h["start_time"])
        ep_hits = [_format_hit(h) for h in ep_hits_raw_sorted]
        ep = episodes_map.get(ep_id)
        # An episode is "title-only" overall iff every hit in it is title-only
        # (i.e. there were no real content matches, only the dedup'd title row).
        is_title_only_match = all(h["is_title_only"] for h in ep_hits) if ep_hits else False
        episodes_payload.append({
            "episode_id": ep_id,
            "episode_title": ep.title if ep else "",
            "show": ep.show if ep else (ep_hits_raw[0].get("show", "") if ep_hits_raw else ""),
            "published_at": ep.published_at.isoformat() if ep and ep.published_at else None,
            "is_title_only_match": is_title_only_match,
            "hit_count": len(ep_hits),
            "hits": ep_hits,
        })

    # Best-effort analytics logging (only on page 1 so refreshes / paginations
    # don't inflate counts for the same user action).
    if page == 1:
        asyncio.create_task(_log_search_query(q))

    return {
        "query": q,
        "total_episodes": total_episodes,
        "total_segment_matches": total_segment_matches,
        "page": page,
        "per_page": per_page,
        "episodes": episodes_payload,
    }


@router.get("/text-count")
async def text_count(
    q: str = Query(..., min_length=1, description="Exact substring to count"),
    db: AsyncSession = Depends(get_db),
):
    """Count segments where text contains the EXACT substring `q`.
    Bypasses Meilisearch's CJK partial-match expansion — gives true counts
    for picking which variants to bulk-replace."""
    result = await db.execute(
        select(func.count(Segment.id)).where(Segment.text.contains(q))
    )
    count = result.scalar() or 0
    return {"query": q, "count": count}


@router.get("/popular-keywords")
async def popular_keywords(
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    limit: int = Query(10, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    """Top N most-searched queries within the recent window."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(SearchLog.query, func.count(SearchLog.id).label("count"))
        .where(SearchLog.created_at >= since)
        .group_by(SearchLog.query)
        .order_by(func.count(SearchLog.id).desc())
        .limit(limit)
    )
    rows = result.all()
    return {
        "window_days": days,
        "keywords": [{"keyword": row[0], "count": row[1]} for row in rows],
    }


@router.get("/episodes")
async def list_episodes(
    show: str | None = Query(None),
    sort: str = Query("newest", description="Sort order: 'newest' or 'oldest'"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all episodes with pagination."""
    if sort == "oldest":
        query = select(Episode).order_by(Episode.published_at.asc())
    else:
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
        "audio_url": episode.audio_url,
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
