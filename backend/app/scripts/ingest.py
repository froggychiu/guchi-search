"""
Ingestion script: fetch RSS feed, download audio, transcribe, and index.

Usage:
    python -m app.scripts.ingest              # Ingest all new episodes
    python -m app.scripts.ingest --episode-id 5  # Re-transcribe a specific episode
    python -m app.scripts.ingest --setup      # Setup DB tables + search index only
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.core.search import setup_search_index
from app.models.episode import Episode, Segment
from app.services.rss_parser import fetch_episodes, download_audio
from app.services.transcriber import transcribe_audio
from app.services.indexer import index_episode_segments


async def setup_database(engine):
    """Create all database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Database tables created.")


async def ingest_episodes(session: AsyncSession, limit: int | None = None):
    """Fetch RSS feed and insert new episodes into DB."""
    print("[...] Fetching RSS feed...")
    episodes_data = fetch_episodes()
    print(f"[OK] Found {len(episodes_data)} episodes in feed.")

    new_count = 0
    for ep_data in episodes_data:
        # Check if already exists by audio_url
        result = await session.execute(
            select(Episode).where(Episode.audio_url == ep_data["audio_url"])
        )
        if result.scalar_one_or_none():
            continue

        episode = Episode(**ep_data, transcription_status="pending")
        session.add(episode)
        new_count += 1

    await session.commit()
    print(f"[OK] Added {new_count} new episodes to database.")
    return new_count


async def transcribe_episode(session: AsyncSession, episode: Episode):
    """Download, transcribe, and index a single episode."""
    print(f"\n[...] Processing: {episode.title}")

    # Download audio
    print(f"  [dl] Downloading audio...")
    try:
        audio_path = await download_audio(episode.audio_url, episode.id)
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")
        episode.transcription_status = "error"
        await session.commit()
        return

    # Transcribe
    print(f"  [tr] Transcribing ({episode.duration_seconds or '?'}s)...")
    episode.transcription_status = "processing"
    await session.commit()

    try:
        segments_data = transcribe_audio(audio_path)
    except Exception as e:
        print(f"  [ERROR] Transcription failed: {e}")
        episode.transcription_status = "error"
        await session.commit()
        return

    # Save segments to DB
    for seg_data in segments_data:
        segment = Segment(episode_id=episode.id, **seg_data)
        session.add(segment)

    episode.transcription_status = "done"
    await session.commit()
    print(f"  [OK] {len(segments_data)} segments saved.")

    # Index in Meilisearch
    try:
        await index_episode_segments(session, episode.id)
        print(f"  [OK] Indexed in search.")
    except Exception as e:
        print(f"  [WARN] Indexing failed (can retry later): {e}")

    # Clean up audio file
    try:
        os.remove(audio_path)
    except OSError:
        pass


async def main():
    parser = argparse.ArgumentParser(description="Ingest podcast episodes")
    parser.add_argument("--setup", action="store_true", help="Setup DB and search index only")
    parser.add_argument("--episode-id", type=int, help="Transcribe a specific episode")
    parser.add_argument("--limit", type=int, help="Max episodes to transcribe in this run")
    parser.add_argument("--show", type=str, help="Only process episodes from this show")
    args = parser.parse_args()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Always ensure tables exist
    await setup_database(engine)

    if args.setup:
        try:
            setup_search_index()
            print("[OK] Meilisearch index configured.")
        except Exception as e:
            print(f"[WARN] Meilisearch setup: {e}")
        return

    async with session_factory() as session:
        if args.episode_id:
            # Re-transcribe a specific episode
            episode = await session.get(Episode, args.episode_id)
            if not episode:
                print(f"[ERROR] Episode {args.episode_id} not found.")
                return
            await transcribe_episode(session, episode)
            return

        # Normal flow: ingest new episodes, then transcribe pending ones
        await ingest_episodes(session)

        # Setup search index if needed
        try:
            setup_search_index()
        except Exception:
            pass

        # Get pending episodes
        query = select(Episode).where(Episode.transcription_status == "pending")
        if args.show:
            query = query.where(Episode.show == args.show)
        query = query.order_by(Episode.published_at.asc())
        if args.limit:
            query = query.limit(args.limit)

        result = await session.execute(query)
        pending = result.scalars().all()

        if not pending:
            print("[OK] No pending episodes to transcribe.")
            return

        print(f"\n[...] {len(pending)} episodes to transcribe.")
        for i, episode in enumerate(pending, 1):
            print(f"\n--- [{i}/{len(pending)}] ---")
            await transcribe_episode(session, episode)

    print("\n[DONE] Ingestion complete.")


if __name__ == "__main__":
    asyncio.run(main())
