"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { CellTooltip } from "../ui/cell-tooltip";
import type { SalesEvent } from "./types";

function day(value: Date): string {
  return value.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "UTC",
  });
}

function shortDate(value: string): string {
  if (!value) return "";
  const datePart = value.includes("T") ? value.slice(0, 10) : value;
  const d = new Date(`${datePart}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "UTC",
  });
}

type PeriodKey = "today" | "yesterday" | "week" | "month";

const FIELD: Record<PeriodKey, keyof SalesEvent> = {
  today: "tickets_today",
  yesterday: "tickets_yesterday",
  week: "tickets_week",
  month: "tickets_month",
};

function periodTooltipContent(
  events: SalesEvent[],
  field: PeriodKey,
  eventHref: string,
  emptyMessage: string
) {
  const key = FIELD[field];
  const rows = [...events]
    .filter((e) => (e[key] as number) > 0)
    .sort((a, b) => (b[key] as number) - (a[key] as number))
    .slice(0, 20);

  if (rows.length === 0) {
    return <p className="text-muted-foreground">{emptyMessage}</p>;
  }

  const more =
    events.filter((e) => (e[key] as number) > 0).length - rows.length;
  return (
    <div className="space-y-1">
      {rows.map((event) => (
        <Link
          key={event.id}
          href={eventHref.replace("{id}", encodeURIComponent(event.id))}
          className="flex justify-between gap-3 text-foreground hover:underline"
          title={`${shortDate(event.date)} · ${event.brand_name} · ${event.venue}`}
        >
          <span className="truncate">
            {event.date ? `${shortDate(event.date)} · ` : ""}
            {event.brand_name && event.venue
              ? `${event.brand_name} · ${event.venue}`
              : event.brand_name || event.venue || "—"}
          </span>
          <span className="shrink-0 tabular-nums">
            {(event[key] as number).toLocaleString("fr-FR")}
          </span>
        </Link>
      ))}
      {more > 0 && (
        <p className="text-muted-foreground">
          + {more} autre{more > 1 ? "s" : ""} événement
          {more > 1 ? "s" : ""}
        </p>
      )}
    </div>
  );
}

export function PeriodCards({
  events,
  eventHref = "/events/{id}",
  footnote = "Ventes Shotgun enregistrées · journées UTC · tous types.",
}: {
  events: SalesEvent[];
  eventHref?: string;
  footnote?: string;
}) {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setUTCDate(today.getUTCDate() - 1);
  const weekStart = new Date(today);
  weekStart.setUTCDate(
    today.getUTCDate() - ((today.getUTCDay() + 6) % 7)
  );
  const monthStart = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)
  );

  const cards: {
    key: PeriodKey;
    label: string;
    detail: string;
    empty: string;
  }[] = [
    {
      key: "today",
      label: "Aujourd’hui",
      detail: `${day(today)} · depuis 00h UTC`,
      empty: "Aucune vente aujourd’hui.",
    },
    {
      key: "yesterday",
      label: "Hier",
      detail: day(yesterday),
      empty: "Aucune vente hier.",
    },
    {
      key: "week",
      label: "Cette semaine",
      detail: `Depuis lundi ${day(weekStart)}`,
      empty: "Aucune vente cette semaine.",
    },
    {
      key: "month",
      label: "Ce mois-ci",
      detail: `Depuis le ${day(monthStart)}`,
      empty: "Aucune vente ce mois-ci.",
    },
  ];

  return (
    <section aria-label="Billets vendus par période" className="space-y-2">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cards.map((card) => {
          const total = events.reduce(
            (acc, e) => acc + (e[FIELD[card.key]] as number),
            0
          );
          const cardContent = (
            <Card className="h-full">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  {card.label}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <p className="text-3xl font-semibold tabular-nums">
                  {total.toLocaleString("fr-FR")}
                </p>
                <p className="text-xs text-muted-foreground">billets Shotgun</p>
                <p className="text-xs text-muted-foreground">{card.detail}</p>
              </CardContent>
            </Card>
          );

          return (
            <CellTooltip
              key={card.key}
              className="block h-full outline-none"
              content={periodTooltipContent(
                events,
                card.key,
                eventHref,
                card.empty
              )}
              contentWidth={320}
            >
              {cardContent}
            </CellTooltip>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">{footnote}</p>
    </section>
  );
}
