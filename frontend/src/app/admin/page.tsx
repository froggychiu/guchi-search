"use client";

import { useState, useEffect } from "react";
import { getCorrections, reviewCorrection, formatTime, type CorrectionItem } from "@/lib/api";

export default function AdminPage() {
  const [secret, setSecret] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("pending");
  const [actionMsg, setActionMsg] = useState<{ id: number; msg: string; ok: boolean } | null>(null);

  function handleLogin() {
    if (secret.trim()) {
      setAuthenticated(true);
      loadCorrections(1, filter);
    }
  }

  async function loadCorrections(p: number, status: string) {
    try {
      const data = await getCorrections(status, p);
      setCorrections(data.corrections);
      setTotal(data.total);
      setPage(p);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    if (authenticated) {
      loadCorrections(1, filter);
    }
  }, [authenticated, filter]);

  async function handleReview(id: number, action: "approve" | "reject") {
    try {
      await reviewCorrection(id, action, secret);
      setActionMsg({ id, msg: action === "approve" ? "已批准" : "已拒絕", ok: true });
      // Remove from list
      setCorrections((prev) => prev.filter((c) => c.id !== id));
      setTotal((prev) => prev - 1);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失敗";
      setActionMsg({ id, msg, ok: false });
    }
  }

  const totalPages = Math.ceil(total / 20);

  if (!authenticated) {
    return (
      <div className="max-w-md mx-auto mt-16">
        <h1 className="text-2xl font-bold mb-4">管理員登入</h1>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLogin()}
          placeholder="輸入管理金鑰"
          className="w-full border border-gray-300 rounded-lg px-4 py-2 mb-3"
        />
        <button
          onClick={handleLogin}
          className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700"
        >
          登入
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">校對審核</h1>
        <div className="flex gap-2">
          {(["pending", "approved", "rejected"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1 rounded text-sm ${
                filter === s ? "bg-blue-600 text-white" : "bg-gray-200 text-gray-600 hover:bg-gray-300"
              }`}
            >
              {{ pending: "待審核", approved: "已批准", rejected: "已拒絕" }[s]}
            </button>
          ))}
        </div>
      </div>

      <p className="text-sm text-gray-500 mb-4">共 {total} 筆</p>

      {corrections.length === 0 ? (
        <p className="text-gray-400 text-center py-12">沒有{filter === "pending" ? "待審核的" : ""}修正建議</p>
      ) : (
        <div className="space-y-4">
          {corrections.map((c) => (
            <div key={c.id} className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <a
                  href={`/episode/${c.episode_id}`}
                  className="text-sm text-blue-600 hover:underline font-medium"
                >
                  {c.episode_title}
                </a>
                <span className="text-xs text-gray-400 font-mono">
                  {formatTime(c.start_time)}
                </span>
                <span className="text-xs text-gray-400 ml-auto">
                  {c.submitter_name} · {new Date(c.created_at).toLocaleDateString("zh-TW")}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <p className="text-xs text-gray-400 mb-1">原文</p>
                  <p className="text-sm text-gray-600 bg-red-50 rounded p-2">{c.original_text}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-400 mb-1">建議修正</p>
                  <p className="text-sm text-gray-800 bg-green-50 rounded p-2">{c.suggested_text}</p>
                </div>
              </div>

              {filter === "pending" && (
                <div className="flex gap-2">
                  <button
                    onClick={() => handleReview(c.id, "approve")}
                    className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700"
                  >
                    批准
                  </button>
                  <button
                    onClick={() => handleReview(c.id, "reject")}
                    className="px-3 py-1 bg-red-500 text-white text-sm rounded hover:bg-red-600"
                  >
                    拒絕
                  </button>
                </div>
              )}

              {actionMsg && actionMsg.id === c.id && (
                <p className={`text-xs mt-1 ${actionMsg.ok ? "text-green-600" : "text-red-500"}`}>
                  {actionMsg.msg}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2 mt-8">
          <button
            onClick={() => loadCorrections(page - 1, filter)}
            disabled={page <= 1}
            className="px-4 py-2 border rounded-lg disabled:opacity-30 hover:bg-gray-100"
          >
            上一頁
          </button>
          <span className="px-4 py-2 text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => loadCorrections(page + 1, filter)}
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
