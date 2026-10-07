import Link from "next/link";
import type { EntityRepresentation } from "@platform/discovery";
import { EntityFacts } from "@/components/entity-facts";
import type { PublicEvent } from "@/lib/domain/types";

const STATUS_LABEL: Record<PublicEvent["status"], string | undefined> = {
  on_sale: undefined,
  sold_out: "Sold out",
  announced: "Bientôt en vente",
  completed: "Événement terminé",
};

/** Permanent public event page — visible facts matching the JSON-LD graph. */
export function EventPage({
  event,
  rep,
}: {
  event: PublicEvent;
  rep: EntityRepresentation;
}) {
  const statusLabel = STATUS_LABEL[event.status];
  const ticketUrl = event.ticketUrl || event.facebookUrl;
  return (
    <main className="event-page">
      <div className="container-3">
        <nav className="breadcrumbs" aria-label="Fil d'Ariane">
          <Link href="/">Accueil</Link>
          <span aria-hidden="true"> / </span>
          <Link href="/events">Événements</Link>
          <span aria-hidden="true"> / </span>
          <span>{event.title}</span>
        </nav>

        <article className="event-page-card">
          {event.imagePath ? (
            <div className="event-page-image-wrap">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={event.imagePath}
                alt={event.title}
                className="event-page-image"
              />
            </div>
          ) : null}
          <div className="event-page-body">
            {event.badgeText || statusLabel ? (
              <div className={`event-status event-status--${event.status}`}>
                {event.badgeText || statusLabel}
              </div>
            ) : null}
            <h1 className="event-page-title">{event.title}</h1>
            {event.dateLabel ? (
              <p className="event-page-date">{event.dateLabel}</p>
            ) : null}
            {event.venueCity ? (
              <p className="event-page-venue">{event.venueCity}</p>
            ) : null}
            <div className="event-page-actions">
              {ticketUrl ? (
                <a
                  href={ticketUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="event-page-ticket"
                >
                  {event.status === "completed" ? "Voir l'événement" : "Billetterie"}
                </a>
              ) : null}
              {event.facebookUrl && event.ticketUrl ? (
                <a
                  href={event.facebookUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="event-page-facebook"
                >
                  Événement Facebook
                </a>
              ) : null}
            </div>
          </div>
        </article>
      </div>
      <EntityFacts rep={rep} title={event.title} />
    </main>
  );
}
