import Link from "next/link";
import { SocialLinks } from "@/components/sections/hero-header";
import { BrandsSection } from "@/components/sections/brands-section";
import { EventCard } from "@/components/event-card";
import { IntroSection } from "@/components/sections/intro-section";
import { NewsletterSection } from "@/components/sections/newsletter-section";
import { TeaserSection } from "@/components/sections/teaser-section";
import type { SiteConfig } from "@/lib/brands";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

/**
 * Brand-site layout ported from each exported Webflow site (Chronologic,
 * We Are The 90's, La Bug de l'An 2000). Scoped under `site-<slug>`.
 */
export function BrandSiteShell({
  site,
  brand,
  events,
  brandCards,
  facts,
}: {
  site: SiteConfig;
  brand: PublicBrand;
  events: PublicEvent[];
  brandCards: PublicBrand[];
  /** Explicit machine-readable facts section (GEO entity home). */
  facts?: React.ReactNode;
}) {
  const theme = site.theme;
  if (!theme) return null;

  const socials = {
    ...brand.socials,
    email: brand.socials.email ?? "yo@toutbaigneproduction.com",
  };

  return (
    <div className={`brand-site ${theme.className}`}>
      <header className="header">
        <div className="brand-header-inner">
          <div className="brand-header-socials">
            <SocialLinks socials={socials} />
          </div>
          <div className="brand-logo">
            <Link href="/">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={theme.logo} alt={brand.name} />
            </Link>
            {brand.tagline && !theme.taglineHidden ? (
              <p className="brand-tagline">{brand.tagline}</p>
            ) : null}
          </div>
        </div>
      </header>

      {theme.introHidden ? null : <IntroSection brand={brand} />}

      {facts}

      <section id="main" className="tickets">
        <div className="brand-tickets-inner">
          <h1 className="sr-only">Préventes</h1>
          {events.length > 0 ? (
            <div
              className={`events-grid events-grid--${theme.eventsCols}col`}
            >
              {events.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <h3>Aucun événement en vente actuellement.</h3>
              <p>
                Suis-nous sur les réseaux ou abonne-toi à la newsletter pour
                être tenu informé de nos prochains événements !
              </p>
            </div>
          )}
        </div>
      </section>

      {theme.newsletter ? (
        <NewsletterSection brand={brand} siteSlug={site.slug} isGlobal={false} />
      ) : null}

      {theme.teaserYoutubeId ? (
        <TeaserSection youtubeId={theme.teaserYoutubeId} />
      ) : null}

      <BrandsSection brands={brandCards} />

      <footer className="site-footer pattern-bg">
        <div className="container-3 brand-footer-inner">
          <a
            href="https://www.toutbaigneproduction.com"
            target="_blank"
            rel="noopener noreferrer"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/images/logo_toutbaigneprod-white.png"
              alt="Tout Baigne Production"
              loading="lazy"
              className="footer-logo"
            />
          </a>
          <SocialLinks socials={socials} />
          <ul className="footer-legal">
            <li>
              <a href="/politique-de-confidentialite" target="_blank">
                Politique de confidentialité
              </a>
            </li>
          </ul>
        </div>
      </footer>
    </div>
  );
}
