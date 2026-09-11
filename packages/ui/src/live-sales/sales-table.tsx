"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CalendarDays,
  ExternalLink,
  Wind,
} from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { CellTooltip } from "../ui/cell-tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import type { SalesEvent, TrendComparison } from "./types";
import { WeatherChart } from "./weather-chart";

export type SortableColumn =
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

export type SortDirection = "asc" | "desc";

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

// Mirrors Admin's lib/daily-sales/benchmark.ts classifyRatio.
function classifyRatio(
  primaryTotal: number,
  benchmarkTotal: number
): "ahead" | "on_track" | "behind" {
  if (benchmarkTotal === 0) {
    return primaryTotal > 0 ? "ahead" : "on_track";
  }
  const ratio = primaryTotal / benchmarkTotal;
  if (ratio >= 1.2) return "ahead";
  if (ratio <= 0.8) return "behind";
  return "on_track";
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

const BENCHMARK_LABELS: Record<string, string> = {
  same_period: "même période",
  latest: "derniers events",
};

const CONJONCTURE_STYLES: Record<
  string,
  { label: string; symbol: string; color: string }
> = {
  favorable: {
    label: "Favorable",
    symbol: "↑",
    color: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
  unfavorable: {
    label: "Défavorable",
    symbol: "↓",
    color: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  },
  mixed: {
    label: "Mitigé",
    symbol: "↔",
    color: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  },
  neutral: {
    label: "Neutre",
    symbol: "·",
    color: "bg-muted text-muted-foreground",
  },
  unknown: {
    label: "À vérifier",
    symbol: "?",
    color: "bg-muted text-muted-foreground",
  },
};

// Same palette as Admin's lib/lifecycle.tsx (plural labels for the tooltip).
const LIFECYCLE_STAGES: { key: string; label: string; color: string }[] = [
  { key: "new", label: "Nouveaux", color: "#34d399" },
  { key: "active", label: "Actifs", color: "#38bdf8" },
  { key: "repeat", label: "Récurrents", color: "#a78bfa" },
  { key: "loyal", label: "Fidèles", color: "#fbbf24" },
  { key: "dormant", label: "Dormants", color: "#94a3b8" },
  { key: "never_purchased", label: "Jamais acheté", color: "#475569" },
];

const AGE_BAND_ORDER = [
  "14-17",
  "18-24",
  "25-34",
  "35-44",
  "45-54",
  "55-64",
  "65+",
];

function trendOrder(s?: string): number {
  return s
    ? ({ ahead: 0, on_track: 1, behind: 2 } as Record<string, number>)[s] ?? 3
    : 3;
}

function conjonctureOrder(s?: string): number {
  return s
    ? ({ favorable: 0, mixed: 1, neutral: 2, unfavorable: 3, unknown: 4 } as Record<
        string,
        number
      >)[s] ?? 4
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
  const aM =
    a === null || a === undefined || (typeof a === "number" && !Number.isFinite(a));
  const bM =
    b === null || b === undefined || (typeof b === "number" && !Number.isFinite(b));
  if (aM && bM) return 0;
  if (aM) return 1;
  if (bM) return -1;
  return dir * ((a as number) - (b as number));
}

function compareStr(a: string, b: string, dir: number): number {
  return dir * a.localeCompare(b, "fr");
}

function temperature(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value)}°C`;
}

function TrendCell({ event }: { event: SalesEvent }) {
  if (!event.trend_status) {
    return <span className="block text-center text-muted-foreground">—</span>;
  }
  const comparisons = event.trend_comparisons ?? [];
  return (
    <CellTooltip
      className="w-fit cursor-help mx-auto"
      content={
        <>
          <div className="flex items-center gap-2">
            <span
              className="rounded px-1.5 py-0.5 font-medium"
              style={{
                backgroundColor: TREND_COLORS[event.trend_status],
                color: "#0f172a",
              }}
            >
              {TREND_LABELS[event.trend_status]}
            </span>
            <span className="font-medium">
              {event.trend_total} vendus
            </span>
            <span className="text-muted-foreground">
              à J-{event.trend_days_before}
            </span>
          </div>
          {comparisons.length > 0 && (
            <div className="mt-2 border-t border-foreground/10">
              {comparisons.map((c) => {
                const methodStatus = classifyRatio(
                  event.trend_total ?? 0,
                  c.total
                );
                return (
                  <div key={c.method} className="mt-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">
                        {BENCHMARK_LABELS[c.method] ?? c.method}
                        {c.method === event.trend_benchmark_method && (
                          <span className="ml-1 font-normal text-muted-foreground">
                            · utilisé
                          </span>
                        )}
                      </span>
                      <span
                        className="rounded px-1 py-px font-medium"
                        style={{
                          backgroundColor: TREND_COLORS[methodStatus],
                          color: "#0f172a",
                        }}
                      >
                        {TREND_LABELS[methodStatus]}
                      </span>
                    </div>
                    <div className="text-muted-foreground">
                      moy. {c.total} · {c.count} event{c.count > 1 ? "s" : ""}
                    </div>
                    <ul className="mt-1 space-y-0.5 border-l border-foreground/10 pl-2 text-muted-foreground">
                      {c.entries.map((b, i) => (
                        <li key={i} className="flex justify-between gap-3">
                          <span className="truncate">
                            {new Date(b.date).toLocaleDateString("fr-FR", {
                              day: "numeric",
                              month: "short",
                              year: "numeric",
                            })}{" "}
                            · {b.name}
                          </span>
                          <span className="font-medium tabular-nums text-foreground">
                            {b.total}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-0.5">
        <Badge
          className="w-fit"
          style={{
            backgroundColor: TREND_COLORS[event.trend_status],
            color: "#0f172a",
          }}
        >
          {TREND_LABELS[event.trend_status]}
        </Badge>
      </div>
    </CellTooltip>
  );
}

function WeatherCell({ event }: { event: SalesEvent }) {
  const hasForecast =
    event.weather_start_temp !== undefined &&
    event.weather_start_temp !== null;

  if (!hasForecast) {
    const availableDate = event.weather_available_from
      ? new Date(event.weather_available_from).toLocaleDateString("fr-FR", {
          day: "numeric",
          month: "short",
          year: "numeric",
          timeZone: "Europe/Paris",
        })
      : null;
    return (
      <CellTooltip
        className="w-fit cursor-help rounded-full focus-visible:outline focus-visible:outline-ring"
        content={
          <p className="whitespace-normal">
            {availableDate
              ? `Prévisions attendues à partir du ${availableDate}. Open-Meteo couvre environ 15 jours ; aucune température fiable n’est encore disponible pour cet événement.`
              : "Prévision indisponible pour le moment (lieu, date ou données météo manquants). Une nouvelle tentative sera faite automatiquement."}
          </p>
        }
      >
        <span className="inline-flex rounded-full border border-foreground/10 bg-muted/60 px-2.5 py-1 text-xs text-muted-foreground">
          {availableDate ? `Prév. le ${availableDate}` : "Météo indisponible"}
        </span>
      </CellTooltip>
    );
  }

  return (
    <CellTooltip
      className="w-fit cursor-help rounded-full focus-visible:outline focus-visible:outline-ring"
      contentWidth={440}
      content={
        <div className="space-y-3 whitespace-normal py-1">
          <div>
            <p className="font-semibold text-sm">
              Météo à l’ouverture · {event.weather_opening_time ?? "—"}
            </p>
            {event.weather_opening_is_fallback && (
              <p className="mt-1 text-muted-foreground">
                Horaire d’ouverture non renseigné : heure de début utilisée.
              </p>
            )}
          </div>
          <div className="flex flex-wrap justify-between gap-2 border-y border-foreground/10 py-2 tabular-nums">
            <span>
              {temperature(event.weather_start_temp)} →{" "}
              {temperature(event.weather_end_temp)}
            </span>
            <span>
              Précipitations :{" "}
              {event.weather_precip_total === null ||
              event.weather_precip_total === undefined
                ? "—"
                : `${event.weather_precip_total.toLocaleString("fr-FR", {
                    maximumFractionDigits: 1,
                  })} mm`}
            </span>
          </div>
          {event.weather_sky_emoji && (
            <div className="flex items-center gap-2 text-sm">
              <span
                role="img"
                aria-label={event.weather_sky_label}
                className="text-base leading-none"
              >
                {event.weather_sky_emoji}
              </span>
              <span>{event.weather_sky_label}</span>
            </div>
          )}
          <div className="flex items-center gap-2 text-sm tabular-nums">
            <Wind className="h-3.5 w-3.5 text-muted-foreground" />
            <span>
              Vent{" "}
              {event.weather_wind_speed !== undefined &&
              event.weather_wind_speed !== null
                ? `${Math.round(event.weather_wind_speed)} km/h`
                : "—"}
            </span>
          </div>
          {event.weather_hours && event.weather_hours.length > 0 && (
            <WeatherChart hours={event.weather_hours} />
          )}
        </div>
      }
    >
      <span className="inline-flex items-center gap-1 rounded-full border border-foreground/10 bg-muted/60 px-2.5 py-1 text-xs tabular-nums">
        {event.weather_sky_emoji && (
          <span aria-label={event.weather_sky_label}>
            {event.weather_sky_emoji}
          </span>
        )}
        <span>
          {temperature(event.weather_start_temp)} →{" "}
          {temperature(event.weather_end_temp)}
        </span>
      </span>
    </CellTooltip>
  );
}

function ConjonctureCell({ event }: { event: SalesEvent }) {
  const status = event.conjoncture_status ?? "unknown";
  const style = CONJONCTURE_STYLES[status] ?? CONJONCTURE_STYLES.unknown;
  const reasons = event.conjoncture_reasons ?? [];
  const limitations = event.conjoncture_limitations ?? [];

  return (
    <CellTooltip
      className="mx-auto w-fit cursor-help rounded-full focus-visible:outline focus-visible:outline-ring"
      contentWidth={380}
      content={
        <div className="space-y-3 whitespace-normal py-1">
          <div>
            <p className="font-semibold text-sm">
              Contexte calendaire · {style.label}
            </p>
            <p className="mt-1 text-muted-foreground">
              {event.conjoncture_city || "Ville non renseignée"}
              {event.conjoncture_zone
                ? ` · Zone ${event.conjoncture_zone} · Académie de ${event.conjoncture_academy}`
                : ""}
            </p>
            {event.conjoncture_date && (
              <p className="mt-1">
                {new Date(
                  `${event.conjoncture_date}T12:00:00Z`
                ).toLocaleDateString("fr-FR", {
                  weekday: "long",
                  day: "numeric",
                  month: "long",
                  year: "numeric",
                  timeZone: "Europe/Paris",
                })}
              </p>
            )}
          </div>
          {reasons.length ? (
            <ul className="space-y-2 border-t border-foreground/10 pt-2">
              {reasons.map((reason, index) => (
                <li key={index} className="space-y-0.5">
                  <p className="font-medium">
                    {reason.influence > 0
                      ? "➕ "
                      : reason.influence < 0
                        ? "➖ "
                        : ""}
                    {reason.label}
                  </p>
                  <p className="text-muted-foreground">{reason.detail}</p>
                  <p className="text-muted-foreground">
                    Source :{" "}
                    {reason.sourceUrl ? (
                      <a
                        href={reason.sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline"
                      >
                        {reason.source}
                      </a>
                    ) : (
                      reason.source
                    )}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">
              {status === "neutral"
                ? "Aucun signal particulier parmi les vacances locales, ponts et jours fériés couverts."
                : "Données insuffisantes pour qualifier cette date."}
            </p>
          )}
          {limitations.map((message) => (
            <p key={message} className="text-amber-700 dark:text-amber-300">
              {message}
            </p>
          ))}
          <p className="border-t border-foreground/10 pt-2 text-muted-foreground">
            Hypothèses : vacances locales et ponts plutôt défavorables
            (départs), veille de jour férié favorable. Signaux opposés : mitigé.
            Indication de contexte, pas une prévision des ventes. Les festivals
            sans localisation ne sont pas appliqués nationalement.
          </p>
        </div>
      }
    >
      <span
        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${style.color}`}
      >
        <span className="text-sm">{style.symbol}</span>
        {style.label}
      </span>
    </CellTooltip>
  );
}

function DemoCell({ event }: { event: SalesEvent }) {
  const buyers = event.demo_buyers ?? 0;
  if (buyers === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  const f = event.demo_gender_female ?? 0;
  const m = event.demo_gender_male ?? 0;
  const o = event.demo_gender_other ?? 0;
  const genderKnown = f + m + o;
  const fPct = genderKnown ? Math.round((f / genderKnown) * 100) : null;
  const mPct = genderKnown ? Math.round((m / genderKnown) * 100) : null;
  const oPct = genderKnown ? 100 - (fPct ?? 0) - (mPct ?? 0) : null;

  const ageBands = event.demo_age_bands ?? {};
  const bandTotal = Object.values(ageBands).reduce((a, b) => a + b, 0);

  return (
    <CellTooltip
      className="w-fit cursor-help"
      contentWidth={300}
      content={
        <div className="space-y-2">
          <div className="font-medium">
            {buyers.toLocaleString("fr-FR")} acheteur
            {buyers > 1 ? "s" : ""} — source Shotgun, jamais inféré
          </div>
          {genderKnown > 0 && (
            <div>
              <div className="flex h-2 w-full overflow-hidden rounded">
                <span style={{ width: `${fPct}%`, background: "#f472b6" }} />
                <span style={{ width: `${mPct}%`, background: "#60a5fa" }} />
                <span style={{ width: `${oPct}%`, background: "#a78bfa" }} />
              </div>
              <div className="mt-1 text-muted-foreground">
                {f} F ({fPct}%) · {m} H ({mPct}%)
                {o > 0 ? ` · ${o} autre (${oPct}%)` : ""}
              </div>
            </div>
          )}
          {bandTotal > 0 && (
            <div>
              <div className="mb-1 font-medium">
                Âges — moyenne {event.demo_avg_age ?? "—"} ans (
                {event.demo_age_known
                  ? Math.round((event.demo_age_known / buyers) * 100)
                  : 0}
                % connus)
              </div>
              <div className="space-y-0.5">
                {AGE_BAND_ORDER.filter((b) => ageBands[b]).map((b) => (
                  <div key={b} className="flex items-center gap-2">
                    <span className="w-12 text-muted-foreground">{b}</span>
                    <div className="h-1.5 flex-1 rounded bg-foreground/10">
                      <div
                        className="h-1.5 rounded bg-primary/70"
                        style={{
                          width: `${Math.round((ageBands[b] / bandTotal) * 100)}%`,
                        }}
                      />
                    </div>
                    <span className="w-8 text-right tabular-nums text-muted-foreground">
                      {ageBands[b]}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      }
    >
      <div className="flex w-fit items-center gap-2 rounded-full border border-foreground/15 bg-foreground/5 px-2 py-1">
        {genderKnown > 0 ? (
          <div
            className="flex h-2 w-16 overflow-hidden rounded-full"
            title={`${fPct}% F / ${mPct}% H`}
          >
            <span style={{ width: `${fPct}%`, background: "#f472b6" }} />
            <span style={{ width: `${mPct}%`, background: "#60a5fa" }} />
            {(oPct ?? 0) > 0 && (
              <span style={{ width: `${oPct}%`, background: "#a78bfa" }} />
            )}
          </div>
        ) : (
          <span className="text-[10px] text-muted-foreground">genre ?</span>
        )}
        <span className="text-xs font-medium tabular-nums whitespace-nowrap">
          {event.demo_avg_age !== null && event.demo_avg_age !== undefined
            ? `~${event.demo_avg_age} ans`
            : "—"}
        </span>
      </div>
    </CellTooltip>
  );
}

function LifecycleCell({ event }: { event: SalesEvent }) {
  const dist = event.lifecycle_dist;
  const total = event.demo_buyers ?? 0;
  const stages = LIFECYCLE_STAGES.filter((s) => (dist?.[s.key] ?? 0) > 0);
  if (!dist || total === 0 || stages.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <CellTooltip
      className="w-fit cursor-help"
      contentWidth={240}
      content={
        <div className="space-y-1.5">
          <div className="font-medium">Lifecycle des acheteurs</div>
          {stages.map((s) => (
            <div key={s.key} className="flex items-center gap-2">
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: s.color }}
              />
              <span className="flex-1">{s.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {dist[s.key]} · {Math.round((dist[s.key] / total) * 100)}%
              </span>
            </div>
          ))}
        </div>
      }
    >
      <div className="flex w-fit items-center rounded-full border border-foreground/15 bg-foreground/5 px-2 py-1">
        <div className="flex h-2 w-20 overflow-hidden rounded-full">
          {stages.map((s) => (
            <span
              key={s.key}
              style={{
                width: `${(dist[s.key] / total) * 100}%`,
                background: s.color,
              }}
            />
          ))}
        </div>
      </div>
    </CellTooltip>
  );
}

export function SalesTable({
  events,
  eventHref = "/events/{id}",
  userId,
  initialGroupByWeek = false,
  initialSortColumn = null,
  initialSortDirection = "asc",
  onSaveGroupByWeek,
  onSaveSort,
}: {
  events: SalesEvent[];
  eventHref?: string;
  userId?: string;
  initialGroupByWeek?: boolean;
  initialSortColumn?: SortableColumn | null;
  initialSortDirection?: SortDirection;
  // Server actions — each app persists prefs its own way (user_settings row).
  onSaveGroupByWeek?: (value: boolean) => void | Promise<void>;
  onSaveSort?: (
    column: SortableColumn | null,
    direction: SortDirection
  ) => void | Promise<void>;
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
          return compareNum(
            eventTimeMinutes(a.start_time),
            eventTimeMinutes(b.start_time),
            dir
          );
        case "weather":
          return compareNum(a.weather_start_temp, b.weather_start_temp, dir);
        case "conjoncture":
          return (
            dir *
            (conjonctureOrder(a.conjoncture_status) -
              conjonctureOrder(b.conjoncture_status))
          );
        case "sold":
          return compareNum(a.sold, b.sold, dir);
        case "left":
          return compareNum(a.left, b.left, dir);
        case "percentage":
          return compareNum(a.percentage, b.percentage, dir);
        case "link":
          return (
            dir *
            ((a.tickets_sales_url ? 1 : 0) - (b.tickets_sales_url ? 1 : 0))
          );
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
    // Order groups by the position of their first item in the sorted list —
    // the active sort column/direction stays visually in effect.
    const rank = new Map<SalesEvent, number>();
    sortedEvents.forEach((e, i) => rank.set(e, i));
    return [...map.values()]
      .map((group) => ({
        type: "week" as const,
        ...group,
        rank: Math.min(...group.items.map((e) => rank.get(e) ?? Infinity)),
      }))
      .sort((a, b) => a.rank - b.rank);
  }, [sortedEvents, groupByWeek]);

  function handleSort(column: SortableColumn) {
    const nextDirection: SortDirection =
      sortColumn === column
        ? sortDirection === "asc"
          ? "desc"
          : "asc"
        : "asc";
    setSortColumn(column);
    setSortDirection(nextDirection);
    if (sortStorageKey && typeof window !== "undefined") {
      window.localStorage.setItem(
        sortStorageKey,
        JSON.stringify({ column, direction: nextDirection })
      );
    }
    if (onSaveSort) {
      Promise.resolve(onSaveSort(column, nextDirection))
        .then(() => {
          // Server value is now authoritative — drop the optimistic cache.
          if (sortStorageKey && typeof window !== "undefined") {
            window.localStorage.removeItem(sortStorageKey);
          }
        })
        .catch(() => {});
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
    if (onSaveGroupByWeek) {
      Promise.resolve(onSaveGroupByWeek(next))
        .then(() => {
          if (storageKey && typeof window !== "undefined") {
            window.localStorage.removeItem(storageKey);
          }
        })
        .catch(() => {});
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
      align === "right"
        ? "justify-end"
        : align === "center"
          ? "justify-center"
          : "";
    const text =
      align === "right"
        ? "text-right"
        : align === "center"
          ? "text-center"
          : "";
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
                      if ((e.target as HTMLElement).closest("a, button"))
                        return;
                      window.location.href = href;
                    }}
                  >
                    <TableCell className="font-medium">
                      {event.days_until !== undefined &&
                      event.days_until !== null
                        ? `D${event.days_until > 0 ? "-" : "+"}${Math.abs(event.days_until)}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      <TrendCell event={event} />
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
                      <WeatherCell event={event} />
                    </TableCell>
                    <TableCell>
                      <ConjonctureCell event={event} />
                    </TableCell>
                    <TableCell>
                      <DemoCell event={event} />
                    </TableCell>
                    <TableCell>
                      <LifecycleCell event={event} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.sold ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.left ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {event.percentage !== undefined
                        ? `${event.percentage}%`
                        : "—"}
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
