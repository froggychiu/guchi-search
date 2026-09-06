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
        "新資料夾": ["新資料夾", "混蛋"],
        "直播": ["直播", "LIVE", "live", "呱吉電台"],
    }
    default_show: str = "其他"

    # Audio download path
    audio_dir: str = "/tmp/guchi_audio"

    # Ingest cron secret (for triggering ingest via API)
    ingest_secret: str = ""

    # Shared with the frontend's server-side rendering only, so its calls are
    # not rate limited as if they were one very busy member of the public —
    # every server-rendered page arrives from a single container IP. Distinct
    # from ingest_secret: this grants nothing except a limiter bypass on
    # read-only endpoints. Empty (the default) disables the bypass entirely.
    internal_token: str = ""

    # Public read limits, per minute. Settings rather than constants because
    # the right ceiling depends on traffic nobody has seen yet — an MCP
    # endpoint's load is not predictable from the website's — and retuning
    # should not need a code deploy.
    #
    # Search is the expensive tier: each call is a sequential scan of ~2.6M
    # segments, ~0.4s of database CPU. The global ceiling is sized so sustained
    # search load stays near one core; the per-client number is set well above
    # a human paging through results (the frontend issues one search per page)
    # so that normal use never sees a 429.
    rate_limit_search_per_client: int = 90
    rate_limit_search_global: int = 150
    rate_limit_read_per_client: int = 300
    rate_limit_read_global: int = 1500

    # CORS — defaults are explicit so prod can never silently fall back to "*".
    # Override in Railway with GUCHI_CORS_ORIGINS='["https://sear.newfolderla.com"]'
    cors_origins: list[str] = [
        "https://sear.newfolderla.com",
        "http://localhost:3000",
    ]

    model_config = {"env_file": ".env", "env_prefix": "GUCHI_"}


settings = Settings()
