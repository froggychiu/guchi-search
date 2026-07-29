"use client";

import { useState, useEffect } from "react";
import {
  getCorrections,
  reviewCorrection,
  verifySecret,
  batchApprove,
  formatTime,
  type CorrectionItem,
} from "@/lib/api";

export default function AdminPage() {
  const [secret, setSecret] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("pending");
  const [actionMsg, setActionMsg] = useState<{ id: number; msg: string; ok: boolean } | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchLoading, setBatchLoading] = useState(false);

  async function handleLogin() {
    if (!secret.trim()) return;
    setLoginLoading(true);
    setLoginError("");
    const ok = await verifySecret(secret);
    setLoginLoading(false);
    if (ok) {
      setAuthenticated(true);
      loadCorrections(1, filter);
    } else {
      setLoginError("金鑰錯誤，請重新輸入");
    }
  }

  async function loadCorrections(p: number, status: string) {
    try {
      const data = await getCorrections(status, p);
      setCorrections(data.corrections);
      setTotal(data.total);
      setPage(p);
      setSelected(new Set());
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
      setCorrections((prev) => prev.filter((c) => c.id !== id));
      setTotal((prev) => prev - 1);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失敗";
      setActionMsg({ id, msg, ok: false });
    }
  }

  async function handleBatchApprove() {
    if (selected.size === 0) return;
    setBatchLoading(true);
    try {
      const result = await batchApprove(Array.from(selected), secret);
      setCorrections((prev) => prev.filter((c) => !selected.has(c.id)));
      setTotal((prev) => prev - result.approved);
      setSelected(new Set());
      setActionMsg({ id: -1, msg: `已批次批准 ${result.approved} 筆`, ok: true });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "批次批准失敗";
      setActionMsg({ id: -1, msg, ok: false });
    }
    setBatchLoading(false);
  }

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === corrections.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(corrections.map((c) => c.id)));
    }
  }

  const totalPages = Math.ceil(total / 20);

  if (!authenticated) {
    return (
      <main className="nrk-main">
        <div className="nrk-login">
          <h1>管理員登入</h1>
          <label htmlFor="admin-secret" className="sr-only" style={{ position: "absolute", left: "-9999px" }}>
            管理金鑰
          </label>
          <input
            id="admin-secret"
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            placeholder="輸入管理金鑰"
          />
          {loginError && <p className="nrk-login__error">{loginError}</p>}
          <button
            onClick={handleLogin}
            disabled={loginLoading}
            className="nrk-btn nrk-btn--primary"
          >
            {loginLoading ? "驗證中" : "登入"}
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="nrk-main nrk-main--wide">
      <div className="nrk-admin-head">
        <h1>校對審核</h1>
        <div className="nrk-chips" style={{ margin: 0 }}>
          {(["pending", "approved", "rejected"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`nrk-chip${filter === s ? " nrk-chip--active" : ""}`}
            >
              {{ pending: "待審核", approved: "已批准", rejected: "已拒絕" }[s]}
            </button>
          ))}
        </div>
      </div>

      <div className="nrk-admin-toolbar">
        <span>共 {total.toLocaleString()} 筆</span>
        {filter === "pending" && corrections.length > 0 && (
          <>
            <span className="nrk-admin-toolbar__spacer" />
            <label className="nrk-admin-toolbar__select">
              <input
                type="checkbox"
                checked={selected.size === corrections.length && corrections.length > 0}
                onChange={toggleSelectAll}
              />
              全選 ({selected.size}/{corrections.length})
            </label>
            <button
              onClick={handleBatchApprove}
              disabled={selected.size === 0 || batchLoading}
              className="nrk-btn nrk-btn--success"
            >
              {batchLoading ? "處理中" : `批次批准 (${selected.size})`}
            </button>
          </>
        )}
      </div>

      {/* Global batch message */}
      {actionMsg && actionMsg.id === -1 && (
        <p
          className="nrk-correction__status"
          style={{
            marginBottom: 12,
            color: actionMsg.ok ? "var(--signal-green)" : "var(--signal-red)",
          }}
        >
          {actionMsg.msg}
        </p>
      )}

      {corrections.length === 0 ? (
        <div className="nrk-empty">
          <div className="nrk-empty__big">
            沒有{filter === "pending" ? "待審核的" : ""}修正建議
          </div>
          {filter === "pending" && <p>有新的建議進來時會顯示在這裡</p>}
        </div>
      ) : (
        <div>
          {corrections.map((c) => {
            const status = actionMsg?.id === c.id ? actionMsg : null;
            const isSelected = selected.has(c.id);
            return (
              <div
                key={c.id}
                className={`nrk-correction${isSelected ? " nrk-correction--selected" : ""}`}
              >
                <div className="nrk-correction__meta">
                  {filter === "pending" && (
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelect(c.id)}
                      aria-label="選取此項"
                    />
                  )}
                  <a href={`/episode/${c.episode_id}`}>{c.episode_title}</a>
                  <span className="nrk-mono">{formatTime(c.start_time)}</span>
                  <span className="nrk-mono">
                    {c.submitter_name} · {new Date(c.created_at).toLocaleDateString("zh-TW")}
                  </span>
                </div>

                <div className="nrk-diff">
                  <div className="nrk-diff__col nrk-diff__col--before">
                    <label>原文</label>
                    <p>{c.original_text}</p>
                  </div>
                  <div className="nrk-diff__col nrk-diff__col--after">
                    <label>建議修正</label>
                    <p>{c.suggested_text}</p>
                  </div>
                </div>

                {filter === "pending" && (
                  <div className="nrk-correction__actions">
                    <button
                      onClick={() => handleReview(c.id, "approve")}
                      className="nrk-btn nrk-btn--success"
                    >
                      批准
                    </button>
                    <button
                      onClick={() => handleReview(c.id, "reject")}
                      className="nrk-btn nrk-btn--destructive"
                    >
                      拒絕
                    </button>
                  </div>
                )}

                {status && (
                  <p
                    className={`nrk-correction__status ${
                      status.ok ? "nrk-correction__status--ok" : "nrk-correction__status--err"
                    }`}
                  >
                    {status.msg}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="nrk-pagination">
          <button
            onClick={() => loadCorrections(page - 1, filter)}
            disabled={page <= 1}
            className="nrk-btn nrk-btn--secondary"
          >
            上一頁
          </button>
          <span className="nrk-mono nrk-pagination__count">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => loadCorrections(page + 1, filter)}
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
