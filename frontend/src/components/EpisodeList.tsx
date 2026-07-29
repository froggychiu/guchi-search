"use client";

import { EpisodeSummary } from "@/lib/api";

interface EpisodeListProps {
  episodes: EpisodeSummary[];
}

export default function EpisodeList({ episodes }: EpisodeListProps) {
  if (episodes.length === 0) {
    return (
      <div className="nrk-empty">
        <div className="nrk-empty__big">目前沒有集數</div>
      </div>
    );
  }

  return (
    <div>
      {episodes.map((ep) => (
        <a key={ep.id} href={`/episode/${ep.id}`} className="nrk-card">
          <div className="nrk-meta">
            <span className="nrk-badge nrk-badge--blue">{ep.show}</span>
            {ep.published_at && (
              <span className="nrk-mono">
                {new Date(ep.published_at).toLocaleDateString("zh-TW")}
              </span>
            )}
            {ep.published_at && ep.duration_seconds && <span className="nrk-dot" />}
            {ep.duration_seconds && (
              <span className="nrk-mono">
                {Math.round(ep.duration_seconds / 60)} 分鐘
              </span>
            )}
          </div>
          <h3 className="nrk-card__title">{ep.title}</h3>
          {ep.transcription_status === "done" ? (
            <span className="nrk-status nrk-status--done">已轉錄</span>
          ) : ep.transcription_status === "pending" ? (
            <span className="nrk-status nrk-status--pending">轉錄中</span>
          ) : ep.transcription_status === "processing" ? (
            <span className="nrk-status nrk-status--pending">轉錄中</span>
          ) : (
            <span className="nrk-status nrk-status--faint">{ep.transcription_status}</span>
          )}
        </a>
      ))}
    </div>
  );
}
