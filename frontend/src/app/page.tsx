"use client";

import { useState, useEffect } from "react";
import SearchBar from "@/components/SearchBar";
import SearchResults from "@/components/SearchResults";
import EpisodeList from "@/components/EpisodeList";
import { search, getEpisodes, getShows, getStats, type SearchHit, type EpisodeSummary, type ShowInfo } from "@/lib/api";

export default function Home() {
  const [query, setQuery] = useState("");
  const [activeShow, setActiveShow] = useState<string | undefined>();
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [totalHits, setTotalHits] = useState(0);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [shows, setShows] = useState<ShowInfo[]>([]);
  const [stats, setStats] = useState({ total_episodes: 0, transcribed_episodes: 0, total_segments: 0 });
  const [page, setPage] = useState(1);
  const [totalEpisodes, setTotalEpisodes] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [mode, setMode] = useState<"browse" | "search">("browse");

  useEffect(() => {
    getShows().then((data) => setShows(data.shows)).catch(() => {});
    getStats().then(setStats).catch(() => {});
    loadEpisodes(1);
  }, []);

  async function loadEpisodes(p: number, show?: string) {
    try {
      const data = await getEpisodes(show, p);
      setEpisodes(data.episodes);
      setTotalEpisodes(data.total);
      setPage(p);
    } catch {
      // API not available
    }
  }

  async function handleSearch(q: string) {
    setQuery(q);
    setMode("search");
    setIsSearching(true);
    setPage(1);
    try {
      const result = await search(q, activeShow, 1);
      setHits(result.hits);
      setTotalHits(result.total_hits);
    } catch {
      setHits([]);
      setTotalHits(0);
    }
    setIsSearching(false);
  }

  async function handlePageChange(newPage: number) {
    setPage(newPage);
    if (mode === "search") {
      setIsSearching(true);
      const result = await search(query, activeShow, newPage);
      setHits(result.hits);
      setTotalHits(result.total_hits);
      setIsSearching(false);
    } else {
      await loadEpisodes(newPage, activeShow);
    }
    window.scrollTo(0, 0);
  }

  function handleShowFilter(show: string | undefined) {
    setActiveShow(show);
    setPage(1);
    if (mode === "search" && query) {
      search(query, show, 1).then((result) => {
        setHits(result.hits);
        setTotalHits(result.total_hits);
      });
    } else {
      loadEpisodes(1, show);
    }
  }

  const totalPages = mode === "search"
    ? Math.ceil(totalHits / 20)
    : Math.ceil(totalEpisodes / 20);

  return (
    <div>
      {/* Stats */}
      <div className="text-center mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">呱吉 Podcast 檢索系統</h1>
        <p className="text-gray-500">
          {stats.transcribed_episodes} 集已轉錄 / {stats.total_segments.toLocaleString()} 段文字可搜尋
        </p>
      </div>

      {/* Search */}
      <div className="mb-6">
        <SearchBar initialQuery={query} onSearch={handleSearch} />
      </div>

      {/* Show filter tabs */}
      {shows.length > 0 && (
        <div className="flex gap-2 mb-6 flex-wrap">
          <button
            onClick={() => handleShowFilter(undefined)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
              !activeShow ? "bg-blue-600 text-white" : "bg-gray-200 text-gray-600 hover:bg-gray-300"
            }`}
          >
            全部
          </button>
          {shows.map((show) => (
            <button
              key={show.name}
              onClick={() => handleShowFilter(show.name)}
              className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                activeShow === show.name ? "bg-blue-600 text-white" : "bg-gray-200 text-gray-600 hover:bg-gray-300"
              }`}
            >
              {show.name} ({show.episode_count})
            </button>
          ))}
        </div>
      )}

      {/* Mode toggle */}
      {mode === "search" && (
        <button
          onClick={() => { setMode("browse"); loadEpisodes(1, activeShow); }}
          className="text-sm text-blue-600 hover:underline mb-4 block"
        >
          ← 返回集數列表
        </button>
      )}

      {/* Content */}
      {isSearching ? (
        <div className="text-center py-12 text-gray-400">搜尋中...</div>
      ) : mode === "search" ? (
        <SearchResults hits={hits} totalHits={totalHits} query={query} />
      ) : (
        <EpisodeList episodes={episodes} />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2 mt-8">
          <button
            onClick={() => handlePageChange(page - 1)}
            disabled={page <= 1}
            className="px-4 py-2 border rounded-lg disabled:opacity-30 hover:bg-gray-100"
          >
            上一頁
          </button>
          <span className="px-4 py-2 text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => handlePageChange(page + 1)}
            disabled={page >= totalPages}
            className="px-4 py-2 border rounded-lg disabled:opacity-30 hover:bg-gray-100"
          >
            下一頁
          </button>
        </div>
      )}
    </div>
  );
}
