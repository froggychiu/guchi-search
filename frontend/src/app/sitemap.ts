import type { MetadataRoute } from "next";

import { getAllEpisodesForSitemap } from "@/lib/server-api";
import { SITE_URL } from "@/lib/site";

/**
 * Regenerated hourly. The episode list is the only part that changes — the
 * nightly ingest cron adds new episodes — and an hour-stale sitemap costs
 * nothing, whereas rebuilding it per request would walk the episode list
 * eight pages at a time on every crawler hit.
 */
export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const episodes = await getAllEpisodesForSitemap();

  const staticRoutes: MetadataRoute.Sitemap = [
    {
      url: SITE_URL,
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 1,
    },
    {
      url: `${SITE_URL}/contributors`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 0.3,
    },
  ];

  const episodeRoutes: MetadataRoute.Sitemap = episodes.map((ep) => ({
    url: `${SITE_URL}/episode/${ep.id}`,
    lastModified: ep.published_at ? new Date(ep.published_at) : undefined,
    changeFrequency: "monthly",
    // Transcripts are the reason this site exists; rank them above the
    // supporting pages.
    priority: 0.8,
  }));

  return [...staticRoutes, ...episodeRoutes];
}
