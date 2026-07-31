# 測試

兩支獨立腳本，通過回傳 0、失敗回傳非 0。沒有測試框架，也不需要
Meilisearch 或 Postgres —— 搜尋只讀資料庫，測試用暫時的 SQLite 檔。

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tests/test_search.py
.venv/bin/python tests/test_highlight.py
```

`requirements-dev.txt` 不是 `-r requirements.txt`。production 用 python:3.11
建置（見 Dockerfile），那裡釘的 `sqlalchemy==2.0.36` 沒問題，但它在
Python 3.13+ 上會 import 失敗 —— 開發機用較新的 Python 就跑不動測試。
所以 dev 那份用下限而非釘版，production 的釘版留在 `requirements.txt`。

## 為什麼是這些測試

每一段都對應一個**曾經上線過**的缺陷。改動 `app/api/search.py` 後請跑一次。

| 測試段落 | 守住的缺陷 |
|---|---|
| 精確計數 / 分頁涵蓋 | SEARCH-01：計數被抓取窗口截斷，呱吉、采翎、電腦、選舉全都回報剛好 1000 處，排在窗口外的集數翻不到 |
| CJK 過度匹配 | 逐字分詞讓「電腦」命中「電踏大叔」「電話」「電影」 |
| 簡繁提示 | BUG-02：查詢被轉成繁體，把「采翎」改寫成語料庫沒有的「採翎」，回傳 0 筆 |
| 高亮轉義 | SEC-01：`highlighted_text` 會被前端當標記渲染，而校對系統允許任何人送出文字 |
| show 白名單 / LIKE 轉義 | SEC-04：`show` 曾被直接串接進查詢條件 |

## 合成語料

`test_search.py` 建 150 集 × 40 段。**每一集標題都含「呱吉」**，重現 production
的病理 —— 標題匹配會淹沒全部段落。預期數字是從語料建構方式算出來的，不是從
實作反推的，所以實作寫錯時測試會失敗而不是跟著錯。
