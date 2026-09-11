"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CalendarDays,
  ExternalLink,
} from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import type { SalesEvent } from "./types";

type SortableColumn =
  | "days"
  | "trend"
  | "status"
  | "event"
  | "venue"
  | "date"
  | "time"
  | "weather"
  | "conjoncture"
  | "sold"
  | "left"
  | "percentage"
  | "link";

type SortDirection = "asc" | "desc";

function formatDate(value: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function formatTime(value?: string): string {
  if (!value) return "—";
  const d = new Date(`2000-01-01T${value}`);
  if (Number.isNaN(d.getTime())) {
    const iso = new Date(value);
    if (!Number.isNaN(iso.getTime())) {
      return iso.toLocaleTimeString("fr-FR", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Europe/Paris",
      });
    }
    return value;
  }
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function eventTimeMinutes(value?: string): number {
  if (!value) return Infinity;
  const normalized = value
    .trim()
    .replace(/^(\d{1,2})h(\d{2})$/i, "$1:$2")
    .padStart(5, "0");
  const [h, m] = normalized.split(":");
  if (!h || !m) return Infinity;
  const hh = Number.parseInt(h, 10);
  const mm = Number.parseInt(m, 10);
  if (Number.isNaN(hh) || Number.isNaN(mm)) return Infinity;
  return hh * 60 + mm;
}

function salesStatusVariant(status?: string) {
  const n = status?.toLowerCase().trim() ?? "";
  if (n.includes("on sale") || n.includes("en vente")) return "default" as const;
  if (n.includes("sold out") || n.includes("complet")) return "destructive" as const;
  if (n.includes("last") || n.includes("dernières")) return "secondary" as const;
  return "outline" as const;
}

const TREND_LABELS: Record<string, string> = {
  ahead: "En avance",
  on_track: "On track",
  behind: "En retard",
};

const TREND_COLORS: Record<string, string> = {
  ahead: "#34d399",
  on_track: "#38bdf8",
  behind: "#f87171",
};

const CONJONCTURE_LABELS: Record<string, string> = {
  favorable: "Favorable",
  mixed: "Mitigé",
  neutral: "Neutre",
  unfavorable: "Défavorable",
  unknown: "—",
};

const CONJONCTURE_COLORS: Record<string, string> = {
  favorable: "#34d399",
  mixed: "#fbbf24",
  neutral: "#94a3b8",
  unfavorable: "#f87171",
  unknown: "#94a3b8",
};

function trendOrder(s?: string): number {
  return s ? ({ ahead: 0, on_track: 1, behind: 2 } as Record<string, number>)[s] ?? 3 : 3;
}

function conjonctureOrder(s?: string): number {
  return s
    ? ({ favorable: 0, mixed: 1, neutral: 2, unfavorable: 3, unknown: 4 } as Record<string, number>)[s] ?? 4
    : 4;
}

function getWeekStart(d: Date): Date {
  const day = d.getDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const start = new Date(d);
  start.setDate(d.getDate() + diff);
  start.setHours(0, 0, 0, 0);
  return start;
}

function compareNum(
  a: number | null | undefined,
  b: number | null | undefined,
  dir: number
): number {
  const aM = a === null || a === undefined || (typeof a === "number" && !Number.isFinite(a));
  const bM = b === null || b === undefined || (typeof b === "number" && !Number.isFinite(b));
  if (aM && bM) return 0;
  if (aM) return 1;
  if (bM) return -1;
  return dir * ((a as number) - (b as number));
}

function compareStr(a: string, b: string, dir: number): number {
  return dir * a.localeCompare(b, "fr");
}

export function SalesTable({
  events,
  eventHref = "/events/{id}",
  userId,
  initialGroupByWeek = false,
  initialSortColumn = null,
  initialSortDirection = "asc",
}: {
  events: SalesEvent[];
  eventHref?: string;
  userId?: string;
  initialGroupByWeek?: boolean;
  initialSortColumn?: SortableColumn | null;
  initialSortDirection?: SortDirection;
}) {
  const storageKey = useMemo(
    () => (userId ? `tbp.live-sales.groupByWeek.${userId}` : null),
    [userId]
  );
  const sortStorageKey = useMemo(
    () => (userId ? `tbp.live-sales.sort.${userId}` : null),
    [userId]
  );

  const [sortColumn, setSortColumn] = useState<SortableColumn | null>(
    initialSortColumn
  );
  const [sortDirection, setSortDirection] = useState<SortDirection>(
    initialSortDirection
  );
  const [groupByWeek, setGroupByWeek] = useState(initialGroupByWeek);

  useEffect(() => {
    if (!storageKey || typeof window === "undefined") return;
    const saved = window.localStorage.getItem(storageKey);
    if (saved !== null) setGroupByWeek(saved === "true");
  }, [storageKey]);

  useEffect(() => {
    if (!sortStorageKey || typeof window === "undefined") return;
    const saved = window.localStorage.getItem(sortStorageKey);
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved);
      if (
        parsed &&
        (parsed.column === null || typeof parsed.column === "string") &&
        (parsed.direction === "asc" || parsed.direction === "desc")
      ) {
        setSortColumn(parsed.column as SortableColumn | null);
        setSortDirection(parsed.direction);
      }
    } catch {}
  }, [sortStorageKey]);

  const sortedEvents = useMemo(() => {
    if (!sortColumn) return [...events];
    const dir = sortDirection === "asc" ? 1 : -1;
    return [...events].sort((a, b) => {
      switch (sortColumn) {
        case "days":
          return compareNum(a.days_until, b.days_until, dir);
        case "trend":
          return dir * (trendOrder(a.trend_status) - trendOrder(b.trend_status));
        case "status":
          return compareStr(a.status ?? "", b.status ?? "", dir);
        case "event":
          return compareStr(a.name, b.name, dir);
        case "venue":
          return compareStr(a.venue, b.venue, dir);
        case "date":
          return dir * (new Date(a.date).getTime() - new Date(b.date).getTime());
        case "time":
          return compareNum(eventTimeMinutes(a.start_time), eventTimeMinutes(b.start_time), dir);
        case "weather":
          return compareNum(a.weather_start_temp, b.weather_start_temp, dir);
        case "conjoncture":
          return dir * (conjonctureOrder(a.conjoncture_status) - conjonctureOrder(b.conjoncture_status));
        case "sold":
          return compareNum(a.sold, b.sold, dir);
        case "left":
          return compareNum(a.left, b.left, dir);
        case "percentage":
          return compareNum(a.percentage, b.percentage, dir);
        case "link":
          return dir * ((a.tickets_sales_url ? 1 : 0) - (b.tickets_sales_url ? 1 : 0));
      }
      return 0;
    });
  }, [events, sortColumn, sortDirection]);

  const grouped = useMemo(() => {
    if (!groupByWeek) return [{ type: "all" as const, items: sortedEvents }];
    const map = new Map<string, { date: Date; items: SalesEvent[] }>();
    for (const event of sortedEvents) {
      const d = new Date(event.date);
      const weekStart = getWeekStart(d);
      const key = weekStart.toISOString().split("T")[0];
      const group = map.get(key) ?? { date: weekStart, items: [] };
      group.items.push(event);
      map.set(key, group);
    }
    return [...map.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, group]) => ({ type: "week" as const, ...group }));
  }, [sortedEvents, groupByWeek]);

  function handleSort(column: SortableColumn) {
    const nextDirection: SortDirection =
      sortColumn === column ? (sortDirection === "asc" ? "desc" : "asc") : "asc";
    setSortColumn(column);
    setSortDirection(nextDirection);
    if (sortStorageKey && typeof window !== "undefined") {
      window.localStorage.setItem(
        sortStorageKey,
        JSON.stringify({ column, direction: nextDirection })
      );
    }
  }

  function sortIcon(column: SortableColumn) {
    if (sortColumn !== column)
      return <ArrowUpDown className="h-3 w-3 text-muted-foreground" />;
    return sortDirection === "asc" ? (
      <ArrowUp className="h-3 w-3 text-foreground" />
    ) : (
      <ArrowDown className="h-3 w-3 text-foreground" />
    );
  }

  function toggleGroupByWeek() {
    const next = !groupByWeek;
    setGroupByWeek(next);
    if (storageKey && typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, String(next));
    }
  }

  if (events.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground border border-foreground/10 rounded-xl bg-card">
        Aucun événement à venir ne correspond à ces filtres.
      </div>
    );
  }

  function header(
    column: SortableColumn,
    label: React.ReactNode,
    align: "left" | "center" | "right" = "left"
  ) {
    const justify =
      align === "right" ? "justify-end" : align === "center" ? "justify-center" : "";
    const text =
      align === "right" ? "text-right" : align === "center" ? "text-center" : "";
    return (
      <TableHead
        className={`cursor-pointer select-none ${text}`}
        onClick={() => handleSort(column)}
      >
        <span
          className={`inline-flex items-center gap-1 ${justify} ${
            align !== "left" ? "w-full" : ""
          }`}
        >
          {label} {sortIcon(column)}
        </span>
      </TableHead>
    );
  }

  return (
    <div className="border border-foreground/10 rounded-xl overflow-hidden bg-card">
      <div className="flex items-center justify-end gap-2 p-3 border-b border-foreground/10">
        <Button
          type="button"
          variant={groupByWeek ? "default" : "outline"}
          size="sm"
          onClick={toggleGroupByWeek}
        >
          <CalendarDays className="h-4 w-4 mr-1.5" />
          {groupByWeek ? "Désactiver le regroupement" : "Regrouper par semaine"}
        </Button>
      </div>
      <Table className="[&_tr]:border-foreground/10">
        <TableHeader>
          <TableRow>
            {header("days", "D")}
            {header("trend", "Trend", "center")}
            {header("status", "Status")}
            {header("event", "Event")}
            {header("venue", "Venue")}
            {header("date", "Date")}
            {header("time", "Time")}
            {header("weather", "Weather")}
            {header("conjoncture", "Conjoncture", "center")}
            <TableHead>Demography</TableHead>
            <TableHead>Lifecycle</TableHead>
            {header("sold", "Sold", "right")}
            {header("left", "Left", "right")}
            {header("percentage", "%", "right")}
            {header("link", "Link", "center")}
          </TableRow>
        </TableHeader>
        <TableBody>
          {grouped.map((group) => (
            <Fragment
              key={group.type === "week" ? group.date.toISOString() : "all"}
            >
              {group.type === "week" && (
                <TableRow className="bg-muted/50 hover:bg-muted/50">
                  <TableCell
                    colSpan={15}
                    className="py-2 text-sm font-medium text-muted-foreground"
                  >
                    Semaine du{" "}
                    {group.date.toLocaleDateString("fr-FR", {
                      day: "numeric",
                      month: "short",
                      year: "numeric",
                    })}
                  </TableCell>
                </TableRow>
              )}
              {group.items.map((event) => {
                const href = eventHref.replace(
                  "{id}",
                  encodeURIComponent(event.id)
                );
                return (
                  <TableRow
                    key={event.id}
                    className="cursor-pointer"
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest("a, button")) return;
                      window.location.href = href;
                    }}
                  >
                    <TableCell className="font-medium">
                      {event.days_until !== undefined && event.days_until !== null
                        ? `D${event.days_until > 0 ? "-" : "+"}${Math.abs(event.days_until)}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {event.trend_status ? (
                        <Badge
                          className="w-fit"
                          style={{
                            backgroundColor: TREND_COLORS[event.trend_status],
                            color: "#0f172a",
                          }}
                        >
                          {TREND_LABELS[event.trend_status] ?? event.trend_status}
                        </Badge>
                      ) : (
                        <span className="block text-center text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {event.sales_status ? (
                        <Badge variant={salesStatusVariant(event.sales_status)}>
                          {event.sales_status}
                        </Badge>
                      ) : (
                        event.status || "—"
                      )}
                    </TableCell>
                    <TableCell>
                      <Link
                        href={href}
                        className="hover:underline font-medium"
                      >
                        {event.name}
                      </Link>
                    </TableCell>
                    <TableCell>{event.venue || "—"}</TableCell>
                    <TableCell>{formatDate(event.date)}</TableCell>
                    <TableCell>{formatTime(event.start_time)}</TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {event.weather_start_temp !== undefined &&
                      event.weather_start_temp !== null ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-foreground/10 bg-muted/60 px-2.5 py-1 text-xs">
                          {event.weather_sky_emoji && (
                            <span aria-label={event.weather_sky_label}>{event.weather_sky_emoji}</span>
                          )}
                          <span>
                            {Math.round(event.weather_start_temp)}°C →{" "}
                            {event.weather_end_temp !== null && event.weather_end_temp !== undefined
                              ? `${Math.round(event.weather_end_temp)}°C`
                              : "—"}
                          </span>
                        </span>
                      ) : event.weather_available_from ? (
                        <span className="inline-flex rounded-full border border-foreground/10 bg-muted/60 px-2.5 py-1 text-xs text-muted-foreground">
                          Prév. le{" "}
                          {new Date(event.weather_available_from).toLocaleDateString("fr-FR", {
                            day: "numeric",
                            month: "short",
                          })}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {event.conjoncture_status && event.conjoncture_status !== "unknown" ? (
                        <span
                          className="text-xs font-medium"
                          style={{ color: CONJONCTURE_COLORS[event.conjoncture_status] }}
                        >
                          {CONJONCTURE_LABELS[event.conjoncture_status]}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {event.demo_buyers && event.demo_buyers > 0 ? (
                        <div className="flex items-center gap-2">
                          {event.demo_gender_female !== undefined && event.demo_gender_male !== undefined ? (
                            <div className="flex h-2 w-16 overflow-hidden rounded-full">
                              <span
                                style={{
                                  width: `${Math.round((event.demo_gender_female / (event.demo_gender_female + event.demo_gender_male + (event.demo_gender_other ?? 0))) * 100)}%`,
                                  background: "#f472b6",
                                }}
                              />
                              <span
                                style={{
                                  width: `${Math.round((event.demo_gender_male / (event.demo_gender_female + event.demo_gender_male + (event.demo_gender_other ?? 0))) * 100)}%`,
                                  background: "#60a5fa",
                                }}
                              />
                            </div>
                          ) : null}
                          <span className="text-xs font-medium tabular-nums whitespace-nowrap">
                            {event.demo_avg_age !== null && event.demo_avg_age !== undefined
                              ? `~${event.demo_avg_age} ans`
                              : "—"}
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {event.lifecycle_dist && event.demo_buyers && event.demo_buyers > 0 ? (
                        <div className="flex h-2 w-20 overflow-hidden rounded-full">
                          {Object.entries(event.lifecycle_dist)
                            .filter(([, v]) => v > 0)
                            .map(([k, v]) => (
                              <span
                                key={k}
                                style={{
                                  width: `${(v / event.demo_buyers!) * 100}%`,
                                  background: "#94a3b8",
                                }}
                              />
                            ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.sold ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.left ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.percentage !== undefined ? `${event.percentage}%` : "—"}
                    </TableCell>
                    <TableCell>
                      {event.tickets_sales_url ? (
                        <a
                          href={event.tickets_sales_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center text-primary hover:underline"
                          aria-label="Open ticket sales"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
