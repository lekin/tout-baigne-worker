import {
  fieldAsString,
  fieldAsStringArray,
  fieldAsAttachment,
  type AirtableRecord,
} from "@/lib/airtable/client";
import type { PublicEvent, PublicEventStatus } from "./types";

/**
 * Maps a raw `Events Websites Posts` record (event × site publication) to a
 * `PublicEvent`. Returns null when the post is not publishable on the site:
 * archived/draft Webflow state, or missing essentials. Finished events are
 * excluded unless `options.includePast` (permanent event pages keep
 * resolving after the event).
 */
export function mapPostToPublicEvent(
  record: AirtableRecord,
  options: { includePast?: boolean } = {}
): PublicEvent | null {
  const f = record.fields;

  if (truthy(f["Is Archived (Webflow item)"]) || truthy(f["Is Draft (Webflow item)"])) {
    return null;
  }

  const eventId = fieldAsStringArray(f["Event"])[0];
  const date = fieldAsString(f["Date (from Event)"]) ?? "";
  const badgeText = (fieldAsString(f["Webflow status (from Event)"]) ?? "").trim();
  const title = fieldAsString(f["Name (from Type) (from Event)"])?.trim() ?? "";

  if (!eventId || !date || !title || !badgeText) return null;
  if (badgeText === "TERMINÉ" && !options.includePast) return null;

  const eventDate = new Date(date);
  if (Number.isNaN(eventDate.getTime())) return null;
  // Keep events through their day (Paris evenings end after midnight UTC).
  if (!options.includePast && eventDate.getTime() < Date.now() - 24 * 60 * 60 * 1000) {
    return null;
  }
  const isPast = eventDate.getTime() < Date.now() - 24 * 60 * 60 * 1000;

  const venue = fieldAsString(f["Name (from Venue) (from Event)"]) ?? "";
  const city = fieldAsString(f["City (from Venue) (from Event)"]) ?? "";
  const venueCity = [venue, city].filter(Boolean).join(" · ");

  const hasImage =
    Boolean(fieldAsAttachment(f["Facebook covers (specific) (from Event)"])) ||
    Boolean(fieldAsAttachment(f["Feature (from Type) (from Event)"]));

  return {
    id: record.id,
    eventId,
    title,
    date,
    dateLabel: fieldAsString(f["Date (French) (from Event)"]) ?? "",
    venue,
    city,
    venueCity,
    badgeText,
    status:
      isPast || badgeText === "TERMINÉ"
        ? "completed"
        : normalizeStatus(badgeText, truthy(f["Sold-out (from Event)"])),
    imagePath: hasImage ? `/api/event-image/${record.id}` : undefined,
    ticketUrl:
      fieldAsString(f["Tickets sales URL (from Event)"]) ||
      fieldAsString(f["Tickets sales URL (from Event) 2"]),
    facebookUrl: fieldAsString(f["Facebook Event URL (from Event)"]),
  };
}

function truthy(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (Array.isArray(value)) return Boolean(value[0]);
  return Boolean(value);
}

/**
 * Normalizes the upstream badge text into a display status.
 * Known texts (from the `Webflow status` formula):
 *   "EN VENTE" · " Sold out" / "🎉 Sold out" variants · "🔥 Last chance" ·
 *   "🚀 Selling fast" · "🕒 WAITING LIST" · "EN VENTE À PARTIR DU …" ·
 *   "EN VENTE PROCHAINEMENT" · "TERMINÉ"
 */
export function normalizeStatus(badgeText: string, soldOutFlag = false): PublicEventStatus {
  const t = badgeText.toLowerCase();
  if (
    soldOutFlag ||
    t.includes("sold out") ||
    t.includes("sold-out") ||
    t.includes("complet") ||
    t.includes("waiting list")
  ) {
    return "sold_out";
  }
  if (t.includes("à partir") || t.includes("prochainement")) {
    return "announced";
  }
  return "on_sale";
}
