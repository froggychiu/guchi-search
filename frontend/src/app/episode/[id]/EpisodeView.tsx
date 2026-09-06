"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  submitCorrection,
  formatTime,
  type EpisodeDetail,
} from "@/lib/api";

/**
 * The interactive half of an episode page: audio seeking, deep links and the
 * correction form.
 *
 * `episode` arrives as a prop from the server component rather than from a
 * fetch in here. That is the whole point of the split — a client component
 * still renders to HTML on the server, so passing the data down puts the full
 * transcript in the initial response, where crawlers can read it. Fetching it
 * in useEffect, as this did before, produced an empty page for anything that
 * does not run JavaScript.
 */
export default function EpisodeView({ episode }: { episode: EpisodeDetail }) {
  const [editingSegId, setEditingSegId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  // Offers the edit as a glossary rule so the same mishearing gets fixed
  // in every other episode. Off by default — most edits are one-offs.
  const [addToVocab, setAddToVocab] = useState(false);
  const [submitterName, setSubmitterName] = useState("");
  const [submitStatus, setSubmitStatus] = useState<{ segId: number; msg: string; ok: boolean } | null>(null);
  const [activeSegId, setActiveSegId] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  /**
   * Honor `?t=` (audio seek) and `#seg-{id}` (scroll + highlight) from the URL.
   * Fires once on mount.
   *
   * Read straight off window.location instead of useSearchParams(): that hook
   * forces the nearest Suspense boundary to render its fallback during static
   * generation, which would push the transcript back out of the server HTML
   * and undo the reason this component takes `episode` as a prop. Both of
   * these are client-only behaviors anyway.
   */
  useEffect(() => {
    if (typeof window === "undefined") return;

    const tParam = new URLSearchParams(window.location.search).get("t");
    const seekSeconds = tParam != null ? parseFloat(tParam) : NaN;

    const segMatch = window.location.hash.match(/^#seg-(\d+)$/);
    const targetSegId = segMatch ? Number(segMatch[1]) : null;

    // Seek audio (set currentTime once metadata is loaded).
    // Don't auto-play — respect browser autoplay policies and user intent.
    if (!Number.isNaN(seekSeconds) && audioRef.current) {
      const audio = audioRef.current;
      const apply = () => {
        audio.currentTime = seekSeconds;
      };
      if (audio.readyState >= 1) apply();
      else audio.addEventListener("loadedmetadata", apply, { once: true });
    }

    // Scroll to segment + briefly highlight
    if (targetSegId != null) {
      setActiveSegId(targetSegId);
      // Wait for DOM to paint, then scroll
      requestAnimationFrame(() => {
        const el = document.getElementById(`seg-${targetSegId}`);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      // Remove the active highlight after a few seconds
      const timer = setTimeout(() => setActiveSegId(null), 3000);
      return () => clearTimeout(timer);
    }
  }, []);

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
      const result = await submitCorrection(
        segId,
        editText.trim(),
        submitterName || "匿名",
        addToVocab
      );
      // Say plainly whether the glossary part took. An edit with several
      // separate changes is still a valid correction but cannot become a
      // rule, and silently ignoring the checkbox would be misleading.
      const msg = result.vocab_rule
        ? `已提交，等待審核。詞彙「${result.vocab_rule.wrong}→${result.vocab_rule.right}」也已送審`
        : addToVocab
          ? "已提交，等待審核。此修改包含多處變動，無法建立詞彙規則"
          : "已提交，等待審核";
      setSubmitStatus({ segId, msg, ok: true });
      setEditingSegId(null);
      setAddToVocab(false);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "提交失敗";
      setSubmitStatus({ segId, msg, ok: false });
    }
  }

  /** Click a transcript line → seek audio there. Plays if paused. */
  const handleSegmentClick = useCallback(
    (startTime: number) => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.currentTime = startTime;
      // Best-effort play; ignore if browser blocks it
      if (audio.paused) audio.play().catch(() => {});
    },
    []
  );

  // Credited above the transcript so the people who fixed these lines are
  // visible on the page their work went into, not only on the leaderboard.
  // Anonymous submitters are already filtered out by the backend.
  const contributors = episode.contributors ?? [];

  return (
    <main className="nrk-main nrk-main--wide">
      <a href="/" className="nrk-back">← 返回</a>

      <div className="nrk-episode-header">
        <div className="nrk-meta">
          <span className="nrk-badge nrk-badge--blue">{episode.show}</span>
          {episode.published_at && (
            <span className="nrk-mono">
              {new Date(episode.published_at).toLocaleDateString("zh-TW")}
            </span>
          )}
          {episode.published_at && episode.duration_seconds && <span className="nrk-dot" />}
          {episode.duration_seconds && (
            <span className="nrk-mono">{Math.round(episode.duration_seconds / 60)} 分鐘</span>
          )}
        </div>
        <h1>{episode.title}</h1>
        {episode.description && <p>{episode.description}</p>}
        {contributors.length > 0 && (
          <p className="nrk-episode-credits">
            <span className="nrk-episode-credits__label">校對貢獻</span>
            {contributors.map((c) => (
              <span
                key={c.name}
                className="nrk-episode-credits__name"
                title={`採用 ${c.count} 筆修正`}
              >
                {c.name}
              </span>
            ))}
          </p>
        )}
      </div>

      {/* Sticky audio player */}
      {episode.audio_url && (
        <div className="nrk-player">
          <audio
            ref={audioRef}
            src={episode.audio_url}
            controls
            preload="metadata"
            className="nrk-player__el"
          >
            你的瀏覽器不支援播放此音訊格式。
          </audio>
        </div>
      )}

      {/* Corrector bar */}
      <div className="nrk-corrector-bar">
        <label htmlFor="submitter-name" style={{ color: "var(--ink-mute)" }}>
          校對者暱稱
        </label>
        <input
          id="submitter-name"
          type="text"
          value={submitterName}
          onChange={(e) => setSubmitterName(e.target.value)}
          placeholder="匿名"
          maxLength={50}
        />
        <span className="nrk-mono">點擊時間戳可跳播 · 點文字旁圖示可建議修正</span>
      </div>

      {/* Transcript */}
      <div className="nrk-transcript">
        {episode.segments.length === 0 ? (
          <div className="nrk-empty" style={{ padding: 32 }}>
            <p>尚未轉錄</p>
          </div>
        ) : (
          episode.segments.map((seg) => {
            const isEditing = editingSegId === seg.id;
            const status = submitStatus?.segId === seg.id ? submitStatus : null;
            const isActive = activeSegId === seg.id;
            return (
              <div
                id={`seg-${seg.id}`}
                key={seg.id}
                className={`nrk-line${isActive ? " nrk-line--active" : ""}`}
              >
                <div className="nrk-line__row">
                  <button
                    type="button"
                    onClick={() => handleSegmentClick(seg.start_time)}
                    className="nrk-line__time"
                    title="從這裡開始播放"
                    aria-label={`從 ${formatTime(seg.start_time)} 開始播放`}
                  >
                    {formatTime(seg.start_time)}
                  </button>
                  {seg.speaker && <span className="nrk-line__speaker">{seg.speaker}</span>}
                  <span className="nrk-line__text">{seg.text}</span>
                  {!isEditing && (
                    <button
                      type="button"
                      onClick={() => startEditing(seg.id, seg.text)}
                      className="nrk-line__edit"
                      aria-label="建議修正"
                      title="建議修正"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        width="14"
                        height="14"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.75"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                  )}
                </div>

                {isEditing && (
                  <div className="nrk-line__form">
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      rows={3}
                      maxLength={2000}
                    />
                    <label className="nrk-vocab-opt">
                      <input
                        type="checkbox"
                        checked={addToVocab}
                        onChange={(e) => setAddToVocab(e.target.checked)}
                      />
                      <span>
                        這是反覆出現的錯字／人名
                        <em>　送審後將自動修正其他集數的相同錯誤</em>
                      </span>
                    </label>
                    <div className="nrk-line__form-actions">
                      <button
                        type="button"
                        onClick={() => handleSubmit(seg.id)}
                        className="nrk-btn nrk-btn--primary"
                      >
                        提交修正
                      </button>
                      <button
                        type="button"
                        onClick={cancelEditing}
                        className="nrk-btn nrk-btn--secondary"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}

                {status && (
                  <div
                    className="nrk-line__status"
                    style={{ color: status.ok ? "var(--signal-green)" : "var(--signal-red)" }}
                  >
                    {status.msg}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </main>
  );
}
