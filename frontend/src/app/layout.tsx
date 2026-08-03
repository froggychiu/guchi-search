import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "新資料庫",
  description: "全文檢索呱吉頻道的 Podcast 逐字稿",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW">
      <body>
        <header className="nrk-header">
          <div className="nrk-header__inner">
            <a href="/" className="nrk-lockup">
              <span className="nrk-lockup__mark" aria-hidden="true">
                <svg viewBox="0 0 48 48" width="28" height="28" fill="none">
                  <rect x="3" y="10" width="42" height="32" rx="3" fill="#B5552E" />
                  <path
                    d="M3 13 C3 11.3 4.3 10 6 10 L18 10 L21 14 L42 14 C43.7 14 45 15.3 45 17 L45 21 L3 21 Z"
                    fill="#8F3E1F"
                  />
                  <circle cx="36" cy="30" r="3.5" fill="#F5C443" />
                  <path d="M13 36 L16 30 L19 36 Z" fill="#FAF7F2" />
                </svg>
              </span>
              <span className="nrk-lockup__word">新資料庫</span>
            </a>
            <nav className="nrk-nav">
              <a href="/">檢索</a>
              <a href="/admin" className="nrk-nav__mute">後台</a>
            </nav>
          </div>
        </header>
        {children}
        <footer className="nrk-footer">
          新資料庫 — 呱吉 Podcast 全文檢索
        </footer>
      </body>
    </html>
  );
}
