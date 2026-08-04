const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Fetch wrapper with automatic retry for cold-start scenarios.
 * Retries up to 3 times with exponential backoff (1s, 2s, 4s).
 */
async function fetchWithRetry(
  url: string,
  options?: RequestInit,
  retries = 3
): Promise<Response> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const res = await fetch(url, {
        ...options,
        signal: AbortSignal.timeout(15000), // 15s timeout per attempt
      });
      if (res.ok) return res;
      // Server returned an error status — if 502/503/504, retry (service waking up)
      if ([502, 503, 504].includes(res.status) && attempt < retries - 1) {
        await sleep(1000 * Math.pow(2, attempt));
        continue;
      }
      return res; // Return non-retryable error responses as-is
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < retries - 1) {
        await sleep(1000 * Math.pow(2, attempt));
      }
    }
  }
  throw lastError || new Error("API request failed after retries");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Parse JSON safely — throws a readable error if the response isn't valid JSON.
 */
async function parseJSON<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      // response wasn't JSON
    }
    throw new Error(detail);
  }
  return res.json();
}

/** A single segment-level match inside an episode. */
export interface SearchHit {
  segment_id: number;
  start_time: number;
  end_time: number;
  text: string;
  highlighted_text: string;
  is_title_only: boolean;
}

/** All hits in a single episode, returned as a group from /api/search. */
export interface EpisodeSearchResult {
  episode_id: number;
  episode_title: string;
  show: string;
  published_at: string | null;
  is_title_only_match: boolean;
  /** Exact number of matching segments in this episode. */
  hit_count: number;
  /** How many of those are in `hits` — the backend caps snippets per episode. */
  hits_shown: number;
  hits: SearchHit[];
}

export interface SearchResult {
  query: string;
  total_episodes: number;
  total_segment_matches: number;
  page: number;
  per_page: number;
  episodes: EpisodeSearchResult[];
  /**
   * Set only when nothing was found and the query written in the other
   * Chinese script would have matched. A suggestion, never applied
   * automatically — see the BUG-02 note in the backend's search.py.
   */
  suggestion: string | null;
}

export interface EpisodeSummary {
  id: number;
  title: string;
  show: string;
  description: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  transcription_status: string;
}

export interface EpisodeDetail extends EpisodeSummary {
  audio_url: string | null;
  segments: {
    id: number;
    speaker: string | null;
    start_time: number;
    end_time: number;
    text: string;
  }[];
}

export interface ShowInfo {
  name: string;
  episode_count: number;
}

export async function search(
  q: string,
  show?: string,
  page = 1
): Promise<SearchResult> {
  const params = new URLSearchParams({ q, page: String(page) });
  if (show) params.set("show", show);
  const res = await fetchWithRetry(`${API_BASE}/api/search?${params}`);
  return parseJSON<SearchResult>(res);
}

export async function getEpisodes(
  show?: string,
  page = 1,
  sort = "newest"
): Promise<{ total: number; page: number; per_page: number; episodes: EpisodeSummary[] }> {
  const params = new URLSearchParams({ page: String(page), sort });
  if (show) params.set("show", show);
  const res = await fetchWithRetry(`${API_BASE}/api/episodes?${params}`);
  return parseJSON(res);
}

export async function getEpisode(id: number): Promise<EpisodeDetail> {
  const res = await fetchWithRetry(`${API_BASE}/api/episodes/${id}`);
  return parseJSON<EpisodeDetail>(res);
}

export async function getShows(): Promise<{ shows: ShowInfo[] }> {
  const res = await fetchWithRetry(`${API_BASE}/api/shows`);
  return parseJSON(res);
}

export async function getStats(): Promise<{
  total_episodes: number;
  transcribed_episodes: number;
  total_segments: number;
}> {
  const res = await fetchWithRetry(`${API_BASE}/api/stats`);
  return parseJSON(res);
}

export interface PopularKeyword {
  keyword: string;
  count: number;
}

export async function getPopularKeywords(
  days = 7,
  limit = 10
): Promise<{ window_days: number; keywords: PopularKeyword[] }> {
  const params = new URLSearchParams({ days: String(days), limit: String(limit) });
  const res = await fetchWithRetry(`${API_BASE}/api/popular-keywords?${params}`);
  return parseJSON(res);
}

export interface Contributor {
  name: string;
  count: number;
  first_at: string | null;
}

export async function getContributors(
  limit = 50
): Promise<{ contributors: Contributor[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await fetchWithRetry(
    `${API_BASE}/api/corrections/contributors?${params}`
  );
  return parseJSON(res);
}

export interface CorrectionItem {
  id: number;
  segment_id: number;
  episode_id: number;
  episode_title: string;
  start_time: number;
  original_text: string;
  suggested_text: string;
  submitter_name: string;
  status: string;
  created_at: string;
}

export interface CorrectionResult {
  status: string;
  id: number;
  /**
   * Non-null when add_to_vocab produced a glossary proposal. The edit may not
   * have been glossary-shaped (several separate changes, or a whole-sentence
   * rewrite), in which case the correction is still recorded and this is null.
   */
  vocab_rule: { wrong: string; right: string } | null;
}

export async function submitCorrection(
  segment_id: number,
  suggested_text: string,
  submitter_name = "匿名",
  add_to_vocab = false
): Promise<CorrectionResult> {
  const res = await fetchWithRetry(`${API_BASE}/api/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      segment_id,
      suggested_text,
      submitter_name,
      add_to_vocab,
    }),
  });
  return parseJSON(res);
}

export interface VocabRule {
  id: number;
  wrong_text: string;
  right_text: string;
  status: string;
  note: string | null;
  submitter_name: string;
  /** "correction" | "mined" | "manual" — how the rule was proposed. */
  source: string;
  /** Independent approved corrections making this same substitution. */
  evidence_count: number;
  applied_count: number;
  created_at: string;
  /** Segments currently containing the misspelling — the review signal. */
  wrong_hits?: number;
  /** Segments already containing the correct form, for comparison. */
  right_hits?: number;
}

export async function getVocabRules(
  status: string,
  secret: string,
  page = 1
): Promise<{
  status: string;
  total: number;
  page: number;
  per_page: number;
  rules: VocabRule[];
}> {
  const params = new URLSearchParams({ status, page: String(page) });
  const res = await fetchWithRetry(`${API_BASE}/api/vocab?${params}`, {
    headers: { "X-Ingest-Secret": secret },
  });
  return parseJSON(res);
}

export async function reviewVocabRule(
  id: number,
  status: "active" | "rejected",
  secret: string
): Promise<{ status: string; id: number }> {
  const res = await fetchWithRetry(`${API_BASE}/api/vocab/${id}/review`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Ingest-Secret": secret,
    },
    body: JSON.stringify({ status }),
  });
  return parseJSON(res);
}

// SEC-05: admin secret travels in the X-Ingest-Secret header so it never
// appears in URLs / Railway access logs / browser history / Referer.
function authHeaders(secret: string): Record<string, string> {
  return { "X-Ingest-Secret": secret };
}

export async function getCorrections(
  status = "pending",
  page = 1,
  secret = ""
): Promise<{ total: number; page: number; per_page: number; corrections: CorrectionItem[] }> {
  const params = new URLSearchParams({ status, page: String(page) });
  const res = await fetchWithRetry(`${API_BASE}/api/corrections?${params}`, {
    headers: authHeaders(secret),
  });
  return parseJSON(res);
}

/**
 * Exchange the admin secret for a short-lived, review-scoped token.
 *
 * Only the token is kept after this. The secret is also the ingest secret and
 * can trigger /api/replace-text, which rewrites all ~2.6M segments; the token
 * is accepted only by the review screens and expires on its own. That is what
 * makes it acceptable to persist across pages at all.
 */
export async function createAdminSession(
  secret: string
): Promise<{ token: string; expires_at: number } | null> {
  try {
    const res = await fetchWithRetry(`${API_BASE}/api/corrections/session`, {
      method: "POST",
      headers: authHeaders(secret),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

const SESSION_KEY = "guchi-admin-session";

/** sessionStorage, not localStorage: the token dies with the browser tab. */
export function storeAdminSession(token: string, expiresAt: number): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token, expiresAt }));
  } catch {
    // Private mode or storage disabled — the session just won't persist.
  }
}

export function loadAdminSession(): string | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const { token, expiresAt } = JSON.parse(raw);
    // Checked here too so an expired token shows the login form rather than
    // a page full of 403s. The backend is what actually enforces it.
    if (!token || typeof expiresAt !== "number" || expiresAt * 1000 <= Date.now()) {
      sessionStorage.removeItem(SESSION_KEY);
      return null;
    }
    return token;
  } catch {
    return null;
  }
}

export function clearAdminSession(): void {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // nothing to do
  }
}

export async function verifySecret(secret: string): Promise<boolean> {
  try {
    const res = await fetchWithRetry(`${API_BASE}/api/corrections/verify-secret`, {
      headers: authHeaders(secret),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function batchApprove(
  ids: number[],
  secret: string
): Promise<{ status: string; approved: number }> {
  const res = await fetchWithRetry(`${API_BASE}/api/corrections/batch-approve`, {
    method: "POST",
    headers: { ...authHeaders(secret), "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  return parseJSON(res);
}

export async function reviewCorrection(
  id: number,
  action: "approve" | "reject",
  secret: string
): Promise<{ status: string }> {
  const res = await fetchWithRetry(`${API_BASE}/api/corrections/${id}/${action}`, {
    method: "POST",
    headers: authHeaders(secret),
  });
  return parseJSON(res);
}

export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}
