export type PublicEventStatus = "on_sale" | "sold_out" | "announced" | "completed";

export interface PublicEvent {
  /** `Events Websites Posts` record id — the (event × site) publication. */
  id: string;
  /** `Events` record id. */
  eventId: string;
  title: string;
  /** ISO datetime of the event (source: `Date`). */
  date: string;
  /** Preformatted French label, e.g. "SAMEDI 26 SEPTEMBRE". */
  dateLabel: string;
  venue: string;
  city: string;
  /** "Venue · City" display label. */
  venueCity: string;
  /** Raw badge text as computed upstream, e.g. "EN VENTE", " Sold out", "EN VENTE PROCHAINEMENT". */
  badgeText: string;
  status: PublicEventStatus;
  /** Internal path resolving to a fresh image URL, e.g. `/api/event-image/recXXX`. */
  imagePath?: string;
  ticketUrl?: string;
  facebookUrl?: string;
}

export interface PublicBrand {
  id: string;
  slug: string;
  name: string;
  tagline?: string;
  description?: string;
  /** Internal path resolving to a fresh logo URL. */
  logoPath?: string;
  /** Internal path resolving to a fresh feature image URL. */
  featureImagePath?: string;
  /** Card image for the global "présente" grid (static export asset). */
  cardImagePath?: string;
  /** Footer logo (white variant for the global site). */
  footerLogoPath?: string;
  socials: {
    instagram?: string;
    facebook?: string;
    linktree?: string;
    website?: string;
    email?: string;
  };
  seo: {
    title?: string;
    description?: string;
    ga4?: string;
  };
}
