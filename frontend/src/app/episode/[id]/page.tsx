"use client";

import { useState, useEffect, use } from "react";
import { getEpisode, formatTime, type EpisodeDetail } from "@/lib/api";

export default function EpisodePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [episode, setEpisode] = useState<EpisodeDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getEpisode(Number(id))
      .then(setEpisode)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <div className="text-center py-12 text-gray-400">載入中...</div>;
  }

  if (!episode) {
    return <div className="text-center py-12 text-gray-500">找不到此集數</div>;
  }

  return (
    <div>
      <a href="/" className="text-sm text-blue-600 hover:underline mb-4 block">
        ← 返回
      </a>

      <div className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <span className="inline-block px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-700 rounded">
            {episode.show}
          </span>
          {episode.published_at && (
            <span className="text-sm text-gray-400">
              {new Date(episode.published_at).toLocaleDateString("zh-TW")}
            </span>
          )}
          {episode.duration_seconds && (
            <span className="text-sm text-gray-400">
              {Math.round(episode.duration_seconds / 60)} 分鐘
            </span>
          )}
        </div>
        <h1 className="text-2xl font-bold text-gray-900">{episode.title}</h1>
        {episode.description && (
          <p className="text-gray-600 mt-2 text-sm leading-relaxed">{episode.description}</p>
        )}
      </div>

      {/* Transcript */}
      <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
        {episode.segments.length === 0 ? (
          <p className="p-4 text-gray-500">尚未轉錄</p>
        ) : (
          episode.segments.map((seg) => (
            <div key={seg.id} className="p-3 hover:bg-gray-50">
              <span className="text-xs text-gray-400 font-mono mr-3">
                {formatTime(seg.start_time)}
              </span>
              {seg.speaker && (
                <span className="text-xs font-medium text-blue-600 mr-2">
                  {seg.speaker}
                </span>
              )}
              <span className="text-gray-800 text-sm">{seg.text}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
