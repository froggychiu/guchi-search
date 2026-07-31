import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.security import check_secret, require_secret

from app.api.search import router as search_router
from app.api.corrections import router as corrections_router
from app.api.vocab import router as vocab_router
from app.core.config import settings
from app.core.database import Base, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure all tables exist before serving traffic.
    Idempotent — create_all skips tables that already exist.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified / created.")
    except Exception as e:
        logger.warning(f"Skipping table create at startup: {e}")
    yield


# SEC-03: hide schema/docs in prod. GUCHI_ENV=development opens them locally.
_ENV = os.getenv("GUCHI_ENV", "production")
_DOCS_ENABLED = _ENV == "development"

app = FastAPI(
    title="新資料庫",
    description="全文檢索呱吉頻道的 Podcast 逐字稿",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
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
app.include_router(vocab_router)


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
    if not check_secret(x_ingest_secret):
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
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    background_tasks.add_task(_run_reindex)
    return {"status": "reindex started"}


def _run_maintenance(action: str, extra_args: list[str] | None = None):
    """Run maintenance tasks in background."""
    import subprocess
    cmd = ["python", "-m", "app.scripts.ingest", f"--{action}"]
    if extra_args:
        cmd.extend(extra_args)
    logger.info(f"Starting maintenance: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f"Maintenance stdout: {result.stdout}")
    if result.returncode != 0:
        logger.error(f"Maintenance stderr: {result.stderr}")


@app.post("/api/maintenance/{action}")
async def trigger_maintenance(
    action: str,
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    x_ingest_secret: str = Header(None),
):
    """Run maintenance tasks: dedup, reclassify, reindex. Protected by secret token."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    # "setup" re-applies Meilisearch index settings only — no documents are
    # touched, so it is the cheap way to roll out a settings change.
    if action not in ("setup", "dedup", "reclassify", "reindex", "retry-errors", "normalize-tw", "apply-vocab", "replace-text", "scan-hallucinations"):
        raise HTTPException(status_code=400, detail="Invalid action")

    # normalize-tw and apply-vocab rewrite existing transcripts, so both
    # support a preview pass before committing to it. Results
    # land in the deployment log rather than the response — this is a
    # background task, it cannot report back.
    extra = ["--dry-run"] if dry_run and action in ("normalize-tw", "apply-vocab") else None
    background_tasks.add_task(_run_maintenance, action, extra)
    return {"status": f"{action} started{' (dry run)' if extra else ''}"}


class ReplaceTextRequest(BaseModel):
    old_text: str
    new_text: str


@app.post("/api/replace-text")
async def replace_text(
    body: ReplaceTextRequest,
    background_tasks: BackgroundTasks,
    x_ingest_secret: str = Header(None),
):
    """Replace text across all segments. Protected by secret token."""
    if not check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")

    background_tasks.add_task(_run_maintenance, "replace-text", [body.old_text, body.new_text])
    return {"status": f"replacing '{body.old_text}' → '{body.new_text}'"}
