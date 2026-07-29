/**
 * Regression: 簡體字搜尋 vs 繁體字搜尋結果差異巨大。
 *
 * Discovered: 2026-04-29 by qa-tester
 *
 * 後端對使用者 query 沒做 OpenCC s2t；資料庫 transcript 已轉繁體。
 * 結果：使用簡體輸入法的使用者找不到絕大多數匹配。
 *
 * 數據（2026-04-29 production）:
 *   电脑: 2 episodes
 *   電腦: 393 episodes  (差 196 倍)
 *   电视: 2 episodes
 *   電視: 378 episodes
 *
 * Suggested fix: backend/app/api/search.py 進入 Meilisearch 前對 q 套 OpenCC s2t 一次。
 */
import { test, expect } from "@playwright/test";

const BACKEND = "https://backend-production-b729.up.railway.app";

async function searchCount(request: any, q: string): Promise<number> {
  const res = await request.get(`${BACKEND}/api/search?q=${encodeURIComponent(q)}&page=1`);
  const data = await res.json();
  return data.total_episodes;
}

test("simplified chinese query should find similar count to traditional", async ({ request }) => {
  const simp = await searchCount(request, "电脑");
  const trad = await searchCount(request, "電腦");
  // We expect them to be reasonably close (within 2x). Currently differ ~190x.
  expect(simp).toBeGreaterThan(trad / 2);
});
