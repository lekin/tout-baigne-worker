/**
 * TBP adapter for @platform/discovery.
 *
 * This is the ONLY place TBP knowledge enters Discovery: the canonical
 * organization, the host→brand mapping, and the domain→entity projections.
 * The package itself never knows these domains or facts.
 *
 * Pure mapping — no rendering logic.
 */

import {
  createDiscovery,
  slugify,
  type BrandEntity,
  type DiscoveryEntity,
  type EventEntity,
  type Offer,
  type OrganizationEntity,
  type VenueEntity,
  type WebPageEntity,
  type WebSiteEntity,
} from "@platform/discovery";
import { DEFAULT_SITE, GLOBAL_BRAND, type SiteConfig } from "@/lib/brands";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

export const discovery = createDiscovery();

/** Canonical public origin of a site (first configured domain). */
export function siteOrigin(site: SiteConfig): string {
  return `https://${site.domains[0]}`;
}

export const TIMEZONE = "Europe/Paris";
export const LOCALE = "fr";

/** Editorial entity category per brand site (TBP-owned fact). */
const BRAND_CATEGORY: Record<string, string> = {
  "we-are-the-90s": "une soirée",
  chronologic: "une soirée",
  "la-bug-de-lan-2000": "une soirée",
};

/**
 * The canonical Tout Baigne Production organization — declared once, so every
 * hostname expresses the same "who organizes what" graph.
 * Facts come from the site's own legal page and the GLOBAL_BRAND registry.
 */
export const TBP_ORGANIZATION: OrganizationEntity = discovery.organization({
  id: "tout-baigne-production",
  name: "Tout Baigne Production",
  legalName: "SARL Tout Baigne Production",
  url: siteOrigin(DEFAULT_SITE),
  description:
    "Société de production de soirées et d'événements basée à Paris.",
  email: GLOBAL_BRAND.socials.email,
  sameAs: [GLOBAL_BRAND.socials.instagram].filter(Boolean) as string[],
  address: { addressLocality: "Paris", addressCountry: "FR" },
  category: "une société de production de soirées",
});

/** Brand entity for a brand site (the global umbrella is the org itself). */
export function brandEntity(site: SiteConfig, brand: PublicBrand): BrandEntity {
  const origin = siteOrigin(site);
  return discovery.brand({
    id: `brand:${site.slug}`,
    name: brand.name,
    slug: site.slug,
    url: origin,
    description: brand.description,
    tagline: brand.tagline,
    image: brand.featureImagePath ? `${origin}${brand.featureImagePath}` : undefined,
    logo: brand.logoPath ? `${origin}${brand.logoPath}` : undefined,
    category: BRAND_CATEGORY[site.slug] ?? "un format événementiel",
    sameAs: Object.values(brand.socials).filter(
      (v): v is string => Boolean(v)
    ),
    organizer: TBP_ORGANIZATION,
    schemaType: site.isGlobal ? "Organization" : "EventSeries",
  });
}

/**
 * Resolve which known brand an event belongs to, by matching the event's
 * type name (`title`) against the brands' canonical names. Returns undefined
 * when no brand matches — the event then only relates to its organizer.
 */
export function eventBrand(
  event: PublicEvent,
  brands: BrandEntity[]
): BrandEntity | undefined {
  const title = slugify(event.title);
  return (
    brands.find((b) => slugify(b.name) === title) ??
    brands.find(
      (b) =>
        title && (slugify(b.name).includes(title) || title.includes(slugify(b.name)))
    )
  );
}

/**
 * Permanent public path of an event: `<title>-<date>-<venue>-<postId>`.
 * The trailing post id (Airtable `rec…`) keeps the URL resolvable forever,
 * even if title/date/venue are edited later.
 */
export function eventSlug(event: PublicEvent): string {
  const parts = [
    slugify(event.title),
    event.date.slice(0, 10),
    slugify(event.venue || "evenement"),
  ].filter(Boolean);
  return `${parts.join("-")}-${event.id}`;
}

/** Extract the post id (`rec…`) from an event slug. */
export function eventPostId(slug: string): string | undefined {
  return /(rec[A-Za-z0-9]{10,})/.exec(slug)?.[1];
}

export function eventPath(event: PublicEvent): string {
  return `/events/${eventSlug(event)}`;
}

function eventOffers(event: PublicEvent): Offer[] {
  const availability: Offer["availability"] =
    event.status === "sold_out"
      ? "sold_out"
      : event.status === "announced"
        ? "pre_order"
        : "in_stock";
  return event.ticketUrl ? [{ url: event.ticketUrl, availability }] : [];
}

/** Event entity — canonical public representation of a PublicEvent. */
export function eventEntity(
  site: SiteConfig,
  event: PublicEvent,
  brand: BrandEntity | undefined
): EventEntity {
  const origin = siteOrigin(site);
  const venue: VenueEntity | undefined = event.venue
    ? discovery.venue({
        name: event.venue,
        city: event.city || undefined,
        country: "FR",
      })
    : undefined;
  return discovery.event({
    id: `event:${event.id}`,
    name: event.title,
    url: `${origin}${eventPath(event)}`,
    startDate: event.date,
    timezone: TIMEZONE,
    status: event.status === "completed" ? "completed" : "scheduled",
    attendanceMode: "offline",
    description: event.title !== brand?.name ? brand?.description : undefined,
    image: event.imagePath ? `${origin}${event.imagePath}` : undefined,
    venue,
    brand,
    organizer: TBP_ORGANIZATION,
    ticketUrl: event.ticketUrl,
    offers: eventOffers(event),
  });
}

/** WebSite entity for a host. */
export function webSiteEntity(
  site: SiteConfig,
  brand: PublicBrand
): WebSiteEntity {
  return discovery.webSite({
    id: `website:${site.slug}`,
    name: brand.name,
    url: siteOrigin(site),
    description: brand.description,
    publisher: TBP_ORGANIZATION,
    locale: "fr-FR",
  });
}

/** WebPage entity — a public page (entity home or auxiliary page). */
export function webPageEntity(input: {
  site: SiteConfig;
  webSite: WebSiteEntity;
  path: string;
  title: string;
  description?: string;
  image?: string;
  about?: DiscoveryEntity;
  breadcrumbs?: { name: string; url?: string }[];
  noindex?: boolean;
}): WebPageEntity {
  const origin = siteOrigin(input.site);
  return discovery.webPage({
    id: `page:${input.site.slug}${input.path}`,
    title: input.title,
    url: `${origin}${input.path}`,
    description: input.description,
    image: input.image,
    about: input.about,
    isPartOf: input.webSite,
    breadcrumbs: input.breadcrumbs,
    noindex: input.noindex,
    locale: "fr-FR",
    siteName: input.webSite.name,
  });
}

/**
 * Full entity ecosystem for a site: organization → website → brand → events.
 * `allBrands` (every brand entity across sites) lets events on the global
 * site resolve to their real brand. Used for JSON-LD graphs, GEO
 * representations and the answer engine.
 */
export function siteEcosystem(input: {
  site: SiteConfig;
  brand: PublicBrand;
  events: PublicEvent[];
  allBrands?: BrandEntity[];
}): {
  organization: OrganizationEntity;
  webSite: WebSiteEntity;
  brand: BrandEntity;
  events: EventEntity[];
  entities: DiscoveryEntity[];
} {
  const brand = brandEntity(input.site, input.brand);
  const webSite = webSiteEntity(input.site, input.brand);
  const allBrands = input.allBrands ?? [brand];
  const events = input.events.map((e) =>
    eventEntity(input.site, e, eventBrand(e, allBrands) ?? (input.site.isGlobal ? undefined : brand))
  );
  const brandNodes = input.site.isGlobal ? allBrands : [brand];
  return {
    organization: TBP_ORGANIZATION,
    webSite,
    brand,
    events,
    entities: [TBP_ORGANIZATION, webSite, ...brandNodes, ...events],
  };
}
