import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.search import router as search_router
from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="呱吉 Podcast 檢索系統",
    description="全文檢索呱吉頻道的 Podcast 逐字稿",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _run_ingest(limit: int | None = None):
    """Run ingest in background."""
    import subprocess
    cmd = ["python", "-m", "app.scripts.ingest"]
    if limit:
        cmd += ["--limit", str(limit)]
    logger.info(f"Starting ingest: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f"Ingest stdout: {result.stdout}")
    if result.returncode != 0:
        logger.error(f"Ingest stderr: {result.stderr}")


@app.post("/api/ingest")
async def trigger_ingest(
    background_tasks: BackgroundTasks,
    limit: int | None = None,
    x_ingest_secret: str = Header(None),
):
    """Trigger ingestion of new episodes. Protected by secret token."""
    if not settings.ingest_secret:
        raise HTTPException(status_code=503, detail="Ingest secret not configured")
    if x_ingest_secret != settings.ingest_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    background_tasks.add_task(_run_ingest, limit)
    return {"status": "ingest started"}
