from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/guchi_search"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/guchi_search"

    # Meilisearch
    meilisearch_url: str = "http://localhost:7700"
    meilisearch_api_key: str = ""

    # OpenAI (Whisper) - fallback
    openai_api_key: str = ""

    # Groq (Whisper) - primary
    groq_api_key: str = ""

    # RSS Feed
    rss_feed_url: str = "https://feeds.soundon.fm/podcasts/ecd31076-d12d-46dc-ba11-32d24b41cca5.xml"

    # Show classification keywords
    show_keywords: dict[str, list[str]] = {
        "新資料夾": ["新資料夾"],
        "直播": ["直播", "LIVE", "live"],
    }
    default_show: str = "其他"

    # Audio download path
    audio_dir: str = "/tmp/guchi_audio"

    # Ingest cron secret (for triggering ingest via API)
    ingest_secret: str = ""

    # CORS
    cors_origins: list[str] = ["*"]

    model_config = {"env_file": ".env", "env_prefix": "GUCHI_"}


settings = Settings()
