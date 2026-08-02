import logging
import os
import sys
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
from app.core.schema import sync_columns

# Without this, nothing this module logs is ever seen. `getLogger(__name__)`
# has no handler of its own and propagates to the root logger, which under
# uvicorn has no handler and defaults to WARNING — so every logger.info below
# was silently dropped. Uvicorn's own access log still appeared, which made it
# look like logging worked. Two normalize-tw dry runs were investigated as
# hangs before this was spotted; they had most likely finished, and their
# reports went nowhere.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure all tables exist before serving traffic.
    Idempotent — create_all skips tables that already exist.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # create_all builds missing tables but ignores missing columns on
            # tables that already exist, which is how adding a field to an
            # existing model used to break every read of that table.
            await conn.run_sync(lambda c: sync_columns(c, Base.metadata))
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



def _run_script(args: list[str], label: str) -> None:
    """Run an ingest subcommand, streaming its output into the log as it goes.

    subprocess.run(capture_output=True) buffers everything until the process
    exits, so a job that takes ten minutes looks identical to one that hung.
    normalize-tw walks ~2.6M rows and prints per batch; those lines need to
    arrive while it is still running to be worth anything.
    """
    import subprocess

    cmd = ["python", "-m", "app.scripts.ingest", *args]
    logger.info(f"[{label}] starting: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        logger.info(f"[{label}] {line.rstrip()}")
    code = process.wait()
    if code != 0:
        logger.error(f"[{label}] exited with code {code}")
    else:
        logger.info(f"[{label}] done")


def _run_ingest(limit: int | None = None):
    """Run ingest in background (sync function so BackgroundTasks runs it in a thread)."""
    args = ["--limit", str(limit)] if limit else []
    _run_script(args, "ingest")


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
    _run_script(["--reindex"], "reindex")


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
    _run_script([f"--{action}", *(extra_args or [])], action)


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
    if action not in ("setup", "dedup", "reclassify", "reindex", "retry-errors", "normalize-tw", "apply-vocab", "mine-vocab", "replace-text", "scan-hallucinations"):
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
