import Link from "next/link";
import { eventPath } from "@/lib/discovery/entities";
import type { PublicEvent } from "@/lib/domain/types";

const STATUS_CLASS: Record<PublicEvent["status"], string> = {
  on_sale: "event-status--on-sale",
  sold_out: "event-status--sold-out",
  announced: "event-status--announced",
  completed: "event-status--completed",
};

export function EventCard({ event }: { event: PublicEvent }) {
  // Cards link to the permanent event page; ticketing lives there.
  const inner = (
    <>
      <div className="event-card-image-wrap">
        {event.imagePath ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={event.imagePath}
            alt={event.title}
            loading="lazy"
            className="event-card-image"
          />
        ) : null}
      </div>
      <div className="event-card-body">
        <div className={`event-status ${STATUS_CLASS[event.status]}`}>
          {event.badgeText}
        </div>
        <h2 className="event-card-title">{event.title}</h2>
        {event.dateLabel ? (
          <div className="event-card-meta">{event.dateLabel}</div>
        ) : null}
        {event.venueCity ? (
          <div className="event-card-sub">{event.venueCity}</div>
        ) : null}
      </div>
    </>
  );

  return (
    <Link href={eventPath(event)} className="event-card">
      {inner}
    </Link>
  );
}
