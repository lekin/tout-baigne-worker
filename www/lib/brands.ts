/**
 * Technical site registry — the ONLY place domains map to brands.
 * Editorial content (tagline, socials, SEO, images) lives in Airtable
 * (`Event types` records); this file only holds routing + record ids.
 *
 * `websiteId`  = `Websites` record → publication source (`Events Websites Posts`)
 * `eventTypeId` = `Event types` record → brand content (brand sites only)
 */
/**
 * Visual theme of a brand site, ported from its exported Webflow stylesheet.
 * Static assets live under `public/images/brands/<slug>/`.
 */
export interface BrandTheme {
  /** CSS scope class applied to the page wrapper, e.g. `site-chronologic`. */
  className: string;
  logo: string;
  favicon: string;
  webclip: string;
  /** Tagline under the logo (hidden on Chronologic). */
  taglineHidden?: boolean;
  /** Airtable `Description` intro block (hidden on We Are The 90's). */
  introHidden?: boolean;
  /** Newsletter section (absent on Chronologic). */
  newsletter: boolean;
  /** YouTube teaser embed id (Chronologic only). */
  teaserYoutubeId?: string;
  /** Event grid columns on desktop: 2 for Chrono/WAT90s, 1 for La Bug. */
  eventsCols: 1 | 2;
}

export interface SiteConfig {
  /** Internal route segment — never appears in public URLs. */
  slug: string;
  /** Public hostnames served by this site (first = canonical). */
  domains: string[];
  /** The global umbrella site shows all events + the brand grid. */
  isGlobal: boolean;
  websiteId: string;
  eventTypeId?: string;
  /** Brand slugs shown in the "présente" grid. */
  brandSiteSlugs?: string[];
  /** Static card image from the Webflow export (brand sites only). */
  cardImage?: string;
  /** Brand-site theme (absent on the global site). */
  theme?: BrandTheme;
}

/** Brand slugs shown in the "présente" grid on every site. */
export const BRAND_GRID_SLUGS = [
  "we-are-the-90s",
  "chronologic",
  "la-bug-de-lan-2000",
];

export const SITES: SiteConfig[] = [
  {
    slug: "tout-baigne",
    domains: [
      "www.toutbaigneproduction.com",
      "toutbaigneproduction.com",
      "localhost",
      "127.0.0.1",
      "0.0.0.0",
    ],
    isGlobal: true,
    websiteId: "recO62Zhs9JEI8bJa",
    brandSiteSlugs: BRAND_GRID_SLUGS,
  },
  {
    slug: "we-are-the-90s",
    domains: ["www.wearethe90s.com", "wearethe90s.com"],
    isGlobal: false,
    websiteId: "reciKHAhqZZuoUda4",
    eventTypeId: "reccm8TGn7aLyTWK2",
    cardImage: "/images/brands/studio-image-77.jpg",
    theme: {
      className: "site-we-are-the-90s",
      logo: "/images/brands/we-are-the-90s/logo.png",
      favicon: "/images/brands/we-are-the-90s/favicon.gif",
      webclip: "/images/brands/we-are-the-90s/webclip.gif",
      introHidden: true,
      newsletter: true,
      eventsCols: 2,
    },
  },
  {
    slug: "chronologic",
    domains: ["www.chronologic-soiree.com", "chronologic-soiree.com"],
    isGlobal: false,
    websiteId: "recgbuvJwVsm77wnv",
    eventTypeId: "recK5YvAwttrG5eaO",
    cardImage: "/images/brands/studio-image-73.jpg",
    theme: {
      className: "site-chronologic",
      logo: "/images/brands/chronologic/logo.png",
      favicon: "/images/brands/chronologic/favicon.gif",
      webclip: "/images/brands/chronologic/webclip.gif",
      taglineHidden: true,
      newsletter: false,
      teaserYoutubeId: "KgrrDa_UyDw",
      eventsCols: 2,
    },
  },
  {
    slug: "la-bug-de-lan-2000",
    domains: [
      "www.labugdelan2000.com",
      "labugdelan2000.com",
      "la-bug-de-lan-2000.webflow.io",
    ],
    isGlobal: false,
    websiteId: "reccj2I2s3Xu0sRci",
    eventTypeId: "recI63LCiWYhkBsRn",
    cardImage: "/images/brands/logo-square.jpg",
    theme: {
      className: "site-la-bug-de-lan-2000",
      logo: "/images/brands/la-bug-de-lan-2000/logo.png",
      favicon: "/images/brands/la-bug-de-lan-2000/favicon.gif",
      webclip: "/images/brands/la-bug-de-lan-2000/webclip.gif",
      newsletter: true,
      eventsCols: 1,
    },
  },
];

export const DEFAULT_SITE = SITES[0];

export function siteBySlug(slug: string | undefined | null): SiteConfig | undefined {
  if (!slug) return undefined;
  return SITES.find((s) => s.slug === slug);
}

export function siteByHost(host: string | undefined | null): SiteConfig | undefined {
  if (!host) return undefined;
  const hostname = host.split(":")[0].toLowerCase();
  // `*.localhost` subdomains resolve a brand locally, e.g. chronologic.localhost
  const sub = hostname.endsWith(".localhost") ? hostname.split(".")[0] : null;
  return (
    SITES.find((s) => s.domains.includes(hostname)) ??
    (sub ? SITES.find((s) => s.slug === sub || s.slug.startsWith(sub)) : undefined)
  );
}

/**
 * Identity of the global site — there is no `Event types` record for the
 * umbrella brand, so its editorial identity is a technical default here.
 */
export const GLOBAL_BRAND = {
  name: "Tout Baigne Production",
  tagline: "Viens la fête !\nOn sera bien tous les deux.",
  logoPath: "/images/logo_toutbaigneprod_colors.png",
  footerLogoPath: "/images/logo_toutbaigneprod-white.png",
  socials: {
    instagram: "https://www.instagram.com/toutbaigneproduction/",
    email: "yo@toutbaigneproduction.com",
  },
  seo: {
    title: "Tout Baigne Production - soirées • parties • paris",
    description: "Tout Baigne Production - soirées • parties • paris",
  },
};

/** Newsletter opt-in checkboxes shown on the global site (brand slugs). */
export const NEWSLETTER_BRAND_CHOICES = [
  { slug: "tout-baigne", label: "Tout Baigne Production", highlighted: true },
  { slug: "chronologic", label: "Chronologic" },
  { slug: "we-are-the-90s", label: "We Are The 90's" },
  { slug: "la-bug-de-lan-2000", label: "La Bug de l'An 2000" },
];
