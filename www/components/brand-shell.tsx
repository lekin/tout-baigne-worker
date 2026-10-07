import { BookingSection } from "@/components/sections/booking-section";
import { BrandsSection } from "@/components/sections/brands-section";
import { EventsSection } from "@/components/sections/events-section";
import { HeroHeader } from "@/components/sections/hero-header";
import { NewsletterSection } from "@/components/sections/newsletter-section";
import { SiteFooter } from "@/components/sections/site-footer";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

/**
 * Composable brand page. Sections are independent and data-driven — new
 * content types (videos, radio, playlists…) become new sections later.
 */
export function BrandShell({
  brand,
  events,
  brandCards,
  isGlobal,
  facts,
}: {
  brand: PublicBrand;
  events: PublicEvent[];
  brandCards: PublicBrand[];
  isGlobal: boolean;
  /** Explicit machine-readable facts section (GEO entity home). */
  facts?: React.ReactNode;
}) {
  return (
    <>
      <HeroHeader brand={brand} />
      <EventsSection events={events} brand={brand} />
      <NewsletterSection brand={brand} siteSlug={brand.slug} isGlobal={isGlobal} />
      {isGlobal ? <BrandsSection brands={brandCards} /> : null}
      {facts}
      <BookingSection />
      <SiteFooter brand={brand} />
    </>
  );
}
