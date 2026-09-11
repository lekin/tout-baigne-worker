import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import type { SalesEvent } from "./types";

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function shortDay(value: string): string {
  return new Date(`${value}T12:00:00Z`).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "UTC",
  });
}

type PeriodKey = "today" | "yesterday" | "week" | "month";

// Period totals across a set of events — same cards as the Admin dashboard
// (Aujourd'hui / Hier / Cette semaine / Ce mois-ci, UTC days).
export function PeriodCards({
  events,
  footnote = "Ventes Shotgun enregistrées · journées UTC",
}: {
  events: SalesEvent[];
  footnote?: string;
}) {
  const now = new Date();
  const today = isoDay(now);
  const yesterdayDate = new Date(`${today}T00:00:00Z`);
  yesterdayDate.setUTCDate(yesterdayDate.getUTCDate() - 1);
  const yesterday = isoDay(yesterdayDate);
  const weekStartDate = new Date(`${today}T00:00:00Z`);
  weekStartDate.setUTCDate(
    weekStartDate.getUTCDate() - ((weekStartDate.getUTCDay() + 6) % 7)
  );
  const weekStart = isoDay(weekStartDate);
  const monthStart = `${today.slice(0, 7)}-01`;

  const totals: Record<PeriodKey, number> = {
    today: events.reduce((s, e) => s + e.tickets_today, 0),
    yesterday: events.reduce((s, e) => s + e.tickets_yesterday, 0),
    week: events.reduce((s, e) => s + e.tickets_week, 0),
    month: events.reduce((s, e) => s + e.tickets_month, 0),
  };

  const cards: { key: PeriodKey; label: string; detail: string }[] = [
    {
      key: "today",
      label: "Aujourd’hui",
      detail: `${shortDay(today)} · depuis 00h UTC`,
    },
    { key: "yesterday", label: "Hier", detail: shortDay(yesterday) },
    {
      key: "week",
      label: "Cette semaine",
      detail: `Depuis lundi ${shortDay(weekStart)}`,
    },
    {
      key: "month",
      label: "Ce mois-ci",
      detail: `Depuis le ${shortDay(monthStart)}`,
    },
  ];

  return (
    <section aria-label="Billets vendus par période" className="space-y-2">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cards.map((card) => (
          <Card key={card.key} className="h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {card.label}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              <p className="text-3xl font-semibold tabular-nums">
                {totals[card.key].toLocaleString("fr-FR")}
              </p>
              <p className="text-xs text-muted-foreground">billets Shotgun</p>
              <p className="text-xs text-muted-foreground">{card.detail}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">{footnote}</p>
    </section>
  );
}
