import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { mergeGraphs } from "@platform/discovery";
import { EventCard } from "@/components/event-card";
import { JsonLd } from "@/components/json-ld";
import { siteBySlug } from "@/lib/brands";
import { getSiteEntities } from "@/lib/discovery/data";
import {
  discovery,
  siteOrigin,
  webPageEntity,
} from "@/lib/discovery/entities";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ brand: string }>;
}): Promise<Metadata> {
  const { brand: slug } = await params;
  const site = siteBySlug(slug);
  if (!site) return {};
  const { webSite } = await getSiteEntities(site);
  const page = webPageEntity({
    site,
    webSite,
    path: "/events",
    title: `Événements — ${webSite.name}`,
    description: `Prochains événements ${webSite.name}.`,
    breadcrumbs: [
      { name: "Accueil", url: `${siteOrigin(site)}/` },
      { name: "Événements" },
    ],
  });
  return discovery.seo.nextMetadata(page, {
    metadataBase: siteOrigin(site),
  }) as Metadata;
}

export default async function EventsPage({
  params,
}: {
  params: Promise<{ brand: string }>;
}) {
  const { brand: slug } = await params;
  const site = siteBySlug(slug);
  if (!site) notFound();

  const eco = await getSiteEntities(site);
  const page = webPageEntity({
    site,
    webSite: eco.webSite,
    path: "/events",
    title: `Événements — ${eco.webSite.name}`,
    description: `Prochains événements ${eco.webSite.name}.`,
    breadcrumbs: [
      { name: "Accueil", url: `${siteOrigin(site)}/` },
      { name: "Événements" },
    ],
  });

  const itemList = {
    "@type": "ItemList" as const,
    "@id": `${siteOrigin(site)}/events#list`,
    itemListElement: eco.events.map((e, i) => ({
      "@type": "ListItem" as const,
      position: i + 1,
      url: e.url,
      name: e.name,
    })),
  };
  const graph = mergeGraphs(discovery.seo.schema(page), itemList);

  return (
    <>
      <JsonLd data={graph} />
      <main className="events-page">
        <div className="container-2">
          <nav className="breadcrumbs" aria-label="Fil d'Ariane">
            <Link href="/">Accueil</Link>
            <span aria-hidden="true"> / </span>
            <span>Événements</span>
          </nav>
          <h1 className="section-title events-page-title">
            Prochains événements
          </h1>
          {eco.publicEvents.length > 0 ? (
            <div className="events-grid">
              {eco.publicEvents.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <h3>Aucun événement en vente actuellement.</h3>
            </div>
          )}
        </div>
      </main>
    </>
  );
}
