import type { MetadataRoute } from "next";
import { headers } from "next/headers";
import { siteByHost, DEFAULT_SITE } from "@/lib/brands";
import { discovery, siteOrigin } from "@/lib/discovery/entities";

export default async function robots(): Promise<MetadataRoute.Robots> {
  const host = (await headers()).get("host") ?? "";
  const site = siteByHost(host) ?? DEFAULT_SITE;
  const origin = siteOrigin(site);

  return discovery.seo.toNextRobots(
    discovery.seo.robots({
      disallow: ["/api/"],
      sitemap: `${origin}/sitemap.xml`,
    })
  ) as MetadataRoute.Robots;
}
