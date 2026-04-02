import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.search import router as search_router
from app.api.corrections import router as corrections_router
from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="新資料庫",
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
app.include_router(corrections_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _run_ingest(limit: int | None = None):
    """Run ingest in background (sync function so BackgroundTasks runs it in a thread)."""
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


def _run_reindex():
    """Run reindex in background."""
    import subprocess
    cmd = ["python", "-m", "app.scripts.ingest", "--reindex"]
    logger.info(f"Starting reindex: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f"Reindex stdout: {result.stdout}")
    if result.returncode != 0:
        logger.error(f"Reindex stderr: {result.stderr}")


@app.post("/api/reindex")
async def trigger_reindex(
    background_tasks: BackgroundTasks,
    x_ingest_secret: str = Header(None),
):
    """Re-index all transcribed episodes into Meilisearch. Protected by secret token."""
    if not settings.ingest_secret:
        raise HTTPException(status_code=503, detail="Ingest secret not configured")
    if x_ingest_secret != settings.ingest_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    background_tasks.add_task(_run_reindex)
    return {"status": "reindex started"}


def _run_maintenance(action: str):
    """Run maintenance tasks in background."""
    import subprocess
    cmd = ["python", "-m", "app.scripts.ingest", f"--{action}"]
    logger.info(f"Starting maintenance: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f"Maintenance stdout: {result.stdout}")
    if result.returncode != 0:
        logger.error(f"Maintenance stderr: {result.stderr}")


@app.post("/api/maintenance/{action}")
async def trigger_maintenance(
    action: str,
    background_tasks: BackgroundTasks,
    x_ingest_secret: str = Header(None),
):
    """Run maintenance tasks: dedup, reclassify, reindex. Protected by secret token."""
    if not settings.ingest_secret:
        raise HTTPException(status_code=503, detail="Ingest secret not configured")
    if x_ingest_secret != settings.ingest_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if action not in ("dedup", "reclassify", "reindex", "retry-errors", "convert-s2t"):
        raise HTTPException(status_code=400, detail="Invalid action")

    background_tasks.add_task(_run_maintenance, action)
    return {"status": f"{action} started"}
