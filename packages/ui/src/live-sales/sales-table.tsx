"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Input } from "../ui/input";
import type { SalesEvent } from "./types";

type SortKey =
  | "name"
  | "date"
  | "brand_name"
  | "tickets_today"
  | "tickets_yesterday"
  | "tickets_week"
  | "tickets_month"
  | "tickets_total";

function formatDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  });
}

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: "name", label: "Événement" },
  { key: "date", label: "Date" },
  { key: "brand_name", label: "Marque" },
  { key: "tickets_today", label: "Aujourd’hui", numeric: true },
  { key: "tickets_yesterday", label: "Hier", numeric: true },
  { key: "tickets_week", label: "Semaine", numeric: true },
  { key: "tickets_month", label: "Mois", numeric: true },
  { key: "tickets_total", label: "Total", numeric: true },
];

// Shared sortable/searchable sales table — same columns and French labels
// as the Admin live-sales table, driven by the minimal SalesEvent shape.
export function SalesTable({
  events,
  getEventHref,
}: {
  events: SalesEvent[];
  getEventHref?: (event: SalesEvent) => string;
}) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? events.filter(
          (e) =>
            e.name.toLowerCase().includes(q) ||
            (e.brand_name ?? "").toLowerCase().includes(q)
        )
      : events;

    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), "fr");
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [events, query, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "date" || key === "name" ? "asc" : "desc");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <Input
          type="search"
          aria-label="Rechercher un événement ou une marque"
          placeholder="Événement ou marque…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-56"
        />
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50 text-left">
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={`px-3 py-2 font-medium ${col.numeric ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort(col.key)}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    {col.label}
                    {sortKey === col.key && (
                      <span aria-hidden>{sortDir === "asc" ? "↑" : "↓"}</span>
                    )}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={COLUMNS.length}
                  className="px-3 py-6 text-center text-muted-foreground"
                >
                  Aucun événement.
                </td>
              </tr>
            ) : (
              rows.map((e) => {
                const href = getEventHref?.(e);
                return (
                  <tr key={e.id} className="border-b last:border-0">
                    <td className="px-3 py-2">
                      {href ? (
                        <Link
                          href={href}
                          className="font-medium hover:underline"
                        >
                          {e.name}
                        </Link>
                      ) : (
                        <span className="font-medium">{e.name}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatDate(e.date)}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {e.brand_name}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {e.tickets_today.toLocaleString("fr-FR")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {e.tickets_yesterday.toLocaleString("fr-FR")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {e.tickets_week.toLocaleString("fr-FR")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {e.tickets_month.toLocaleString("fr-FR")}
                    </td>
                    <td className="px-3 py-2 text-right font-medium tabular-nums">
                      {e.tickets_total.toLocaleString("fr-FR")}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
