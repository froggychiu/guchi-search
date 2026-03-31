import feedparser
import httpx
import os
from datetime import datetime
from email.utils import parsedate_to_datetime

from app.core.config import settings


def classify_show(title: str) -> str:
    """Classify an episode into a show based on title keywords."""
    for show_name, keywords in settings.show_keywords.items():
        for keyword in keywords:
            if keyword.lower() in title.lower():
                return show_name
    return settings.default_show


def parse_duration(duration_str: str | None) -> int | None:
    """Parse duration string (HH:MM:SS or MM:SS or seconds) to seconds."""
    if not duration_str:
        return None
    parts = duration_str.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(parts[0])
    except ValueError:
        return None


def fetch_episodes() -> list[dict]:
    """Fetch and parse all episodes from the RSS feed."""
    feed = feedparser.parse(settings.rss_feed_url)
    episodes = []

    for entry in feed.entries:
        # Get audio URL from enclosure
        audio_url = ""
        if entry.get("enclosures"):
            audio_url = entry.enclosures[0].get("href", "")

        # Parse published date
        published_at = None
        if entry.get("published"):
            try:
                dt = parsedate_to_datetime(entry.published)
                # Convert to naive UTC datetime for DB storage
                if dt.tzinfo is not None:
                    from datetime import timezone
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                published_at = dt
            except (ValueError, TypeError):
                pass

        # Parse duration
        duration = parse_duration(entry.get("itunes_duration"))

        title = entry.get("title", "")
        episodes.append({
            "title": title,
            "description": entry.get("summary", ""),
            "show": classify_show(title),
            "audio_url": audio_url,
            "published_at": published_at,
            "duration_seconds": duration,
        })

    return episodes


async def download_audio(audio_url: str, episode_id: int) -> str:
    """Download audio file and return local path."""
    os.makedirs(settings.audio_dir, exist_ok=True)
    file_path = os.path.join(settings.audio_dir, f"episode_{episode_id}.mp3")

    if os.path.exists(file_path):
        return file_path

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("GET", audio_url) as response:
            response.raise_for_status()
            with open(file_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)

    return file_path
