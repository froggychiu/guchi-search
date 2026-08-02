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
from app.services.rss_parser import fetch_episodes, download_audio, classify_show
from app.services.transcriber import transcribe_audio, detect_hallucinations
from app.services.indexer import index_episode_segments
from app.services.vocab import apply_rules, load_active_rules, mine_rules_from_corrections
from app.models.episode import Correction


async def setup_database(engine):
    """Create all database tables, and add any columns models have gained."""
    from app.core.schema import sync_columns

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await conn.run_sync(lambda c: sync_columns(c, Base.metadata))
    if added:
        print(f"[OK] Added missing columns: {', '.join(added)}")
    print("[OK] Database tables created.")


async def ingest_episodes(session: AsyncSession, limit: int | None = None):
    """Fetch RSS feed and insert new episodes into DB."""
    print("[...] Fetching RSS feed...")
    episodes_data = fetch_episodes()
    print(f"[OK] Found {len(episodes_data)} episodes in feed.")

    new_count = 0
    for ep_data in episodes_data:
        # Check if already exists by audio_url OR title
        result = await session.execute(
            select(Episode).where(
                (Episode.audio_url == ep_data["audio_url"]) |
                (Episode.title == ep_data["title"])
            )
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

    # Apply the reviewed glossary before anything else looks at the text, so
    # hallucination detection and the stored transcript agree.
    rules = await load_active_rules(session)
    if rules:
        fixed = 0
        for seg_data in segments_data:
            corrected = apply_rules(seg_data["text"], rules)
            if corrected != seg_data["text"]:
                seg_data["text"] = corrected
                fixed += 1
        if fixed:
            print(f"  [vocab] {fixed} segments corrected by {len(rules)} glossary rules.")

    # Detect hallucinations before saving
    hallucination_indices = detect_hallucinations(segments_data)

    # Save segments to DB
    saved_segments = []
    for seg_data in segments_data:
        segment = Segment(episode_id=episode.id, **seg_data)
        session.add(segment)
        saved_segments.append(segment)

    episode.transcription_status = "done"
    await session.commit()

    # Create correction entries for hallucinated segments
    if hallucination_indices:
        for idx in hallucination_indices:
            seg = saved_segments[idx]
            correction = Correction(
                segment_id=seg.id,
                original_text=seg.text,
                suggested_text="（疑似幻覺，建議刪除）",
                submitter_name="系統自動偵測",
            )
            session.add(correction)
        await session.commit()
        print(f"  [WARN] {len(hallucination_indices)} segments flagged as potential hallucinations.")

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
    parser.add_argument("--reindex", action="store_true", help="Re-index all done episodes into Meilisearch")
    parser.add_argument("--reclassify", action="store_true", help="Re-classify all episodes into correct shows")
    parser.add_argument("--dedup", action="store_true", help="Remove duplicate episodes (keep first by ID)")
    parser.add_argument("--retry-errors", action="store_true", help="Reset error/processing episodes to pending and re-transcribe")
    parser.add_argument("--apply-vocab", action="store_true", help="Apply all active glossary rules to existing segments")
    parser.add_argument("--mine-vocab", action="store_true", help="Propose glossary rules from repeated approved corrections")
    parser.add_argument("--min-evidence", type=int, default=None, help="With --mine-vocab: how many matching corrections are required (default 3)")
    parser.add_argument("--normalize-tw", action="store_true", help="Repair non-Taiwan Traditional variants (爲->為, 喫->吃, 纔->才 ...) in existing segments")
    parser.add_argument("--dry-run", action="store_true", help="With --normalize-tw / --apply-vocab: report what would change without writing")
    parser.add_argument("--replace-text", nargs=2, metavar=("OLD", "NEW"), help="Replace text in all segments")
    parser.add_argument("--scan-hallucinations", action="store_true", help="Scan existing transcripts for hallucinations and create correction entries")
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

    if args.reclassify:
        async with session_factory() as session:
            result = await session.execute(select(Episode))
            episodes = result.scalars().all()
            changed = 0
            for ep in episodes:
                new_show = classify_show(ep.title)
                if ep.show != new_show:
                    print(f"  [{ep.show}] → [{new_show}] {ep.title}")
                    ep.show = new_show
                    changed += 1
            await session.commit()
            print(f"[OK] Reclassified {changed} episodes.")
        return

    if args.dedup:
        async with session_factory() as session:
            from sqlalchemy import func as sqlfunc
            # Find duplicate titles
            dup_result = await session.execute(
                select(Episode.title, sqlfunc.count(Episode.id))
                .group_by(Episode.title)
                .having(sqlfunc.count(Episode.id) > 1)
            )
            duplicates = dup_result.all()
            removed = 0
            for title, count in duplicates:
                ep_result = await session.execute(
                    select(Episode).where(Episode.title == title).order_by(Episode.id.asc())
                )
                eps = ep_result.scalars().all()
                # Keep the first, delete the rest
                for ep in eps[1:]:
                    print(f"  [DEL] id={ep.id} {ep.title}")
                    # Delete segments first
                    await session.execute(
                        select(Segment).where(Segment.episode_id == ep.id)
                    )
                    segs = (await session.execute(
                        select(Segment).where(Segment.episode_id == ep.id)
                    )).scalars().all()
                    for seg in segs:
                        await session.delete(seg)
                    await session.delete(ep)
                    removed += 1
            await session.commit()
            print(f"[OK] Removed {removed} duplicate episodes.")
        return

    if args.retry_errors:
        async with session_factory() as session:
            result = await session.execute(
                select(Episode).where(Episode.transcription_status.in_(["error", "processing"]))
            )
            episodes = result.scalars().all()
            for ep in episodes:
                print(f"  [RESET] id={ep.id} [{ep.transcription_status}] → [pending] {ep.title}")
                ep.transcription_status = "pending"
            await session.commit()
            print(f"[OK] Reset {len(episodes)} episodes to pending.")
        return

    if args.normalize_tw:
        # Repairs transcripts produced before the OpenCC config was fixed —
        # see app/services/text_normalize.py for why s2t was the wrong choice.
        from app.scripts.rewrite import print_report, rewrite_segments
        from app.services.text_normalize import normalize_variants

        async with session_factory() as session:
            scanned, changed, subs = await rewrite_segments(
                session,
                normalize_variants,
                dry_run=args.dry_run,
                label="normalize-tw",
            )
            print_report("normalize-tw", scanned, changed, subs, args.dry_run)
        return

    if args.mine_vocab:
        # Propose glossary rules from corrections proofreaders already made.
        # Proposals only — see services/vocab.py.
        from app.services.vocab import MIN_EVIDENCE

        threshold = args.min_evidence or MIN_EVIDENCE
        async with session_factory() as session:
            proposed = await mine_rules_from_corrections(session, threshold)
            print(f"[OK] {len(proposed)} new rules proposed "
                  f"(threshold: {threshold} matching corrections)")
            for wrong, right, count, people in proposed:
                print(f"    {wrong} -> {right}   ({count} corrections, {people} people)")
            if proposed:
                print("[OK] Pending review at /admin/vocab. Nothing is applied yet.")
        return

    if args.apply_vocab:
        # Backfills the glossary over existing transcripts. Separate from
        # approving a rule on purpose: approval decides what future episodes
        # get, this decides whether to rewrite history.
        from app.models.episode import VocabRule
        from app.scripts.rewrite import print_report, rewrite_segments

        async with session_factory() as session:
            rules = await load_active_rules(session)
            if not rules:
                print("[OK] No active glossary rules.")
                return
            print(f"[...] applying {len(rules)} active rules")

            scanned, changed, subs = await rewrite_segments(
                session,
                lambda text: apply_rules(text, rules),
                dry_run=args.dry_run,
                label="apply-vocab",
            )
            print_report("apply-vocab", scanned, changed, subs, args.dry_run)
            if args.dry_run:
                return

            # Record what each rule actually did, so a rule matching far more
            # than its author expected becomes visible in the admin list.
            for wrong, right in rules:
                hits = sum(n for (old, _), n in subs.items() if old == wrong)
                row = await session.execute(
                    select(VocabRule).where(
                        VocabRule.wrong_text == wrong,
                        VocabRule.right_text == right,
                    )
                )
                rule = row.scalar_one_or_none()
                if rule:
                    rule.applied_count = hits
            await session.commit()
        return

    if args.scan_hallucinations:
        from app.services.transcriber import detect_hallucinations
        async with session_factory() as session:
            result = await session.execute(select(Episode).where(Episode.transcription_status == "done"))
            episodes = result.scalars().all()
            total_flagged = 0
            for ep in episodes:
                seg_result = await session.execute(
                    select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.start_time.asc())
                )
                segments = seg_result.scalars().all()

                # Build the format detect_hallucinations expects
                seg_dicts = [
                    {"start_time": s.start_time, "end_time": s.end_time, "text": s.text}
                    for s in segments
                ]
                flagged_indices = detect_hallucinations(seg_dicts)

                for idx in flagged_indices:
                    seg = segments[idx]
                    # Skip if a pending correction already exists
                    existing = await session.execute(
                        select(Correction).where(
                            Correction.segment_id == seg.id,
                            Correction.status == "pending",
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue
                    correction = Correction(
                        segment_id=seg.id,
                        original_text=seg.text,
                        suggested_text="（疑似幻覺，建議刪除）",
                        submitter_name="系統自動偵測",
                    )
                    session.add(correction)
                    total_flagged += 1
                    print(f"  [FLAG] {ep.title} @ {seg.start_time:.0f}s: {seg.text[:50]}")
            await session.commit()
            print(f"[OK] Flagged {total_flagged} segments as potential hallucinations.")
        return

    if args.replace_text:
        old_text, new_text_val = args.replace_text
        async with session_factory() as session:
            result = await session.execute(select(Segment).where(Segment.text.contains(old_text)))
            segments = result.scalars().all()
            for seg in segments:
                seg.text = seg.text.replace(old_text, new_text_val)
            await session.commit()
            print(f"[OK] Replaced '{old_text}' → '{new_text_val}' in {len(segments)} segments.")
        return

    if args.reindex:
        async with session_factory() as session:
            from app.services.indexer import index_all_episodes
            print("[...] Re-indexing all done episodes into Meilisearch...")
            await index_all_episodes(session)
            print("[OK] Re-indexing complete.")
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

        # Keep the glossary growing from proofreading that already happened.
        # Proposals only — nothing reaches a transcript without review.
        try:
            proposed = await mine_rules_from_corrections(session)
            if proposed:
                print(f"[vocab] {len(proposed)} new glossary rules proposed:")
                for wrong, right, count, people in proposed:
                    print(f"    {wrong} -> {right}   ({count} corrections, {people} people)")
        except Exception as e:
            print(f"[WARN] Glossary mining skipped: {e}")

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
