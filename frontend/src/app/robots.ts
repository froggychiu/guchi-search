import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/site";

/**
 * The AI crawlers are named explicitly rather than left to the wildcard.
 *
 * Their defaults differ and change: Google-Extended and Applebot-Extended are
 * opt-out flags that do nothing unless named, and several of these ignore
 * wildcard Allow rules while honoring a group addressed to them directly.
 * Since the point of this site is for these crawlers to find the transcripts,
 * the permission is stated for each one instead of inferred.
 */
const AI_CRAWLERS = [
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  "ClaudeBot",
  "Claude-User",
  "anthropic-ai",
  "PerplexityBot",
  "Google-Extended",
  "Applebot-Extended",
  "CCBot",
  "Bytespider",
  "cohere-ai",
];

// The review queue and glossary screens. Nothing here is secret — the backend
// requires a token — but they are useless in an index and would waste crawl
// budget that belongs to the transcripts.
const PRIVATE_PATHS = ["/admin", "/admin/"];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: PRIVATE_PATHS,
      },
      ...AI_CRAWLERS.map((userAgent) => ({
        userAgent,
        allow: "/",
        disallow: PRIVATE_PATHS,
      })),
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
