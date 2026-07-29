"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Page error:", error);
  }, [error]);

  return (
    <main className="nrk-main">
      <div className="nrk-empty">
        <div className="nrk-empty__big">暫時無法載入</div>
        <p style={{ marginBottom: 24 }}>服務可能正在啟動中，請稍候再試一次。</p>
        <button onClick={reset} className="nrk-btn nrk-btn--primary">
          重新載入
        </button>
      </div>
    </main>
  );
}
