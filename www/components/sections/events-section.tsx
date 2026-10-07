import { EventCard } from "@/components/event-card";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

export function EventsSection({
  events,
  brand,
}: {
  events: PublicEvent[];
  brand: PublicBrand;
}) {
  return (
    <section id="main" className="tickets">
      <div className="container-2">
        {/* Hidden on the live site (display:none in Webflow) — kept for a11y/SEO */}
        <h1 className="sr-only">PRÉVENTES</h1>
        {events.length > 0 ? (
          <div className="events-grid">
            {events.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <h3>Aucun événement en vente actuellement.</h3>
            <p>
              {brand.socials.instagram ? (
                <>
                  Suis-nous sur{" "}
                  <a
                    href={brand.socials.instagram}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <strong>Instagram</strong>
                  </a>{" "}
                  ou{" "}
                </>
              ) : null}
              abonne-toi à la newsletter ci-dessous pour être tenu informé de
              nos prochains événements !
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
