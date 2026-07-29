"use client";

import { useEffect, useState } from "react";
import { getContributors, type Contributor } from "@/lib/api";

export default function ContributorsPage() {
  const [contributors, setContributors] = useState<Contributor[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    getContributors(200)
      .then((data) => setContributors(data.contributors))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false));
  }, []);

  // Tied ranks: 三人並列第 1 → 下一個是第 4
  const ranked: { rank: number; c: Contributor }[] = [];
  let lastCount = -1;
  let lastRank = 0;
  contributors.forEach((c, i) => {
    const rank = c.count === lastCount ? lastRank : i + 1;
    ranked.push({ rank, c });
    lastCount = c.count;
    lastRank = rank;
  });

  return (
    <main className="nrk-main">
      <a href="/" className="nrk-back">
        ← 回首頁
      </a>

      <section className="nrk-hero" style={{ marginBottom: 24 }}>
        <div className="nrk-hero__eyebrow">校對貢獻榜</div>
        <h1 className="nrk-hero__title">網友貢獻排行</h1>
        <p className="nrk-hero__stats">
          僅統計被採用的校對，依採用筆數排序
        </p>
      </section>

      {loading && <div className="nrk-loading">載入中</div>}

      {loadError && !loading && (
        <div className="nrk-empty">
          <p>無法載入排行榜，請稍後再試</p>
        </div>
      )}

      {!loading && !loadError && contributors.length === 0 && (
        <div className="nrk-empty">
          <div className="nrk-empty__big">尚未有暱稱貢獻者</div>
          <p>提交校對時填上暱稱，就有機會出現在這裡</p>
        </div>
      )}

      {!loading && !loadError && contributors.length > 0 && (
        <>
          <div className="nrk-leader-table">
            <div className="nrk-leader-table__head">
              <span className="nrk-leader-table__rank">名次</span>
              <span className="nrk-leader-table__name">暱稱</span>
              <span className="nrk-leader-table__count">採用筆數</span>
              <span className="nrk-leader-table__date">首次貢獻</span>
            </div>
            {ranked.map(({ rank, c }) => (
              <div key={c.name} className="nrk-leader-table__row">
                <span className="nrk-leader-table__rank nrk-mono">
                  {rank <= 3 ? medalFor(rank) : rank}
                </span>
                <span className="nrk-leader-table__name">{c.name}</span>
                <span className="nrk-leader-table__count nrk-mono">
                  {c.count.toLocaleString()}
                </span>
                <span className="nrk-leader-table__date nrk-mono">
                  {c.first_at
                    ? new Date(c.first_at).toLocaleDateString("zh-TW")
                    : "—"}
                </span>
              </div>
            ))}
          </div>
          <p
            className="nrk-mono"
            style={{ marginTop: 16, color: "var(--ink-faint)", fontSize: 11 }}
          >
            * 以暱稱統計，若多人取相同暱稱會合併計算。匿名與未填暱稱者不計入。
          </p>
        </>
      )}
    </main>
  );
}

function medalFor(rank: number): string {
  if (rank === 1) return "🥇";
  if (rank === 2) return "🥈";
  if (rank === 3) return "🥉";
  return String(rank);
}
