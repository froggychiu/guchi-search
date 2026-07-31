"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getVocabRules,
  reviewVocabRule,
  verifySecret,
  type VocabRule,
} from "@/lib/api";

const TABS = [
  { key: "pending", label: "待審核" },
  { key: "active", label: "已啟用" },
  { key: "rejected", label: "已拒絕" },
] as const;

/**
 * Review queue for the transcription glossary.
 *
 * The decision each row asks for is not "is this spelling right" — it is "is
 * the wrong spelling ever legitimate". 彩玲 appears in 38 segments and is
 * always a mistake; 瓜子 appears in 519 and usually means melon seeds.
 * Approving the second would corrupt the transcripts, so the corpus counts
 * are shown as the primary signal rather than as a footnote.
 */
export default function VocabAdminPage() {
  const [secret, setSecret] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [rules, setRules] = useState<VocabRule[]>([]);
  const [tab, setTab] = useState<string>("pending");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<{ id: number; text: string; ok: boolean } | null>(null);

  const load = useCallback(
    async (status: string, key: string) => {
      setLoading(true);
      try {
        const data = await getVocabRules(status, key);
        setRules(data.rules);
      } catch {
        setRules([]);
      }
      setLoading(false);
    },
    []
  );

  async function handleLogin() {
    if (!secret.trim()) return;
    setLoginLoading(true);
    setLoginError("");
    const ok = await verifySecret(secret);
    setLoginLoading(false);
    if (ok) setAuthenticated(true);
    else setLoginError("金鑰錯誤，請重新輸入");
  }

  useEffect(() => {
    if (authenticated) load(tab, secret);
  }, [authenticated, tab, secret, load]);

  async function handleReview(id: number, status: "active" | "rejected") {
    try {
      await reviewVocabRule(id, status, secret);
      setRules((prev) => prev.filter((r) => r.id !== id));
      setMsg({ id, text: status === "active" ? "已啟用" : "已拒絕", ok: true });
    } catch (e: unknown) {
      setMsg({ id, text: e instanceof Error ? e.message : "操作失敗", ok: false });
    }
  }

  if (!authenticated) {
    return (
      <main className="nrk-main">
        <h1 className="nrk-page-title">詞彙庫審核</h1>
        <div className="nrk-search" style={{ marginTop: 24 }}>
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            placeholder="管理金鑰"
            className="nrk-search__input"
          />
          <button
            onClick={handleLogin}
            disabled={loginLoading}
            className="nrk-btn nrk-btn--primary"
          >
            {loginLoading ? "驗證中" : "登入"}
          </button>
        </div>
        {loginError && (
          <p style={{ color: "var(--signal-red)", marginTop: 12 }}>{loginError}</p>
        )}
      </main>
    );
  }

  return (
    <main className="nrk-main">
      <h1 className="nrk-page-title">詞彙庫審核</h1>
      <p className="nrk-lede">
        啟用後，之後轉錄的每一集都會自動套用這條修正。
        <strong>「錯誤形式」出現次數很高時要特別小心</strong>
        —— 那通常代表它本身是個正常的詞（例如 瓜子、里昂），
        啟用會改壞正確的內容。
      </p>

      <div className="nrk-scope" role="group" aria-label="狀態">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`nrk-scope__btn${tab === t.key ? " nrk-scope__btn--active" : ""}`}
            aria-pressed={tab === t.key}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="nrk-loading">載入中</div>
      ) : rules.length === 0 ? (
        <div className="nrk-empty">
          <div className="nrk-empty__big">沒有{TABS.find((t) => t.key === tab)?.label}的詞彙規則</div>
        </div>
      ) : (
        <div style={{ marginTop: 20 }}>
          {rules.map((rule) => {
            const risky = (rule.wrong_hits ?? 0) > 100;
            return (
              <div key={rule.id} className="nrk-card" style={{ marginBottom: 12, padding: 16 }}>
                <div className="nrk-vocab-rule">
                  <span className="nrk-vocab-rule__wrong">{rule.wrong_text}</span>
                  <span aria-hidden>→</span>
                  <span className="nrk-vocab-rule__right">{rule.right_text}</span>
                </div>

                <div className="nrk-meta" style={{ marginTop: 10 }}>
                  <span
                    className={`nrk-badge ${risky ? "nrk-badge--red" : "nrk-badge--neutral"}`}
                  >
                    錯誤形式出現 {rule.wrong_hits ?? "?"} 段
                  </span>
                  <span className="nrk-badge nrk-badge--neutral">
                    正確形式出現 {rule.right_hits ?? "?"} 段
                  </span>
                  <span className="nrk-mono" style={{ color: "var(--ink-mute)" }}>
                    {rule.submitter_name}
                  </span>
                </div>

                {risky && (
                  <p className="nrk-vocab-warn">
                    「{rule.wrong_text}」在逐字稿中出現 {rule.wrong_hits} 段，
                    數量偏多。啟用前請先確認它不是一個獨立存在的正常詞彙。
                  </p>
                )}

                {rule.applied_count > 0 && (
                  <p className="nrk-mono" style={{ color: "var(--ink-mute)", marginTop: 8 }}>
                    上次套用時修改了 {rule.applied_count} 處
                  </p>
                )}

                {tab === "pending" && (
                  <div className="nrk-line__form-actions" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      onClick={() => handleReview(rule.id, "active")}
                      className="nrk-btn nrk-btn--primary"
                    >
                      啟用
                    </button>
                    <button
                      type="button"
                      onClick={() => handleReview(rule.id, "rejected")}
                      className="nrk-btn nrk-btn--secondary"
                    >
                      拒絕
                    </button>
                  </div>
                )}

                {msg?.id === rule.id && (
                  <p style={{ color: msg.ok ? "var(--signal-green)" : "var(--signal-red)" }}>
                    {msg.text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
