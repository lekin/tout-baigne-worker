import {
  fieldAsStringArray,
  getRecord,
  listTable,
} from "@/lib/airtable/client";
import {
  BRAND_GRID_SLUGS,
  GLOBAL_BRAND,
  siteBySlug,
  type SiteConfig,
} from "@/lib/brands";
import { mapPostToPublicEvent } from "@/lib/domain/map-event";
import { mapEventTypeToPublicBrand } from "@/lib/domain/map-brand";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

const REVALIDATE_SECONDS = 900; // 15 min — Airtable attachment URLs expire ~2h

const POST_FIELDS = [
  "Event",
  "Website",
  "Webflow status (from Event)",
  "Name (from Type) (from Event)",
  "Date (from Event)",
  "Date (French) (from Event)",
  "Name (from Venue) (from Event)",
  "City (from Venue) (from Event)",
  "Tickets sales URL (from Event)",
  "Tickets sales URL (from Event) 2",
  "Facebook Event URL (from Event)",
  "Sold-out (from Event)",
  "Facebook covers (specific) (from Event)",
  "Feature (from Type) (from Event)",
  "Is Draft (Webflow item)",
  "Is Archived (Webflow item)",
];

export async function getPublicBrand(site: SiteConfig): Promise<PublicBrand> {
  if (site.isGlobal || !site.eventTypeId) {
    return {
      id: site.slug,
      slug: site.slug,
      name: GLOBAL_BRAND.name,
      tagline: GLOBAL_BRAND.tagline,
      logoPath: GLOBAL_BRAND.logoPath,
      footerLogoPath: GLOBAL_BRAND.footerLogoPath,
      socials: { ...GLOBAL_BRAND.socials },
      seo: { ...GLOBAL_BRAND.seo },
    };
  }
  const record = await getRecord("Event types", site.eventTypeId, {
    revalidate: REVALIDATE_SECONDS,
    tags: [`brand:${site.slug}`, "airtable"],
  });
  if (!record) {
    return {
      id: site.eventTypeId,
      slug: site.slug,
      name: site.slug,
      socials: {},
      seo: {},
    };
  }
  return mapEventTypeToPublicBrand(record, site.slug);
}

/**
 * Events shown on a site = published `Events Websites Posts` for that site's
 * `Websites` record, upcoming and not archived/draft.
 */
export async function getPublicEvents(site: SiteConfig): Promise<PublicEvent[]> {
  const website = await getRecord("Websites", site.websiteId, {
    revalidate: REVALIDATE_SECONDS,
    tags: [`site:${site.slug}`, "airtable"],
  });
  const websiteName = website
    ? (website.fields.Name as string | undefined)
    : undefined;
  if (!websiteName) return [];

  const posts = await listTable("Events Websites Posts", {
    filterByFormula: `AND(FIND(${formulaString(websiteName)}, {Website}&""), NOT({Is Archived (Webflow item)}), NOT({Is Draft (Webflow item)}))`,
    fields: POST_FIELDS,
    revalidate: REVALIDATE_SECONDS,
    tags: [`site:${site.slug}:events`, "airtable"],
  });

  return posts
    .map((p) => mapPostToPublicEvent(p))
    .filter((e): e is PublicEvent => Boolean(e))
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
}

/**
 * Single event by `Events Websites Posts` id — for permanent /events/[slug]
 * pages. Past (finished) events still resolve; archived/draft posts do not.
 * When `site` is given, the post must be published on that site.
 */
export async function getPublicEventByPostId(
  postId: string,
  site?: SiteConfig
): Promise<PublicEvent | null> {
  const post = await getRecord("Events Websites Posts", postId, {
    revalidate: REVALIDATE_SECONDS,
    tags: ["airtable", `post:${postId}`],
  });
  if (!post) return null;
  if (
    site &&
    !fieldAsStringArray(post.fields["Website"]).includes(site.websiteId)
  ) {
    return null;
  }
  return mapPostToPublicEvent(post, { includePast: true });
}

/** All brand entities' public data, for cross-brand event resolution. */
export async function getAllPublicBrands(
  sites: SiteConfig[]
): Promise<Map<string, PublicBrand>> {
  const entries = await Promise.all(
    sites.map(async (site) => [site.slug, await getPublicBrand(site)] as const)
  );
  return new Map(entries);
}

/** Brand cards for the "présente" section (shown on every site). */
export async function getBrandCards(site: SiteConfig): Promise<PublicBrand[]> {
  const slugs = site.brandSiteSlugs ?? BRAND_GRID_SLUGS;
  const brands = await Promise.all(
    slugs.map(async (slug) => {
      const s = siteBySlug(slug);
      if (!s) return null;
      const brand = await getPublicBrand(s);
      // Card links point at our own sites, not the (possibly stale)
      // `Website URL` field in Airtable.
      const card: PublicBrand = {
        ...brand,
        cardImagePath: s.cardImage ?? brand.logoPath,
        socials: { ...brand.socials, website: `https://${s.domains[0]}` },
      };
      return card;
    })
  );
  return brands.filter((b): b is PublicBrand => Boolean(b));
}

/** Fresh image URL for an event card (specific cover → brand feature fallback). */
export async function resolveEventImageUrl(
  postId: string
): Promise<string | undefined> {
  const post = await getRecord("Events Websites Posts", postId, {
    revalidate: 300,
    tags: ["airtable"],
  });
  if (!post) return undefined;
  const cover = firstAttachmentUrl(
    post.fields["Facebook covers (specific) (from Event)"]
  );
  return (
    cover ?? firstAttachmentUrl(post.fields["Feature (from Type) (from Event)"])
  );
}

/** Fresh brand image URL (kind: logo | feature). */
export async function resolveBrandImageUrl(
  typeId: string,
  kind: "logo" | "feature"
): Promise<string | undefined> {
  const record = await getRecord("Event types", typeId, {
    revalidate: 300,
    tags: ["airtable"],
  });
  if (!record) return undefined;
  const fields =
    kind === "logo"
      ? ["Logo", "Square (1:1)", "Linktree thumbnail", "Dice image", "Feature"]
      : ["Feature"];
  for (const field of fields) {
    const url = firstAttachmentUrl(record.fields[field]);
    if (url) return url;
  }
  return undefined;
}

function firstAttachmentUrl(value: unknown): string | undefined {
  const first = Array.isArray(value) ? value[0] : value;
  if (first && typeof first === "object") {
    const att = first as { url?: string };
    return att.url;
  }
  if (typeof first === "string" && first.startsWith("http")) return first;
  return undefined;
}

function formulaString(value: string): string {
  const escaped = value
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t");
  return `"${escaped}"`;
}
