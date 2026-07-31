"use client";

import { useState } from "react";
import { EpisodeSearchResult, formatTime } from "@/lib/api";

/**
 * Renders backend-supplied highlighted text without dangerouslySetInnerHTML.
 *
 * The backend HTML-escapes user content but preserves our chosen <mark>
 * sentinel tags around matches (see _safe_highlight in backend search.py).
 * Splitting on those exact strings means React renders any other "<", ">",
 * "&" etc. as literal text — defusing stored XSS even if a malicious payload
 * gets indexed.
 */
function HighlightedText({ html }: { html: string }) {
  const parts = html.split(/(<mark>|<\/mark>)/);
  let inMark = false;
  return (
    <>
      {parts.map((p, i) => {
        if (p === "<mark>") {
          inMark = true;
          return null;
        }
        if (p === "</mark>") {
          inMark = false;
          return null;
        }
        if (p === "") return null;
        return inMark ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>;
      })}
    </>
  );
}

interface SearchResultsProps {
  episodes: EpisodeSearchResult[];
  totalEpisodes: number;
  totalSegmentMatches: number;
  query: string;
  suggestion?: string | null;
  onSuggestionClick?: (query: string) => void;
}

export default function SearchResults({
  episodes,
  totalEpisodes,
  totalSegmentMatches,
  query,
  suggestion,
  onSuggestionClick,
}: SearchResultsProps) {
  if (episodes.length === 0) {
    return (
      <div className="nrk-empty">
        <div className="nrk-empty__big">找不到「{query}」的相關結果</div>
        {suggestion ? (
          <p>
            你是不是要找{" "}
            <button
              type="button"
              className="nrk-suggest"
              onClick={() => onSuggestionClick?.(suggestion)}
            >
              {suggestion}
            </button>
            ？
          </p>
        ) : (
          <p>試試其他關鍵字？</p>
        )}
      </div>
    );
  }

  return (
    <div>
      <p
        className="nrk-mono"
        style={{ marginBottom: 16, color: "var(--ink-mute)" }}
      >
        找到 {totalEpisodes.toLocaleString()} 集
        {totalSegmentMatches !== totalEpisodes && (
          <> · 共 {totalSegmentMatches.toLocaleString()} 處</>
        )}
      </p>
      <div>
        {episodes.map((ep) => (
          <EpisodeResultCard key={ep.episode_id} episode={ep} />
        ))}
      </div>
    </div>
  );
}

function EpisodeResultCard({ episode }: { episode: EpisodeSearchResult }) {
  const [expanded, setExpanded] = useState(false);
  const firstHit = episode.hits[0];
  const hasContentHits = !episode.is_title_only_match;

  // Title-only matches link straight to the episode page (no segment to seek to).
  const titleOnlyHref = `/episode/${episode.episode_id}`;

  return (
    <div className={`nrk-card nrk-card--episode${expanded ? " nrk-card--expanded" : ""}`}>
      <button
        type="button"
        className="nrk-card__head"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <div className="nrk-meta">
          <span className="nrk-badge nrk-badge--blue">{episode.show}</span>
          {episode.is_title_only_match ? (
            <span className="nrk-badge nrk-badge--neutral">標題匹配</span>
          ) : (
            <span className="nrk-badge nrk-badge--neutral">
              {episode.hit_count} 處
            </span>
          )}
          {episode.published_at && (
            <>
              <span className="nrk-dot" />
              <span className="nrk-mono">
                {new Date(episode.published_at).toLocaleDateString("zh-TW")}
              </span>
            </>
          )}
          <span className="nrk-card__chevron" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        </div>
        <h3 className="nrk-card__title">{episode.episode_title}</h3>
        {!expanded && hasContentHits && firstHit && (
          <p className="nrk-card__snippet">
            <HighlightedText html={firstHit.highlighted_text} />
          </p>
        )}
      </button>

      {expanded && (
        <div className="nrk-card__hits">
          {episode.is_title_only_match ? (
            <a href={titleOnlyHref} className="nrk-hit nrk-hit--title-only">
              <span className="nrk-mono">前往該集</span>
            </a>
          ) : (
            episode.hits.map((hit) => {
              const href =
                `/episode/${episode.episode_id}` +
                `?t=${encodeURIComponent(hit.start_time.toFixed(2))}` +
                `#seg-${hit.segment_id}`;
              return (
                <a key={hit.segment_id} href={href} className="nrk-hit">
                  <span className="nrk-mono nrk-hit__time">
                    {formatTime(hit.start_time)}
                  </span>
                  <span className="nrk-hit__text">
                    <HighlightedText html={hit.highlighted_text} />
                  </span>
                </a>
              );
            })
          )}
          {!episode.is_title_only_match &&
            episode.hit_count > episode.hits_shown && (
              <p className="nrk-hit__note nrk-mono">
                僅顯示前 {episode.hits_shown} 處，共 {episode.hit_count.toLocaleString()} 處
              </p>
            )}
        </div>
      )}
    </div>
  );
}
