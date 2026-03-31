# 呱吉 Podcast 檢索系統

全文檢索呱吉頻道（新資料夾、直播）的 Podcast 逐字稿。

## 架構

- **Backend**: Python FastAPI
- **Database**: PostgreSQL
- **Search**: Meilisearch（中文全文檢索）
- **Frontend**: Next.js + Tailwind CSS
- **Transcription**: OpenAI Whisper API

## 本地開發

### 環境需求

- Docker & Docker Compose
- OpenAI API Key

### 啟動

```bash
# 1. 複製環境變數
cp backend/.env.example backend/.env
# 編輯 .env，填入 OPENAI_API_KEY

# 2. 啟動所有服務
OPENAI_API_KEY=sk-your-key docker compose up -d

# 3. 初始化資料庫和搜尋引擎
docker compose exec backend python -m app.scripts.ingest --setup

# 4. 開始轉錄（可用 --limit 控制數量）
docker compose exec backend python -m app.scripts.ingest --limit 5

# 5. 打開瀏覽器
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000/docs
```

### 轉錄指令

```bash
# 轉錄全部待處理集數
docker compose exec backend python -m app.scripts.ingest

# 只轉錄特定節目
docker compose exec backend python -m app.scripts.ingest --show "新資料夾"

# 重新轉錄特定集數
docker compose exec backend python -m app.scripts.ingest --episode-id 42

# 限制一次處理數量（避免 API 費用爆炸）
docker compose exec backend python -m app.scripts.ingest --limit 10
```

## Railway 部署

### 需要建立的服務

1. **PostgreSQL** — 用 Railway 內建 PostgreSQL template
2. **Meilisearch** — 用 Docker image `getmeili/meilisearch:v1.12`
3. **Backend** — 指向 `backend/` 目錄
4. **Frontend** — 指向 `frontend/` 目錄

### 環境變數設定

**Backend:**
```
GUCHI_DATABASE_URL=postgresql+asyncpg://<Railway提供的連線資訊>
GUCHI_DATABASE_URL_SYNC=postgresql://<Railway提供的連線資訊>
GUCHI_MEILISEARCH_URL=http://<meilisearch-service>:7700
GUCHI_MEILISEARCH_API_KEY=<your-key>
GUCHI_OPENAI_API_KEY=<your-openai-key>
```

**Frontend:**
```
NEXT_PUBLIC_API_URL=https://<backend-service>.railway.app
```

### 首次部署後

```bash
# SSH 進 backend service 執行初始化
python -m app.scripts.ingest --setup
python -m app.scripts.ingest --limit 5  # 先測試幾集
python -m app.scripts.ingest             # 全部轉錄
```

## API 端點

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/search?q=關鍵字&show=新資料夾&page=1` | 全文搜尋 |
| GET | `/api/episodes?show=新資料夾&page=1` | 集數列表 |
| GET | `/api/episodes/{id}` | 單集詳情+逐字稿 |
| GET | `/api/shows` | 節目列表 |
| GET | `/api/stats` | 系統統計 |
| GET | `/health` | 健康檢查 |

## 成本估算

- **轉錄（一次性）**: ~600 集 × 50min × $0.006/min ≈ $180 USD ≈ 5,800 TWD
- **Railway 月費**: ~$10-15 USD ≈ 320-480 TWD/月
