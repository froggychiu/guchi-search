"use client";

import { EpisodeSummary } from "@/lib/api";

interface EpisodeListProps {
  episodes: EpisodeSummary[];
}

export default function EpisodeList({ episodes }: EpisodeListProps) {
  if (episodes.length === 0) {
    return <p className="text-gray-500 text-center py-8">目前沒有集數</p>;
  }

  return (
    <div className="space-y-3">
      {episodes.map((ep) => (
        <a
          key={ep.id}
          href={`/episode/${ep.id}`}
          className="block bg-white rounded-lg border border-gray-200 p-4 hover:border-blue-300 hover:shadow-sm transition-all"
        >
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-block px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-700 rounded">
              {ep.show}
            </span>
            {ep.published_at && (
              <span className="text-sm text-gray-400">
                {new Date(ep.published_at).toLocaleDateString("zh-TW")}
              </span>
            )}
            {ep.duration_seconds && (
              <span className="text-sm text-gray-400">
                {Math.round(ep.duration_seconds / 60)} 分鐘
              </span>
            )}
          </div>
          <h3 className="font-medium text-gray-900">{ep.title}</h3>
          {ep.transcription_status === "done" ? (
            <span className="text-xs text-green-600 mt-1 inline-block">已轉錄</span>
          ) : (
            <span className="text-xs text-gray-400 mt-1 inline-block">
              {ep.transcription_status}
            </span>
          )}
        </a>
      ))}
    </div>
  );
}
