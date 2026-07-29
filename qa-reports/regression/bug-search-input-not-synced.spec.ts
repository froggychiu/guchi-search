/**
 * Regression: 點熱門關鍵字觸發搜尋後，搜尋框 input 仍顯示前一次的查詢字。
 *
 * Discovered: 2026-04-29 by qa-tester
 *
 * Repro:
 *   1. 在搜尋框輸入「OpenAI」按搜尋（input 顯示 OpenAI）
 *   2. 點熱門關鍵字「黃國昌」
 *   3. 結果列表已切到「黃國昌」找到 116 集，但搜尋框仍顯示「OpenAI」
 *
 * Root cause（推測）:
 *   src/components/SearchBar.tsx：useState(initialQuery) 只在 mount 讀 prop，
 *   後續 parent 的熱門關鍵字 onClick → handleSearch 並未把 query 推回 SearchBar 內部 state。
 *
 * Suggested fix: 把 SearchBar 改成 controlled component，或加 useEffect 對 initialQuery 做 sync。
 */
import { test, expect } from "@playwright/test";

test("clicking popular keyword should sync the search input value", async ({ page }) => {
  await page.goto("https://sear.newfolderla.com");
  await page.waitForLoadState("networkidle");

  // First search: type and submit something
  const searchInput = page.locator('input[type=text]');
  await searchInput.fill("OpenAI");
  await page.getByRole("button", { name: "搜尋", exact: true }).click();
  await expect(page.getByText(/找到 \d+ 集/)).toBeVisible();
  await expect(searchInput).toHaveValue("OpenAI");

  // Now click a popular keyword
  const keyword = page.locator("button.nrk-popular__item").first();
  const keywordText = (await keyword.innerText()).trim();
  await keyword.click();

  // Wait for new results
  await page.waitForTimeout(2000);

  // Bug: input still shows old query "OpenAI" instead of the popular keyword
  await expect(searchInput).toHaveValue(keywordText);
});
