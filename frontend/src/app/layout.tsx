import type { Metadata } from "next";
import "./globals.css";
import { SITE_NAME, SITE_TAGLINE, SITE_URL } from "@/lib/site";

// Deliberately no episode count: the nightly ingest cron adds episodes, and a
// number baked into every page's meta description would be wrong within weeks.
const SITE_DESCRIPTION =
  "全文檢索呱吉頻道（新資料夾、直播）歷年全部集數的 Podcast 逐字稿，逐句可搜尋，每一句都能跳播回原始音檔對照。";

export const metadata: Metadata = {
  // Without metadataBase, every relative canonical/OG URL below resolves
  // against localhost in the build output.
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} — ${SITE_TAGLINE}`,
    // Episode pages set only their own title; this appends the site name so
    // all 786 of them stop sharing one indistinguishable heading.
    template: `%s — ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: `${SITE_NAME} — ${SITE_TAGLINE}`,
    description: SITE_DESCRIPTION,
    locale: "zh_TW",
  },
  twitter: {
    card: "summary",
    title: `${SITE_NAME} — ${SITE_TAGLINE}`,
    description: SITE_DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      // Defaults truncate the snippet and forbid large previews, which for a
      // transcript archive is the difference between a useful search result
      // and a title with nothing under it.
      "max-snippet": -1,
      "max-image-preview": "large",
      "max-video-preview": -1,
    },
  },
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
