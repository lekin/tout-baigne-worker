import {
  fieldAsString,
  fieldAsAttachment,
  type AirtableRecord,
} from "@/lib/airtable/client";
import type { PublicBrand } from "./types";

const LOGO_FIELDS = ["Logo", "Square (1:1)", "Linktree thumbnail", "Dice image", "Feature"];

/**
 * Maps a raw `Event types` record to a `PublicBrand`.
 */
export function mapEventTypeToPublicBrand(
  record: AirtableRecord,
  slug: string
): PublicBrand {
  const f = record.fields;

  const hasLogo = LOGO_FIELDS.some((field) => fieldAsAttachment(f[field]));
  const hasFeature = Boolean(fieldAsAttachment(f["Feature"]));

  return {
    id: record.id,
    slug,
    name: fieldAsString(f["Name"]) ?? "",
    tagline: fieldAsString(f["Slogan"])?.trim(),
    description: fieldAsString(f["Description"])?.replace(/ /g, " "),
    logoPath: hasLogo ? `/api/brand-image/${record.id}?kind=logo` : undefined,
    featureImagePath: hasFeature
      ? `/api/brand-image/${record.id}?kind=feature`
      : undefined,
    socials: {
      instagram: fieldAsString(f["Instagram URL"]),
      facebook: fieldAsString(f["Facebook URL"]),
      linktree: fieldAsString(f["Linktree"]),
      website: fieldAsString(f["Website URL"]),
    },
    seo: {
      title: fieldAsString(f["SEO - Title tag"]),
      description: fieldAsString(f["SEO - Meta description"]),
      ga4: fieldAsString(f["GA4 - Measurement ID"]),
    },
  };
}
