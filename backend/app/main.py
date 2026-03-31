from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.search import router as search_router
from app.core.config import settings

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
