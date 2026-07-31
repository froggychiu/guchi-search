"use client";

import { useState, useEffect } from "react";
import SearchBar, { type SearchScope } from "@/components/SearchBar";
import SearchResults from "@/components/SearchResults";
import EpisodeList from "@/components/EpisodeList";
import {
  search,
  getEpisodes,
  getStats,
  getPopularKeywords,
  getContributors,
  type EpisodeSearchResult,
  type EpisodeSummary,
  type PopularKeyword,
  type Contributor,
} from "@/lib/api";

export default function Home() {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<SearchScope>("all");
  const [searchEpisodes, setSearchEpisodes] = useState<EpisodeSearchResult[]>([]);
  const [totalSearchEpisodes, setTotalSearchEpisodes] = useState(0);
  const [totalSegmentMatches, setTotalSegmentMatches] = useState(0);
  // Other-script spelling to offer when a search finds nothing.
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [stats, setStats] = useState({ total_episodes: 0, transcribed_episodes: 0, total_segments: 0 });
  const [page, setPage] = useState(1);
  const [totalBrowseEpisodes, setTotalBrowseEpisodes] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [mode, setMode] = useState<"browse" | "search">("browse");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [popularKeywords, setPopularKeywords] = useState<PopularKeyword[]>([]);
  const [topContributors, setTopContributors] = useState<Contributor[]>([]);

  // Translate scope toggle to the show= filter the backend expects.
  // "all" sends no filter; the other three are exact show names from
  // the backend's classification config.
  const scopeShow = scope === "all" ? undefined : scope;

  useEffect(() => {
    async function init() {
      setLoading(true);
      setLoadError(false);
      try {
        const statsData = await getStats();
        setStats(statsData);
        await loadEpisodes(1);
      } catch {
        setLoadError(true);
      }
      setLoading(false);
    }
    init();
    // Popular keywords load independently — don't block the main UI
    getPopularKeywords(7, 10)
      .then((data) => setPopularKeywords(data.keywords))
      .catch(() => {});
    // Contributors leaderboard (top 5) — also async, non-blocking
    getContributors(5)
      .then((data) => setTopContributors(data.contributors))
      .catch(() => {});
  }, []);

  async function loadEpisodes(p: number, show?: string, sort?: "newest" | "oldest") {
    try {
      const data = await getEpisodes(show, p, sort || sortOrder);
      setEpisodes(data.episodes);
      setTotalBrowseEpisodes(data.total);
      setPage(p);
    } catch {
      // ignore
    }
  }

  async function handleSearch(q: string) {
    setQuery(q);
    setMode("search");
    setIsSearching(true);
    setPage(1);
    try {
      const result = await search(q, scopeShow, 1);
      setSearchEpisodes(result.episodes);
      setTotalSearchEpisodes(result.total_episodes);
      setTotalSegmentMatches(result.total_segment_matches);
      setSuggestion(result.suggestion);
    } catch {
      setSearchEpisodes([]);
      setTotalSearchEpisodes(0);
      setTotalSegmentMatches(0);
      setSuggestion(null);
    }
    setIsSearching(false);
  }

  async function handlePageChange(newPage: number) {
    setPage(newPage);
    if (mode === "search") {
      setIsSearching(true);
      const result = await search(query, scopeShow, newPage);
      setSearchEpisodes(result.episodes);
      setTotalSearchEpisodes(result.total_episodes);
      setTotalSegmentMatches(result.total_segment_matches);
      setIsSearching(false);
    } else {
      await loadEpisodes(newPage, scopeShow);
    }
    window.scrollTo(0, 0);
  }

  function handleScopeChange(next: SearchScope) {
    setScope(next);
    setPage(1);
    const nextShow = next === "all" ? undefined : next;
    if (mode === "search" && query) {
      setIsSearching(true);
      search(query, nextShow, 1)
        .then((result) => {
          setSearchEpisodes(result.episodes);
          setTotalSearchEpisodes(result.total_episodes);
          setTotalSegmentMatches(result.total_segment_matches);
        })
        .catch(() => {
          setSearchEpisodes([]);
          setTotalSearchEpisodes(0);
          setTotalSegmentMatches(0);
        })
        .finally(() => setIsSearching(false));
    } else {
      loadEpisodes(1, nextShow);
    }
  }

  function handleSortChange(sort: "newest" | "oldest") {
    setSortOrder(sort);
    setPage(1);
    loadEpisodes(1, scopeShow, sort);
  }

  const totalPages =
    mode === "search"
      ? Math.ceil(totalSearchEpisodes / 20)
      : Math.ceil(totalBrowseEpisodes / 20);

  return (
    <main className="nrk-main">
      {/* Hero */}
      <section className="nrk-hero">
        <div className="nrk-hero__eyebrow">呱吉 Podcast · 全文檢索</div>
        <h1 className="nrk-hero__title">新資料庫</h1>
        <p className="nrk-hero__stats">
          <b>{stats.transcribed_episodes.toLocaleString()}</b> 集已轉錄　·
          <b>{stats.total_segments.toLocaleString()}</b> 段文字可搜尋
        </p>
      </section>

      {/* Search */}
      <SearchBar
        initialQuery={query}
        scope={scope}
        onScopeChange={handleScopeChange}
        onSearch={handleSearch}
      />

      {/* Popular keywords (last 7 days) */}
      {popularKeywords.length > 0 && (
        <div className="nrk-popular">
          <span className="nrk-popular__label">7 日熱門</span>
          {popularKeywords.map((k) => (
            <button
              key={k.keyword}
              onClick={() => handleSearch(k.keyword)}
              className="nrk-popular__item"
              title={`${k.count} 次搜尋`}
            >
              {k.keyword}
            </button>
          ))}
        </div>
      )}

      {/* Contributors leaderboard (top 5) */}
      {topContributors.length > 0 && (
        <div className="nrk-leaders">
          <div className="nrk-leaders__head">
            <span className="nrk-popular__label">校對貢獻榜</span>
            <a href="/contributors" className="nrk-leaders__more">
              完整排行 →
            </a>
          </div>
          <ol className="nrk-leaders__list">
            {topContributors.map((c, i) => (
              <li key={c.name} className="nrk-leaders__item">
                <span className="nrk-leaders__rank">{i + 1}</span>
                <span className="nrk-leaders__name">{c.name}</span>
                <span className="nrk-leaders__count">{c.count} 筆</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Loading / Error states */}
      {loading && <div className="nrk-loading">載入中</div>}
      {loadError && !loading && (
        <div className="nrk-empty">
          <p style={{ marginBottom: 16 }}>服務正在啟動中，請稍候再試</p>
          <button
            onClick={() => window.location.reload()}
            className="nrk-btn nrk-btn--primary"
          >
            重新載入
          </button>
        </div>
      )}

      {/* Sort toggle (browse only) */}
      {mode === "browse" && !loading && !loadError && (
        <div className="nrk-sort">
          <span>排序</span>
          <button
            onClick={() => handleSortChange("newest")}
            className={`nrk-sort__btn${sortOrder === "newest" ? " nrk-sort__btn--active" : ""}`}
          >
            最新
          </button>
          <button
            onClick={() => handleSortChange("oldest")}
            className={`nrk-sort__btn${sortOrder === "oldest" ? " nrk-sort__btn--active" : ""}`}
          >
            最舊
          </button>
        </div>
      )}

      {/* Back to browse from search mode */}
      {mode === "search" && (
        <button
          onClick={() => {
            setMode("browse");
            loadEpisodes(1, scopeShow);
          }}
          className="nrk-back"
        >
          ← 返回集數列表
        </button>
      )}

      {/* Content */}
      {loading || loadError ? null : isSearching ? (
        <div className="nrk-loading">搜尋中</div>
      ) : mode === "search" ? (
        <SearchResults
          episodes={searchEpisodes}
          totalEpisodes={totalSearchEpisodes}
          totalSegmentMatches={totalSegmentMatches}
          query={query}
          suggestion={suggestion}
          onSuggestionClick={handleSearch}
        />
      ) : (
        <EpisodeList episodes={episodes} />
      )}

      {/* Pagination */}
      {!loading && !loadError && totalPages > 1 && (
        <div className="nrk-pagination">
          <button
            onClick={() => handlePageChange(page - 1)}
            disabled={page <= 1}
            className="nrk-btn nrk-btn--secondary"
          >
            上一頁
          </button>
          <span className="nrk-mono nrk-pagination__count">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => handlePageChange(page + 1)}
            disabled={page >= totalPages}
            className="nrk-btn nrk-btn--secondary"
          >
            下一頁
          </button>
        </div>
      )}
    </main>
  );
}
