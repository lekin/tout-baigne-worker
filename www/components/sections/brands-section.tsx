import type { PublicBrand } from "@/lib/domain/types";

export function BrandsSection({ brands }: { brands: PublicBrand[] }) {
  if (brands.length === 0) return null;
  return (
    <section className="brands-section pattern-bg">
      <div className="container-3">
        <h2 className="section-title brands-heading">
          Tout Baigne Production
          <br />
          présente
        </h2>
        <div className="brands-grid">
          {brands.map((brand) => {
            const href = brand.socials.website;
            const image = brand.cardImagePath ?? brand.logoPath;
            const card = (
              <>
                {image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={image}
                    alt={brand.name}
                    loading="lazy"
                    className="brand-card-image"
                  />
                ) : null}
                <div className="brand-card-name">{brand.name}</div>
                {brand.tagline ? (
                  <div className="brand-card-slogan">{brand.tagline}</div>
                ) : null}
              </>
            );
            return href ? (
              <a
                key={brand.id}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="brand-card"
              >
                {card}
              </a>
            ) : (
              <div key={brand.id} className="brand-card">
                {card}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
