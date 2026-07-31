import asyncio
import html
from datetime import datetime, timedelta

import opencc
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session, get_db
from app.models.episode import Episode, SearchLog, Segment

router = APIRouter(prefix="/api", tags=["search"])


# ---------------------------------------------------------------------------
# Why /api/search matches against Postgres rather than Meilisearch.
#
# Two independent defects made the Meilisearch path unusable for this corpus,
# and both are structural rather than tuning problems:
#
#   1. `episode_title` was a searchable attribute and every episode title
#      contains 呱吉, so searching for it matched all ~2.6M segment docs. The
#      implementation pulled one flat window of hits and derived every number
#      from it, so the window size WAS the reported total: 呱吉, 采翎, 電腦 and
#      選舉 all reported exactly 1000 matches against true counts of 8489,
#      2597, 899 and 1137, and episodes past the window were unreachable.
#
#   2. Meilisearch tokenizes CJK per character, so 電腦 matched any segment
#      containing 電 or 腦 — the top-ranked result was an episode about
#      電踏大叔 with no 電腦 in it at all. Quoting the query as a phrase fixed
#      that, but then broke 采翎: jieba segments a name differently in
#      isolation than in running text, so the phrase never lined up and the
#      co-host's name returned zero results despite 2597 real matches.
#
# Defect 2 has no general fix — a custom dictionary only covers names someone
# thought to add, and every new guest is a fresh chance to silently return
# nothing. Postgres has none of these failure modes: LIKE is exactly the
# literal-substring semantics users expect, and it is also *faster* here
# (measured on production: 0.37-0.40s to scan 2.6M rows, including a query
# matching 708k of them, against 1.17-2.14s for the Meilisearch path).
#
# So content matching, counting, ordering and snippets all come from Postgres:
#
#   1. Counts   — GROUP BY over segments, exact, one sequential scan.
#   2. Titles   — ILIKE over the ~820 episode rows.
#   3. Snippets — window function capped per episode, via the episode_id index.
#
# Meilisearch is no longer in the read path. Ingest still indexes into it, so
# nothing breaks if this is reverted.
#
# Scaling note: the count query is a sequential scan on every search. That is
# comfortable at current traffic; a pg_trgm GIN index on segments.text is the
# fix if concurrency grows.
# ---------------------------------------------------------------------------

# Snippets rendered per episode. The card expands to show these, so an episode
# with 8,000 matches must not emit 8,000 rows — the exact count is still
# reported in hit_count.
HITS_PER_EPISODE = 20

# Long transcript lines are cropped around the first match so a card does not
# render a wall of text. Segments are usually short, so this rarely triggers.
SNIPPET_MAX_CHARS = 140

# Minimum query length to count toward popular-keyword analytics.
# Single-character queries add noise and dominate rankings.
MIN_LOGGABLE_QUERY_LEN = 2

# ---------------------------------------------------------------------------
# Script conversion is a SUGGESTION, never part of matching (BUG-02).
#
# Ingest already normalizes transcripts to Traditional, and the corpus is
# clean: eleven Simplified-only characters (电 脑 个 们 国 说 学 这 时 会 麽)
# return zero rows. Nobody searches in Simplified either — zero Simplified
# queries among the top 30 over 90 days.
#
# Converting queries anyway cost real users, because Simplification merged
# distinct Traditional characters and converting back is a guess. Matching a
# term against its converted spelling meant:
#
#     里 also matched 裡  -> +12,277 unrelated segments
#     干 also matched 乾  -> +3,127
#     采 also matched 採  -> +1,248
#
# and converting unconditionally (the first attempt) made 采翎 return zero
# against 2597 real matches, because s2t rewrites it to 採翎.
#
# So matching uses exactly what the user typed. When that finds nothing, the
# other script is offered as a suggestion the user can click. Being a
# suggestion rather than a silent rewrite makes it safe in both directions,
# which also covers 採翎 -> 采翎.
# ---------------------------------------------------------------------------
_s2t = opencc.OpenCC("s2t")
_t2s = opencc.OpenCC("t2s")


def _script_alternatives(q: str) -> list[str]:
    """Other-script spellings of `q`, for a did-you-mean on an empty result."""
    seen, out = {q}, []
    for converted in (_s2t.convert(q), _t2s.convert(q)):
        if converted not in seen:
            seen.add(converted)
            out.append(converted)
    return out

# Wrapped around matches in `highlighted_text`. The frontend splits on these
# exact strings and renders everything else as literal React text (SEC-01), so
# changing them requires changing HighlightedText in SearchResults.tsx too.
HIGHLIGHT_PRE_TAG = "<mark>"
HIGHLIGHT_POST_TAG = "</mark>"


# Whitelist for the `show` query parameter. Kept as a whitelist rather than an
# escape because it is also the safest shape (SEC-04).
ALLOWED_SHOWS = set(settings.show_keywords.keys()) | {settings.default_show}


def _tokens(q: str) -> list[str]:
    """Split a query into the terms that must all be present.

    Multi-term queries require every term somewhere in the same segment, but
    not adjacent to each other — 呱吉 電腦 finds segments mentioning both.
    """
    return [t for t in (part.strip() for part in q.split()) if t]


def _ilike_literal(s: str) -> str:
    """Escape LIKE wildcards so a query is matched as a literal substring.

    Without this, searching for `%` would match every row, and `_` would match
    any single character.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ilike_all(column, terms: list[str]):
    """Require every term, matched as a literal case-insensitive substring."""
    return and_(*[
        column.ilike(f"%{_ilike_literal(t)}%", escape="\\") for t in terms
    ])


def _highlight(text: str, terms: list[str]) -> str:
    """HTML-escape `text` and wrap literal occurrences of `terms` in <mark>.

    Replaces Meilisearch's _formatted output. Everything that is not a match
    is escaped, and the tags are the only markup emitted, so the result is
    safe for the frontend's tag-splitting renderer (SEC-01).
    """
    text = text or ""
    lowered = text.lower()
    needles = [t.lower() for t in terms if t]
    if not needles:
        return html.escape(text, quote=False)

    out: list[str] = []
    i = 0
    while i < len(text):
        # Earliest match across all terms; longest wins on a tie so that
        # overlapping terms don't produce a truncated highlight.
        best_at, best_len = -1, 0
        for n in needles:
            at = lowered.find(n, i)
            if at != -1 and (best_at == -1 or at < best_at
                             or (at == best_at and len(n) > best_len)):
                best_at, best_len = at, len(n)
        if best_at == -1:
            break
        out.append(html.escape(text[i:best_at], quote=False))
        out.append(HIGHLIGHT_PRE_TAG)
        out.append(html.escape(text[best_at:best_at + best_len], quote=False))
        out.append(HIGHLIGHT_POST_TAG)
        i = best_at + best_len
    out.append(html.escape(text[i:], quote=False))
    return "".join(out)


def _crop(text: str, terms: list[str], limit: int = SNIPPET_MAX_CHARS) -> str:
    """Trim a long transcript line to a window around its first match."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    lowered = text.lower()
    positions = [p for p in (lowered.find(t.lower()) for t in terms) if p != -1]
    first = min(positions) if positions else 0
    start = max(0, first - limit // 3)
    end = min(len(text), start + limit)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


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

    Matching is literal substring, case-insensitive, over segment text and
    episode titles. See the note at the top of this module for why this runs
    against Postgres rather than Meilisearch.
    """
    if show is not None and show not in ALLOWED_SHOWS:
        raise HTTPException(status_code=400, detail="Invalid show name")

    q = q.strip()
    terms = _tokens(q)
    if not terms:
        raise HTTPException(status_code=400, detail="Empty query")

    # ------------------------------------------------------------------
    # Query 1 — exact per-episode content counts.
    # ------------------------------------------------------------------
    count_stmt = (
        select(Segment.episode_id, func.count(Segment.id))
        .where(_ilike_all(Segment.text, terms))
        .group_by(Segment.episode_id)
    )
    if show:
        count_stmt = count_stmt.where(
            Segment.episode_id.in_(select(Episode.id).where(Episode.show == show))
        )
    content_counts: dict[int, int] = {
        ep_id: n for ep_id, n in (await db.execute(count_stmt)).all()
    }

    # ------------------------------------------------------------------
    # Query 2 — title matches, over the ~820 episode rows.
    # ------------------------------------------------------------------
    title_stmt = select(Episode.id, Episode.published_at).where(
        _ilike_all(Episode.title, terms)
    )
    if show:
        title_stmt = title_stmt.where(Episode.show == show)
    title_rows = (await db.execute(title_stmt)).all()

    # An episode with real content matches is never "title-only".
    title_only = [row for row in title_rows if row[0] not in content_counts]
    title_only.sort(key=lambda row: row[1] or datetime.min, reverse=True)
    title_only_ids = {row[0] for row in title_only}

    # ------------------------------------------------------------------
    # Ranking: episodes that discuss the term most come first. Meilisearch's
    # relevance score is gone with it, and for transcript search this is both
    # more useful and explainable — an episode mentioning a topic 40 times is
    # more about it than one mentioning it once. Newest first breaks ties.
    # Title-only episodes trail the content matches.
    # ------------------------------------------------------------------
    published: dict[int, datetime] = {}
    if content_counts:
        rows = await db.execute(
            select(Episode.id, Episode.published_at)
            .where(Episode.id.in_(list(content_counts)))
        )
        published = {ep_id: (pub or datetime.min) for ep_id, pub in rows}

    content_order = sorted(
        content_counts,
        key=lambda ep_id: (-content_counts[ep_id],
                           -(published.get(ep_id, datetime.min).timestamp()),
                           ep_id),
    )
    ordered_episode_ids = content_order + [row[0] for row in title_only]

    total_episodes = len(ordered_episode_ids)
    # A title-only episode contributes exactly one row, matching how it renders.
    # Summing its segments would report every segment in the episode.
    total_segment_matches = sum(content_counts.values()) + len(title_only)

    start = (page - 1) * per_page
    page_episode_ids = ordered_episode_ids[start:start + per_page]
    page_content_ids = [e for e in page_episode_ids if e not in title_only_ids]

    # ------------------------------------------------------------------
    # Query 3 — snippets for this page's content episodes. The window function
    # caps rows per episode inside the database, so an episode with thousands
    # of matches doesn't stream thousands of rows back to trim in Python.
    # ------------------------------------------------------------------
    grouped: dict[int, list[dict]] = {}
    if page_content_ids:
        ranked_segments = (
            select(
                Segment.id,
                Segment.episode_id,
                Segment.start_time,
                Segment.end_time,
                Segment.text,
                func.row_number().over(
                    partition_by=Segment.episode_id,
                    order_by=Segment.start_time.asc(),
                ).label("rn"),
            )
            .where(
                Segment.episode_id.in_(page_content_ids),
                _ilike_all(Segment.text, terms),
            )
            .subquery()
        )
        rows = await db.execute(
            select(ranked_segments).where(ranked_segments.c.rn <= HITS_PER_EPISODE)
        )
        for row in rows:
            grouped.setdefault(row.episode_id, []).append({
                "id": row.id,
                "episode_id": row.episode_id,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "text": row.text,
            })

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
        # SEC-01: _highlight escapes everything except the <mark> tags it emits
        # itself; neighbor context is escaped as plain text.
        highlighted = _highlight(_crop(hit["text"], terms), terms)
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
            # Exact count from the GROUP BY — not len(hits), which is capped
            # at HITS_PER_EPISODE. hits_shown lets the UI say so.
            "hit_count": content_counts.get(ep_id, 0),
            "hits_shown": len(ep_hits),
            "hits": ep_hits,
        })

    # Nothing found: offer the other script, if it would have found something.
    # Only runs on an empty result, so the normal path pays nothing for it.
    suggestion: str | None = None
    if not ordered_episode_ids:
        for alt in _script_alternatives(q):
            alt_terms = _tokens(alt)
            if not alt_terms:
                continue
            found = await db.execute(
                select(Segment.id).where(_ilike_all(Segment.text, alt_terms)).limit(1)
            )
            if found.scalar_one_or_none() is not None:
                suggestion = alt
                break

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
        "suggestion": suggestion,
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
