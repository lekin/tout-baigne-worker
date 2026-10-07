/**
 * TBP adapter — data side: assembles the discovery entity ecosystem from the
 * canonical public data (Airtable → domain types → entities). No rendering.
 */

import { SITES, type SiteConfig } from "@/lib/brands";
import {
  getAllPublicBrands,
  getPublicBrand,
  getPublicEvents,
} from "@/lib/data/public";
import type { PublicEvent } from "@/lib/domain/types";
import {
  brandEntity,
  eventBrand,
  eventEntity,
  siteEcosystem,
  TBP_ORGANIZATION,
  webSiteEntity,
} from "@/lib/discovery/entities";

const BRAND_SITES = SITES.filter((s) => !s.isGlobal);

/**
 * Brand entities for every brand site — needed so events listed on the
 * global site (or anywhere) resolve to their real brand.
 */
export async function getAllBrandEntities() {
  const brandsBySlug = await getAllPublicBrands(BRAND_SITES);
  return BRAND_SITES.map((site) => {
    const brand = brandsBySlug.get(site.slug);
    return brand ? brandEntity(site, brand) : undefined;
  }).filter((b): b is NonNullable<typeof b> => Boolean(b));
}

/** Full ecosystem for a site: org → website → brand(s) → events. */
export async function getSiteEntities(site: SiteConfig) {
  const [publicBrand, publicEvents, allBrands] = await Promise.all([
    getPublicBrand(site),
    getPublicEvents(site),
    getAllBrandEntities(),
  ]);
  return {
    publicBrand,
    publicEvents,
    ...siteEcosystem({ site, brand: publicBrand, events: publicEvents, allBrands }),
  };
}

/**
 * Entities for a single event page — resolves the event's real brand across
 * all sites without loading the whole events list.
 */
export async function getEventEntities(site: SiteConfig, event: PublicEvent) {
  const [publicBrand, allBrands] = await Promise.all([
    getPublicBrand(site),
    getAllBrandEntities(),
  ]);
  const webSite = webSiteEntity(site, publicBrand);
  const ownBrand = site.isGlobal ? undefined : brandEntity(site, publicBrand);
  const brand = eventBrand(event, allBrands) ?? ownBrand;
  const event_ = eventEntity(site, event, brand);
  const entities = [
    TBP_ORGANIZATION,
    webSite,
    ...(brand ? [brand] : []),
    event_,
  ];
  return { publicBrand, webSite, brand, event: event_, entities };
}
