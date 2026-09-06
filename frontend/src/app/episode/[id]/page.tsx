import type { Metadata } from "next";
import { notFound } from "next/navigation";

import EpisodeView from "./EpisodeView";
import { getEpisodeSSR } from "@/lib/server-api";
import { SITE_NAME, SITE_URL } from "@/lib/site";
import type { EpisodeDetail } from "@/lib/api";

/**
 * Server component. It fetches the episode and hands it to the client
 * component as a prop, so the transcript is present in the HTML response
 * instead of appearing only after hydration.
 *
 * Rendered on demand and cached for an hour rather than pre-built for all
 * ~786 episodes: a full generateStaticParams would fire 786 transcript
 * fetches on every deploy, and most episodes are not requested between
 * deploys anyway. The first crawler to reach a page pays for it; everyone
 * after that gets the cached HTML.
 */
export const revalidate = 3600;

const DESCRIPTION_MAX = 155;

/** Plain-text, length-capped summary for meta/OG tags. */
function buildDescription(episode: EpisodeDetail): string {
  // RSS descriptions arrive with markup in them; meta tags want plain text.
  const fromDescription = (episode.description ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  // Episodes with no description still deserve a useful snippet, and the
  // opening lines of the transcript are the best one available.
  const source =
    fromDescription ||
    episode.segments
      .slice(0, 12)
      .map((s) => s.text)
      .join("")
      .replace(/\s+/g, " ")
      .trim();

  if (!source) return `${episode.show}｜${episode.title} 的完整逐字稿。`;
  return source.length > DESCRIPTION_MAX
    ? `${source.slice(0, DESCRIPTION_MAX - 1)}…`
    : source;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const episode = await getEpisodeSSR(Number(id));

  if (!episode) {
    return { title: "找不到此集數", robots: { index: false, follow: false } };
  }

  const description = buildDescription(episode);
  const url = `${SITE_URL}/episode/${episode.id}`;

  return {
    title: episode.title,
    description,
    alternates: { canonical: url },
    openGraph: {
      type: "article",
      url,
      siteName: SITE_NAME,
      title: episode.title,
      description,
      locale: "zh_TW",
      publishedTime: episode.published_at ?? undefined,
    },
    twitter: {
      card: "summary",
      title: episode.title,
      description,
    },
  };
}

export default async function EpisodePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const numericId = Number(id);

  if (!Number.isInteger(numericId) || numericId < 1) notFound();

  const episode = await getEpisodeSSR(numericId);
  if (!episode) notFound();

  return <EpisodeView episode={episode} />;
}
