import type { MetadataRoute } from "next";
import { headers } from "next/headers";
import { siteByHost, DEFAULT_SITE } from "@/lib/brands";
import { getPublicEvents } from "@/lib/data/public";
import { discovery, eventPath, siteOrigin } from "@/lib/discovery/entities";

/**
 * Host-aware sitemap: always emitted on the canonical domain, never exposes
 * internal /[brand]/ paths. Uses `headers()` → rendered per request host.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const host = (await headers()).get("host") ?? "";
  const site = siteByHost(host) ?? DEFAULT_SITE;
  const origin = siteOrigin(site);
  const events = await getPublicEvents(site);

  return discovery.seo.toNextSitemap([
    { url: `${origin}/`, changeFrequency: "daily", priority: 1 },
    { url: `${origin}/events`, changeFrequency: "daily", priority: 0.9 },
    ...events.map((e) => ({
      url: `${origin}${eventPath(e)}`,
      changeFrequency: "daily" as const,
      priority: 0.8,
    })),
    {
      url: `${origin}/politique-de-confidentialite`,
      changeFrequency: "yearly",
      priority: 0.1,
    },
  ]) as MetadataRoute.Sitemap;
}
