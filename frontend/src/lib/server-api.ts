/**
 * Server-side data fetching for rendered HTML (episode pages, sitemap).
 *
 * Why this exists separately from api.ts: everything in api.ts runs in the
 * browser, after hydration, which is exactly why the transcripts were
 * invisible to crawlers. GPTBot, ClaudeBot and PerplexityBot do not execute
 * JavaScript, so a page whose content arrives via useEffect is a blank page
 * to them — 786 episodes and ~2.6M transcript segments that no AI could see.
 *
 * These functions run during render on the server, so what they return ends
 * up in the initial HTML.
 *
 * Failures return null / [] rather than throwing. A backend hiccup should
 * degrade one page, not fail the whole build or the sitemap.
 */

import { API_BASE, fetchWithRetry, type EpisodeDetail, type EpisodeSummary } from "./api";

/**
 * One hour in Next's Data Cache.
 *
 * This is not optional politeness. Next 15 defaults fetch to `no-store`, so
 * without it every crawler request re-fetches a full transcript — an episode
 * is thousands of segments, and a crawler walking all 832 of them would do
 * that on every pass. With it, the backend sees each episode at most once an
 * hour no matter how hard the page is hit.
 */
const CACHE: RequestInit = { next: { revalidate: 3600 } };

/**
 * Identifies these calls to the backend's read limiter.
 *
 * Every server-rendered page fetches from one container IP, so without this a
 * crawler walking the episode list would hit the per-client limit and get 429s
 * cached into the HTML. Deliberately NOT prefixed NEXT_PUBLIC_, so it stays on
 * the server and never ships to a browser. Unset locally and in any
 * deployment that has not configured it, where the limiter simply applies.
 */
function internalHeaders(): HeadersInit | undefined {
  const token = process.env.INTERNAL_API_TOKEN;
  return token ? { "X-Internal-Token": token } : undefined;
}

function serverFetchOptions(): RequestInit {
  const headers = internalHeaders();
  return headers ? { ...CACHE, headers } : CACHE;
}

/** Full episode with transcript, for server rendering. Null if missing. */
export async function getEpisodeSSR(id: number): Promise<EpisodeDetail | null> {
  try {
    const res = await fetchWithRetry(`${API_BASE}/api/episodes/${id}`, serverFetchOptions());
    if (!res.ok) return null;
    return (await res.json()) as EpisodeDetail;
  } catch {
    return null;
  }
}

/** Hard ceiling so a malformed `total` cannot spin this forever. */
const MAX_SITEMAP_PAGES = 50;
const PER_PAGE = 100;

/**
 * Every episode, for the sitemap. Walks the paginated list endpoint, which
 * caps per_page at 100 — ~8 requests for the current 786 episodes.
 */
export async function getAllEpisodesForSitemap(): Promise<EpisodeSummary[]> {
  const all: EpisodeSummary[] = [];

  try {
    for (let page = 1; page <= MAX_SITEMAP_PAGES; page++) {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(PER_PAGE),
        sort: "newest",
      });
      const res = await fetchWithRetry(`${API_BASE}/api/episodes?${params}`, serverFetchOptions());
      if (!res.ok) break;

      const body = (await res.json()) as { total: number; episodes: EpisodeSummary[] };
      const batch = body.episodes ?? [];
      all.push(...batch);

      if (batch.length < PER_PAGE || all.length >= (body.total ?? 0)) break;
    }
  } catch {
    // Partial list is still a valid sitemap; an empty one still lists the
    // static routes below it.
  }

  return all;
}
