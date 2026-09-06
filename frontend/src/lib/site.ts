/**
 * Canonical public origin, used for metadataBase, canonical URLs, sitemap
 * entries and robots.txt.
 *
 * Hard-coded default rather than a required env var: getting this wrong emits
 * a sitemap full of localhost URLs to crawlers, and a missing variable in
 * Railway should not be able to cause that silently.
 */
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL || "https://sear.newfolderla.com"
).replace(/\/$/, "");

export const SITE_NAME = "新資料庫";
export const SITE_TAGLINE = "呱吉 Podcast 全文檢索";
