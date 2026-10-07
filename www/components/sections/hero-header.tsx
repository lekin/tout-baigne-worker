import Link from "next/link";
import { EmailIcon, FacebookIcon, InstagramIcon } from "@/components/icons";
import type { PublicBrand } from "@/lib/domain/types";

export function SocialLinks({
  socials,
}: {
  socials: PublicBrand["socials"];
}) {
  const items: { href: string; label: string; icon: React.ReactNode }[] = [];
  if (socials.instagram) {
    items.push({
      href: socials.instagram,
      label: "Instagram",
      icon: <InstagramIcon />,
    });
  }
  if (socials.facebook) {
    items.push({
      href: socials.facebook,
      label: "Facebook",
      icon: <FacebookIcon />,
    });
  }
  if (socials.email) {
    items.push({
      href: `mailto:${socials.email}`,
      label: "Email",
      icon: <EmailIcon />,
    });
  }
  return (
    <div className="socials">
      {items.map((item) => (
        <a
          key={item.label}
          href={item.href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={item.label}
          className="social-link"
        >
          {item.icon}
        </a>
      ))}
    </div>
  );
}

export function HeroHeader({ brand }: { brand: PublicBrand }) {
  // Header socials are hidden on the live site (display:none in Webflow).
  return (
    <header className="header">
      <div className="site-logo">
        <Link href="/">
          {brand.logoPath ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={brand.logoPath} alt={brand.name} loading="eager" />
          ) : (
            <h1 className="section-title" style={{ fontSize: 34 }}>
              {brand.name}
            </h1>
          )}
        </Link>
        {brand.tagline ? <p className="site-tagline">{brand.tagline}</p> : null}
      </div>
    </header>
  );
}
