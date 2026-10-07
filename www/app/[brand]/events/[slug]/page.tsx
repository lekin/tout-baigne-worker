import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { mergeGraphs } from "@platform/discovery";
import { EventPage } from "@/components/event-page";
import { JsonLd } from "@/components/json-ld";
import { siteBySlug } from "@/lib/brands";
import { getPublicEventByPostId } from "@/lib/data/public";
import { getEventEntities } from "@/lib/discovery/data";
import { formatDate } from "@platform/discovery";
import {
  discovery,
  eventPath,
  eventPostId,
  siteOrigin,
  TIMEZONE,
  webPageEntity,
} from "@/lib/discovery/entities";
import type { PublicEvent } from "@/lib/domain/types";

async function loadEvent(siteSlug: string, slug: string) {
  const site = siteBySlug(siteSlug);
  if (!site) return null;
  const postId = eventPostId(slug);
  if (!postId) return null;
  const event = await getPublicEventByPostId(postId, site);
  if (!event) return null;
  return { site, event };
}

function eventPageEntity(
  site: NonNullable<ReturnType<typeof siteBySlug>>,
  event: PublicEvent,
  webSite: Parameters<typeof webPageEntity>[0]["webSite"],
  about: Parameters<typeof webPageEntity>[0]["about"],
  description?: string
) {
  const origin = siteOrigin(site);
  const title = [
    event.title,
    event.venueCity,
    formatDate(event.date, "fr", TIMEZONE),
  ]
    .filter(Boolean)
    .join(" — ");
  return webPageEntity({
    site,
    webSite,
    path: eventPath(event),
    title,
    description,
    image: event.imagePath ? `${origin}${event.imagePath}` : undefined,
    about,
    breadcrumbs: [
      { name: "Accueil", url: `${origin}/` },
      { name: "Événements", url: `${origin}/events` },
      { name: event.title },
    ],
  });
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ brand: string; slug: string }>;
}): Promise<Metadata> {
  const { brand: siteSlug, slug } = await params;
  const loaded = await loadEvent(siteSlug, slug);
  if (!loaded) return {};
  const { site, event } = loaded;
  const eco = await getEventEntities(site, event);
  const rep = discovery.geo.representation(eco.event, {
    locale: "fr",
    ecosystem: eco.entities,
  });
  const page = eventPageEntity(
    site,
    event,
    eco.webSite,
    eco.event,
    rep.statements.slice(0, 3).join(" ")
  );
  return discovery.seo.nextMetadata(page, {
    metadataBase: siteOrigin(site),
  }) as Metadata;
}

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ brand: string; slug: string }>;
}) {
  const { brand: siteSlug, slug } = await params;
  const loaded = await loadEvent(siteSlug, slug);
  if (!loaded) notFound();
  const { site, event } = loaded;

  const eco = await getEventEntities(site, event);
  const rep = discovery.geo.representation(eco.event, {
    locale: "fr",
    ecosystem: eco.entities,
  });
  const page = eventPageEntity(
    site,
    event,
    eco.webSite,
    eco.event,
    rep.statements.slice(0, 3).join(" ")
  );
  const graph = mergeGraphs(
    discovery.seo.schema(page),
    discovery.seo.schema(eco.event)
  );

  return (
    <>
      <JsonLd data={graph} />
      <EventPage event={event} rep={rep} />
    </>
  );
}
