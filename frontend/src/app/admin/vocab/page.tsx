"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getVocabRules,
  reviewVocabRule,
  createAdminSession,
  storeAdminSession,
  loadAdminSession,
  clearAdminSession,
  type VocabRule,
} from "@/lib/api";
import AdminTabs from "@/components/AdminTabs";

const TABS = [
  { key: "pending", label: "待審核" },
  { key: "active", label: "已啟用" },
  { key: "rejected", label: "已拒絕" },
] as const;

/** Above this many corpus occurrences, the "wrong" spelling is probably a word. */
const HIGH_CORPUS_HITS = 100;

/**
 * A single character is almost never a safe glossary rule.
 *
 * Mining the 6,012 approved corrections showed why: the highest-evidence
 * proposals were 剛→肛 (37 corrections, but 剛 appears in 20,881 segments),
 * 家→佳 (76,812) and 他→她 (287,169). Those are context-dependent homophone
 * calls a proofreader made inside one sentence — as a global rule each would
 * corrupt tens of thousands of segments.
 *
 * Counts code points rather than UTF-16 units so a rare CJK character outside
 * the BMP is not mistaken for two characters.
 */
function isSingleChar(text: string): boolean {
  return [...text].length === 1;
}

function riskFlags(rule: VocabRule): { key: string; message: string }[] {
  const flags: { key: string; message: string }[] = [];
  if (isSingleChar(rule.wrong_text)) {
    flags.push({
      key: "single",
      message:
        `「${rule.wrong_text}」是單一個字。單字幾乎必然出現在大量無關的語境中，` +
        `校對者當初多半是在特定句子裡做同音字判斷，而不是修一個固定的錯字。` +
        `除非它是異體字（例如 喫→吃），否則不建議啟用。`,
    });
  }
  if ((rule.wrong_hits ?? 0) > HIGH_CORPUS_HITS) {
    flags.push({
      key: "corpus",
      message:
        `「${rule.wrong_text}」在逐字稿中出現 ${rule.wrong_hits?.toLocaleString()} 段，` +
        `數量偏多。啟用前請先確認它不是一個獨立存在的正常詞彙。`,
    });
  }
  return flags;
}

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
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(20);
  const [tab, setTab] = useState<string>("pending");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [msg, setMsg] = useState<{ id: number; text: string; ok: boolean } | null>(null);

  const load = useCallback(
    async (status: string, key: string, p: number) => {
      setLoading(true);
      setLoadError("");
      try {
        const data = await getVocabRules(status, key, p);
        setRules(data.rules);
        setTotal(data.total);
        setPerPage(data.per_page);
        setPage(data.page);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "載入失敗";
        if (msg.includes("expired") || msg.includes("Invalid")) {
          clearAdminSession();
          setAuthenticated(false);
          setLoginError("登入已過期，請重新輸入金鑰");
          return;
        }
        setRules([]);
        setTotal(0);
        // Never let a failed request render as "nothing to review".
        setLoadError(msg);
      }
      setLoading(false);
    },
    []
  );

  async function handleLogin() {
    if (!secret.trim()) return;
    setLoginLoading(true);
    setLoginError("");
    // Trade the secret for a token and forget the secret. Only the token is
    // stored, and it cannot reach the destructive maintenance endpoints.
    const session = await createAdminSession(secret);
    setLoginLoading(false);
    if (session) {
      storeAdminSession(session.token, session.expires_at);
      setSecret(session.token);
      setAuthenticated(true);
    } else {
      setLoginError("金鑰錯誤，請重新輸入");
    }
  }

  useEffect(() => {
    const existing = loadAdminSession();
    if (existing) {
      setSecret(existing);
      setAuthenticated(true);
    }
  }, []);

  useEffect(() => {
    if (authenticated) load(tab, secret, 1);
  }, [authenticated, tab, secret, load]);

  async function handleReview(id: number, status: "active" | "rejected") {
    try {
      await reviewVocabRule(id, status, secret);
      setRules((prev) => prev.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
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
    <main className="nrk-main nrk-main--wide">
      <AdminTabs />
      <h1 className="nrk-page-title">詞彙庫審核</h1>
      <p className="nrk-lede">
        規則有兩個來源：校對者送出修正時勾選，或系統從已批准的校對紀錄中
        自動探勘（同一組修改被獨立做過 3 次以上）。啟用後，之後轉錄的每一集
        都會自動套用。
        兩種情況會被標為高風險：<strong>單字規則</strong>（幾乎必然誤傷，
        除非是異體字），以及<strong>「錯誤形式」在逐字稿出現次數很高</strong>
        （代表它本身是個正常的詞，例如 瓜子、里昂）。
        校對佐證筆數越多，代表這是反覆出現的錯誤而非個人偏好 ——
        但佐證多不等於安全，兩者要分開看。
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
      ) : loadError ? (
        <div className="nrk-empty">
          <div className="nrk-empty__big">載入失敗</div>
          <p style={{ color: "var(--signal-red)" }}>{loadError}</p>
          <p style={{ marginTop: 8 }}>
            這不代表沒有待審核的規則 —— 是請求本身失敗了。
          </p>
          <button
            type="button"
            onClick={() => load(tab, secret, page)}
            className="nrk-btn nrk-btn--primary"
            style={{ marginTop: 16 }}
          >
            重新載入
          </button>
        </div>
      ) : rules.length === 0 ? (
        <div className="nrk-empty">
          <div className="nrk-empty__big">沒有{TABS.find((t) => t.key === tab)?.label}的詞彙規則</div>
        </div>
      ) : (
        <div style={{ marginTop: 20 }}>
          <p className="nrk-mono" style={{ color: "var(--ink-mute)", marginBottom: 12 }}>
            共 {total.toLocaleString()} 條 · 依佐證多寡排序 · 第 {page} /{" "}
            {Math.max(1, Math.ceil(total / perPage))} 頁
          </p>
          {rules.map((rule) => {
            const risks = riskFlags(rule);
            const risky = risks.length > 0;
            return (
              <div key={rule.id} className="nrk-card" style={{ marginBottom: 12, padding: 16 }}>
                <div className="nrk-vocab-rule">
                  <span className="nrk-vocab-rule__wrong">{rule.wrong_text}</span>
                  <span aria-hidden>→</span>
                  <span className="nrk-vocab-rule__right">{rule.right_text}</span>
                  {isSingleChar(rule.wrong_text) && (
                    <span className="nrk-badge nrk-badge--red">單字規則</span>
                  )}
                </div>

                <div className="nrk-meta" style={{ marginTop: 10 }}>
                  <span
                    className={`nrk-badge ${
                      (rule.wrong_hits ?? 0) > HIGH_CORPUS_HITS
                        ? "nrk-badge--red"
                        : "nrk-badge--neutral"
                    }`}
                  >
                    錯誤形式出現 {rule.wrong_hits ?? "?"} 段
                  </span>
                  <span className="nrk-badge nrk-badge--neutral">
                    正確形式出現 {rule.right_hits ?? "?"} 段
                  </span>
                  <span className="nrk-badge nrk-badge--green">
                    {rule.evidence_count} 筆校對佐證
                  </span>
                  <span className="nrk-mono" style={{ color: "var(--ink-mute)" }}>
                    {rule.source === "mined" ? "自動探勘" : rule.submitter_name}
                  </span>
                </div>

                {risky && (
                  <div className="nrk-vocab-warn">
                    {risks.map((risk) => (
                      <p key={risk.key} style={{ margin: "4px 0" }}>
                        {risk.message}
                      </p>
                    ))}
                  </div>
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

          {total > perPage && (
            <div className="nrk-pagination">
              <button
                type="button"
                onClick={() => load(tab, secret, page - 1)}
                disabled={page <= 1}
                className="nrk-btn nrk-btn--secondary"
              >
                上一頁
              </button>
              <button
                type="button"
                onClick={() => load(tab, secret, page + 1)}
                disabled={page >= Math.ceil(total / perPage)}
                className="nrk-btn nrk-btn--secondary"
              >
                下一頁
              </button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
