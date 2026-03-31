"use client";

import { SearchHit } from "@/lib/api";
import { formatTime } from "@/lib/api";

interface SearchResultsProps {
  hits: SearchHit[];
  totalHits: number;
  query: string;
}

export default function SearchResults({ hits, totalHits, query }: SearchResultsProps) {
  if (hits.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">找不到「{query}」的相關結果</p>
        <p className="mt-2">試試其他關鍵字？</p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-sm text-gray-500 mb-4">
        找到約 {totalHits} 筆結果
      </p>
      <div className="space-y-4">
        {hits.map((hit) => (
          <a
            key={hit.segment_id}
            href={`/episode/${hit.episode_id}`}
            className="block bg-white rounded-lg border border-gray-200 p-4 hover:border-blue-300 hover:shadow-sm transition-all"
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="inline-block px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-700 rounded">
                {hit.show}
              </span>
              <span className="text-sm text-gray-500">
                {formatTime(hit.start_time)}
              </span>
              {hit.published_at && (
                <span className="text-sm text-gray-400">
                  {new Date(hit.published_at).toLocaleDateString("zh-TW")}
                </span>
              )}
            </div>
            <h3 className="font-medium text-gray-900 mb-1">{hit.episode_title}</h3>
            <p
              className="text-gray-600 text-sm leading-relaxed"
              dangerouslySetInnerHTML={{ __html: hit.highlighted_text }}
            />
          </a>
        ))}
      </div>
    </div>
  );
}
