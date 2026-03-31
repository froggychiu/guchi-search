from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.search import get_search_index
from app.models.episode import Episode, Segment


async def index_episode_segments(db: AsyncSession, episode_id: int):
    """Index all segments of an episode into Meilisearch."""
    episode = await db.get(Episode, episode_id)
    if not episode:
        return

    result = await db.execute(
        select(Segment).where(Segment.episode_id == episode_id)
    )
    segments = result.scalars().all()

    if not segments:
        return

    docs = [seg.to_search_doc(show=episode.show) for seg in segments]
    index = get_search_index()
    # Meilisearch handles batching internally
    index.add_documents(docs)


async def index_all_episodes(db: AsyncSession):
    """Re-index all episodes into Meilisearch."""
    result = await db.execute(select(Episode).where(Episode.transcription_status == "done"))
    episodes = result.scalars().all()

    for episode in episodes:
        await index_episode_segments(db, episode.id)


async def remove_episode_from_index(episode_id: int):
    """Remove all segments of an episode from Meilisearch."""
    index = get_search_index()
    # Delete by filter (Meilisearch v1.2+)
    index.delete_documents_by_filter(f"episode_id = {episode_id}")
