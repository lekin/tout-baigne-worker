import { SocialLinks } from "@/components/sections/hero-header";
import type { PublicBrand } from "@/lib/domain/types";

export function SiteFooter({ brand }: { brand: PublicBrand }) {
  const logo = brand.footerLogoPath ?? brand.logoPath;
  return (
    <footer className="site-footer pattern-bg">
      <div
        className="container-3"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
        }}
      >
        {logo ? (
          <a
            href="https://www.toutbaigneproduction.com"
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: "block" }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={logo}
              alt={brand.name}
              loading="lazy"
              className="footer-logo"
            />
          </a>
        ) : (
          <strong className="section-title" style={{ fontSize: 18 }}>
            {brand.name}
          </strong>
        )}
        {/* Live site hides Instagram in the footer (`.social-item.instagram` display:none) */}
        <SocialLinks socials={{ email: brand.socials.email ?? "yo@toutbaigneproduction.com" }} />
        <ul className="footer-legal">
          <li>
            <a href="/politique-de-confidentialite" target="_blank">
              Politique de confidentialité
            </a>
          </li>
        </ul>
      </div>
    </footer>
  );
}
