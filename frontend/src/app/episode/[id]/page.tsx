"use client";

import { useState, useEffect, use } from "react";
import { getEpisode, submitCorrection, formatTime, type EpisodeDetail } from "@/lib/api";

export default function EpisodePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [episode, setEpisode] = useState<EpisodeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingSegId, setEditingSegId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [submitterName, setSubmitterName] = useState("");
  const [submitStatus, setSubmitStatus] = useState<{ segId: number; msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    getEpisode(Number(id))
      .then(setEpisode)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  function startEditing(segId: number, currentText: string) {
    setEditingSegId(segId);
    setEditText(currentText);
    setSubmitStatus(null);
  }

  function cancelEditing() {
    setEditingSegId(null);
    setEditText("");
  }

  async function handleSubmit(segId: number) {
    if (!editText.trim()) return;
    try {
      await submitCorrection(segId, editText.trim(), submitterName || "匿名");
      setSubmitStatus({ segId, msg: "已提交，等待審核", ok: true });
      setEditingSegId(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "提交失敗";
      setSubmitStatus({ segId, msg, ok: false });
    }
  }

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

      {/* Submitter name input */}
      <div className="mb-4 flex items-center gap-2">
        <label className="text-sm text-gray-500">校對者暱稱：</label>
        <input
          type="text"
          value={submitterName}
          onChange={(e) => setSubmitterName(e.target.value)}
          placeholder="匿名"
          className="border border-gray-300 rounded px-2 py-1 text-sm w-32"
        />
        <span className="text-xs text-gray-400">點擊文字旁的按鈕即可建議修正</span>
      </div>

      {/* Transcript */}
      <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
        {episode.segments.length === 0 ? (
          <p className="p-4 text-gray-500">尚未轉錄</p>
        ) : (
          episode.segments.map((seg) => (
            <div key={seg.id} className="p-3 hover:bg-gray-50 group">
              <div className="flex items-start gap-1">
                <span className="text-xs text-gray-400 font-mono mr-2 mt-0.5 shrink-0">
                  {formatTime(seg.start_time)}
                </span>
                {seg.speaker && (
                  <span className="text-xs font-medium text-blue-600 mr-1 mt-0.5 shrink-0">
                    {seg.speaker}
                  </span>
                )}
                <span className="text-gray-800 text-sm flex-1">{seg.text}</span>
                <button
                  onClick={() => startEditing(seg.id, seg.text)}
                  className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 ml-1"
                  title="建議修正"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </button>
              </div>

              {/* Correction form */}
              {editingSegId === seg.id && (
                <div className="mt-2 ml-12 space-y-2">
                  <textarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    rows={3}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleSubmit(seg.id)}
                      className="px-3 py-1 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
                    >
                      提交修正
                    </button>
                    <button
                      onClick={cancelEditing}
                      className="px-3 py-1 bg-gray-200 text-gray-600 text-sm rounded hover:bg-gray-300"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* Submit status */}
              {submitStatus && submitStatus.segId === seg.id && (
                <div className={`mt-1 ml-12 text-xs ${submitStatus.ok ? "text-green-600" : "text-red-500"}`}>
                  {submitStatus.msg}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
