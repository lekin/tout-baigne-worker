import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Script from "next/script";
import { mergeGraphs } from "@platform/discovery";
import { BrandShell } from "@/components/brand-shell";
import { BrandSiteShell } from "@/components/brand-site-shell";
import { EntityFacts } from "@/components/entity-facts";
import { JsonLd } from "@/components/json-ld";
import { siteBySlug } from "@/lib/brands";
import { getBrandCards } from "@/lib/data/public";
import { getSiteEntities } from "@/lib/discovery/data";
import {
  discovery,
  siteOrigin,
  TBP_ORGANIZATION,
  webPageEntity,
} from "@/lib/discovery/entities";

async function homeEntity(site: NonNullable<ReturnType<typeof siteBySlug>>) {
  const eco = await getSiteEntities(site);
  const about = site.isGlobal ? TBP_ORGANIZATION : eco.brand;
  const page = webPageEntity({
    site,
    webSite: eco.webSite,
    path: "/",
    title:
      eco.publicBrand.seo.title ??
      `${eco.publicBrand.name} - soirées • parties • paris`,
    description:
      eco.publicBrand.seo.description ??
      eco.publicBrand.description ??
      eco.publicBrand.tagline,
    image:
      eco.publicBrand.featureImagePath ?? eco.publicBrand.logoPath
        ? `${siteOrigin(site)}${eco.publicBrand.featureImagePath ?? eco.publicBrand.logoPath}`
        : undefined,
    about,
  });
  return { eco, page };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ brand: string }>;
}): Promise<Metadata> {
  const { brand: slug } = await params;
  const site = siteBySlug(slug);
  if (!site) return {};
  const { page } = await homeEntity(site);
  return discovery.seo.nextMetadata(page, {
    metadataBase: siteOrigin(site),
    siteName: page.siteName,
    icons: site.theme
      ? { icon: site.theme.favicon, apple: site.theme.webclip }
      : undefined,
  }) as Metadata;
}

export default async function BrandPage({
  params,
}: {
  params: Promise<{ brand: string }>;
}) {
  const { brand: slug } = await params;
  const site = siteBySlug(slug);
  if (!site) notFound();

  const [{ eco, page }, brandCards] = await Promise.all([
    homeEntity(site),
    getBrandCards(site),
  ]);

  const graph = mergeGraphs(
    discovery.seo.schema(page),
    ...eco.entities.map((e) => discovery.seo.schema(e))
  );
  const rep = discovery.geo.representation(page.about!, {
    locale: "fr",
    ecosystem: eco.entities,
  });

  const ga4 = eco.publicBrand.seo.ga4;
  const facts = <EntityFacts rep={rep} title={page.about!.name} />;

  return (
    <>
      <JsonLd data={graph} />
      {ga4 ? (
        <>
          <Script
            src={`https://www.googletagmanager.com/gtag/js?id=${ga4}`}
            strategy="afterInteractive"
          />
          <Script id="ga4" strategy="afterInteractive">
            {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${ga4}');`}
          </Script>
        </>
      ) : null}
      {site.isGlobal ? (
        <BrandShell
          brand={eco.publicBrand}
          events={eco.publicEvents}
          brandCards={brandCards}
          isGlobal
          facts={facts}
        />
      ) : (
        <BrandSiteShell
          site={site}
          brand={eco.publicBrand}
          events={eco.publicEvents}
          brandCards={brandCards}
          facts={facts}
        />
      )}
    </>
  );
}
