"use client";

import { usePathname } from "next/navigation";

/**
 * Navigation between the two admin queues.
 *
 * The glossary review page shipped with no link pointing at it from anywhere,
 * so the only way in was typing the URL. Both queues are admin work on the
 * same transcripts, so they belong behind one visible set of tabs.
 */
const TABS = [
  { href: "/admin", label: "校對審核" },
  { href: "/admin/vocab", label: "詞彙庫審核" },
];

export default function AdminTabs() {
  const pathname = usePathname();
  return (
    <nav className="nrk-admin-tabs" aria-label="後台">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <a
            key={tab.href}
            href={tab.href}
            className={`nrk-admin-tab${active ? " nrk-admin-tab--active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            {tab.label}
          </a>
        );
      })}
    </nav>
  );
}
