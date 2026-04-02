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
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <header className="bg-white border-b border-gray-200">
          <div className="max-w-4xl mx-auto px-4 py-4">
            <a href="/" className="text-xl font-bold text-blue-600 hover:text-blue-700">
              新資料庫
            </a>
          </div>
        </header>
        <main className="max-w-4xl mx-auto px-4 py-8">{children}</main>
        <footer className="border-t border-gray-200 mt-16 py-6 text-center text-sm text-gray-500">
          新資料庫 — 呱吉 Podcast 全文檢索
        </footer>
      </body>
    </html>
  );
}
