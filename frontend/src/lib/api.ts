const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface SearchHit {
  segment_id: number;
  episode_id: number;
  episode_title: string;
  show: string;
  published_at: string | null;
  speaker: string;
  start_time: number;
  end_time: number;
  text: string;
  highlighted_text: string;
}

export interface SearchResult {
  query: string;
  total_hits: number;
  page: number;
  per_page: number;
  hits: SearchHit[];
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
  const res = await fetch(`${API_BASE}/api/search?${params}`);
  return res.json();
}

export async function getEpisodes(
  show?: string,
  page = 1,
  sort = "newest"
): Promise<{ total: number; page: number; per_page: number; episodes: EpisodeSummary[] }> {
  const params = new URLSearchParams({ page: String(page), sort });
  if (show) params.set("show", show);
  const res = await fetch(`${API_BASE}/api/episodes?${params}`);
  return res.json();
}

export async function getEpisode(id: number): Promise<EpisodeDetail> {
  const res = await fetch(`${API_BASE}/api/episodes/${id}`);
  return res.json();
}

export async function getShows(): Promise<{ shows: ShowInfo[] }> {
  const res = await fetch(`${API_BASE}/api/shows`);
  return res.json();
}

export async function getStats(): Promise<{
  total_episodes: number;
  transcribed_episodes: number;
  total_segments: number;
}> {
  const res = await fetch(`${API_BASE}/api/stats`);
  return res.json();
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

export async function submitCorrection(
  segment_id: number,
  suggested_text: string,
  submitter_name = "匿名"
): Promise<{ status: string; id: number }> {
  const res = await fetch(`${API_BASE}/api/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id, suggested_text, submitter_name }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "提交失敗");
  }
  return res.json();
}

export async function getCorrections(
  status = "pending",
  page = 1
): Promise<{ total: number; page: number; per_page: number; corrections: CorrectionItem[] }> {
  const params = new URLSearchParams({ status, page: String(page) });
  const res = await fetch(`${API_BASE}/api/corrections?${params}`);
  return res.json();
}

export async function reviewCorrection(
  id: number,
  action: "approve" | "reject",
  secret: string
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/api/corrections/${id}/${action}`, {
    method: "POST",
    headers: { "X-Ingest-Secret": secret },
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "操作失敗");
  }
  return res.json();
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
