import asyncio
import html
from datetime import datetime, timedelta
from functools import partial

import opencc
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session, get_db
from app.core.search import get_search_index
from app.models.episode import Episode, SearchLog, Segment

router = APIRouter(prefix="/api", tags=["search"])


# ---------------------------------------------------------------------------
# Why /api/search issues three queries instead of one (SEARCH-01).
#
# `episode_title` is a searchable attribute, and every episode title contains
# 呱吉. So a plain search for 呱吉 matches all ~2.6M segment docs. The old
# implementation pulled one flat window of hits and derived every number from
# it, which meant the window size WAS the reported total: common words all
# reported exactly 1000 matches, and the episode count was whatever happened
# to fit — 呱吉 reported fewer episodes (188) than 采翎 (367) purely because
# its hits clustered more densely.
#
# The fix splits the two kinds of match apart and counts each one exactly:
#
#   1. Content matches — Meilisearch restricted to the `text` field, with a
#      facet distribution over episode_id. The facet covers EVERY match, not
#      just the returned window, so counts are exact and cheap.
#   2. Title matches — a Postgres ILIKE over the ~820 episode rows. The old
#      code classified these with a literal `q in title` test, so ILIKE
#      reproduces it exactly, and avoids a facet pass over 2.6M docs.
#   3. Snippets — one filtered query for just the current page's episodes.
# ---------------------------------------------------------------------------

# Relevance-ordering window. Only episode_id is retrieved, so this stays cheap;
# it decides episode ORDER, never the totals. Capped by the index's
# pagination.maxTotalHits (5000, set in core/search.py).
MAX_ORDER_HITS = 5000

# Snippets rendered per episode. The card expands to show these, so an episode
# with 8,000 matches must not emit 8,000 rows — the exact count is still
# reported in hit_count.
HITS_PER_EPISODE = 20

# Minimum query length to count toward popular-keyword analytics.
# Single-character queries add noise and dominate rankings.
MIN_LOGGABLE_QUERY_LEN = 2

# The corpus was normalized to Traditional Chinese with OpenCC s2t during
# ingest, so a Simplified query used to miss nearly everything (电脑 matched
# 2 segments where 電腦 matched 897). s2t is idempotent on Traditional input,
# so queries can be converted unconditionally (BUG-02).
_s2t = opencc.OpenCC("s2t")

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


def _phrase_query(q: str) -> str:
    """Rewrite a query so Meilisearch matches it literally.

    Meilisearch tokenizes CJK per character, so a bare 電腦 matches any
    segment containing 電 OR 腦. Production returned 電踏大叔, 電話, 電臺 and
    電影 as matches for 電腦 — 15,230 hits across 818 of 820 episodes, against
    899 segments that actually contain the word.

    Quoting each whitespace-separated token turns it into a phrase, which
    restores literal matching. matchingStrategy="all" (set at the call site)
    then requires every token to be present, without requiring the separate
    tokens to sit next to each other — so 呱吉 電腦 finds segments containing
    both words anywhere, while 電腦 alone no longer matches 電視.

    Returns "" when nothing usable is left, which the caller rejects.
    """
    tokens = [t.replace('"', "").strip() for t in q.split()]
    return " ".join(f'"{t}"' for t in tokens if t)


def _ilike_literal(s: str) -> str:
    """Escape LIKE wildcards so a query is matched as a literal substring.

    Without this, searching for `%` would make the title ILIKE match every
    episode, and `_` would match any single character.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _meili_search(index, q: str, params: dict) -> dict:
    """Run a Meilisearch query off the event loop, retrying cold-cache blips.

    Raises 503 after three failed attempts rather than returning partial data —
    a silently empty result set would read as "no matches found".
    """
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, partial(index.search, q, params))
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1s
    raise HTTPException(
        status_code=503,
        detail=f"Search service unavailable: {str(last_err)}",
    )


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
    """Full-text search across all transcripts, grouped by episode.

    Content matches and title-only matches are counted separately and exactly;
    see the SEARCH-01 note at the top of this module for why that needs more
    than one query.
    """
    if show is not None and show not in ALLOWED_SHOWS:
        raise HTTPException(status_code=400, detail="Invalid show name")

    # BUG-02: normalize the query to Traditional to match the corpus.
    q = _s2t.convert(q.strip())
    meili_q = _phrase_query(q)
    if not q or not meili_q:
        raise HTTPException(status_code=400, detail="Empty query")

    index = get_search_index()
    # Safe: `show` was validated against ALLOWED_SHOWS above (SEC-04).
    show_filter = f'show = "{show}"' if show else None

    # ------------------------------------------------------------------
    # Query 1 — content matches. Retrieves episode_id only: this pass exists
    # to establish relevance ORDER. The exact per-episode counts come from the
    # facet distribution, which covers every match rather than just this
    # window (requires faceting.maxValuesPerFacet >= episode count, set in
    # core/search.py).
    # ------------------------------------------------------------------
    order_result = await _meili_search(index, meili_q, {
        "limit": MAX_ORDER_HITS,
        "offset": 0,
        "filter": show_filter,
        "matchingStrategy": "all",
        "attributesToSearchOn": ["text"],
        "attributesToRetrieve": ["episode_id"],
        "facets": ["episode_id"],
    })

    facet = (order_result.get("facetDistribution") or {}).get("episode_id") or {}
    content_counts: dict[int, int] = {int(k): int(v) for k, v in facet.items()}

    content_order: list[int] = []
    ranked: set[int] = set()
    for hit in order_result["hits"]:
        ep_id = hit["episode_id"]
        if ep_id not in ranked:
            ranked.add(ep_id)
            content_order.append(ep_id)

    # ------------------------------------------------------------------
    # Query 2 — title matches, straight from Postgres over ~820 episode rows.
    # ------------------------------------------------------------------
    title_stmt = select(Episode.id, Episode.published_at).where(
        Episode.title.ilike(f"%{_ilike_literal(q)}%", escape="\\")
    )
    if show:
        title_stmt = title_stmt.where(Episode.show == show)
    title_rows = (await db.execute(title_stmt)).all()

    # An episode with real content matches is never "title-only".
    title_only = [row for row in title_rows if row[0] not in content_counts]
    title_only.sort(key=lambda row: row[1] or datetime.min, reverse=True)
    title_only_ids = {row[0] for row in title_only}

    # ------------------------------------------------------------------
    # Final episode order: relevance-ranked content episodes, then content
    # episodes whose best hit fell outside the ordering window (most matches
    # first), then title-only episodes newest first.
    # ------------------------------------------------------------------
    unranked = sorted(
        (ep_id for ep_id in content_counts if ep_id not in ranked),
        key=lambda ep_id: (-content_counts[ep_id], ep_id),
    )
    ordered_episode_ids = content_order + unranked + [row[0] for row in title_only]

    total_episodes = len(ordered_episode_ids)
    # A title-only episode contributes exactly one row, matching how it renders.
    # Summing its segments would report every segment in the episode.
    total_segment_matches = sum(content_counts.values()) + len(title_only)

    start = (page - 1) * per_page
    page_episode_ids = ordered_episode_ids[start:start + per_page]
    page_content_ids = [e for e in page_episode_ids if e not in title_only_ids]

    # ------------------------------------------------------------------
    # Query 3 — snippets, for this page's content episodes only.
    # ------------------------------------------------------------------
    grouped: dict[int, list[dict]] = {}
    if page_content_ids:
        # Episode ids are integers from our own database, not user input.
        id_list = ", ".join(str(int(e)) for e in page_content_ids)
        snippet_filter = f"episode_id IN [{id_list}]"
        if show_filter:
            snippet_filter = f"{show_filter} AND {snippet_filter}"

        snippet_result = await _meili_search(index, meili_q, {
            "limit": len(page_content_ids) * HITS_PER_EPISODE,
            "offset": 0,
            "filter": snippet_filter,
            "matchingStrategy": "all",
            "attributesToSearchOn": ["text"],
            "attributesToHighlight": ["text"],
            "highlightPreTag": HIGHLIGHT_PRE_TAG,
            "highlightPostTag": HIGHLIGHT_POST_TAG,
            "attributesToCrop": ["text"],
            "cropLength": 80,
        })
        for hit in snippet_result["hits"]:
            bucket = grouped.setdefault(hit["episode_id"], [])
            if len(bucket) < HITS_PER_EPISODE:
                bucket.append(hit)

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
        page_hits_flat.extend(grouped.get(ep_id, []))

    neighbors_map: dict[int, tuple[str | None, str | None]] = {}
    short_hits = [
        h
        for h in page_hits_flat
        if len((h.get("text") or "").strip()) < SHORT_SEGMENT_CHARS
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
            "is_title_only": False,
        }

    episodes_payload: list[dict] = []
    for ep_id in page_episode_ids:
        is_title_only_match = ep_id in title_only_ids
        ep_hits_raw = grouped.get(ep_id, [])
        # Sort within an episode by start_time (chronological inside the audio)
        ep_hits_raw_sorted = sorted(ep_hits_raw, key=lambda h: h["start_time"])
        ep_hits = [_format_hit(h) for h in ep_hits_raw_sorted]
        ep = episodes_map.get(ep_id)
        episodes_payload.append({
            "episode_id": ep_id,
            "episode_title": ep.title if ep else "",
            "show": ep.show if ep else (ep_hits_raw[0].get("show", "") if ep_hits_raw else ""),
            "published_at": ep.published_at.isoformat() if ep and ep.published_at else None,
            "is_title_only_match": is_title_only_match,
            # Exact, from the facet distribution — not len(hits), which is
            # capped at HITS_PER_EPISODE. hits_shown lets the UI say so.
            "hit_count": content_counts.get(ep_id, 0),
            "hits_shown": len(ep_hits),
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
