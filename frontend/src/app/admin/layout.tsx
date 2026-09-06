import type { Metadata } from "next";

/**
 * robots.txt asks crawlers not to walk /admin, but a URL that leaks some
 * other way (a Referer header, a pasted link) can still be indexed without
 * ever being crawled. This header is what actually keeps the review screens
 * out of an index.
 *
 * It lives in a layout because the admin pages are client components, and
 * "use client" modules cannot export metadata.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false, nocache: true },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
