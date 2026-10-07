/**
 * Sales-day bucketing in the Europe/Paris calendar.
 *
 * `daily_ticket_sales.sales_date` stores the *Paris* calendar day of the
 * purchase — matching how Shotgun and the team read daily numbers. (It used
 * to be the UTC day, which split evening door sales across "today"/"yesterday"
 * in a way that diverged from Shotgun's counters.)
 */

export const PARIS_TZ = "Europe/Paris";

const dayFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: PARIS_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const wallclockFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: PARIS_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatParts(
  formatter: Intl.DateTimeFormat,
  at: Date
): Map<string, string> {
  return new Map(formatter.formatToParts(at).map((p) => [p.type, p.value]));
}

/** YYYY-MM-DD of the instant in Europe/Paris. */
export function toParisDay(at: Date): string {
  const parts = formatParts(dayFormatter, at);
  return `${parts.get("year")}-${parts.get("month")}-${parts.get("day")}`;
}

/** Offset Europe/Paris − UTC, in ms, at the given instant. NaN in → NaN out. */
function parisOffsetMs(at: Date): number {
  if (!Number.isFinite(at.getTime())) return NaN;
  const parts = formatParts(wallclockFormatter, at);
  const hour = parts.get("hour") === "24" ? "00" : parts.get("hour")!;
  const localAsUtc = Date.UTC(
    Number(parts.get("year")),
    Number(parts.get("month")) - 1,
    Number(parts.get("day")),
    Number(hour),
    Number(parts.get("minute")),
    Number(parts.get("second"))
  );
  return localAsUtc - Math.floor(at.getTime() / 1000) * 1000;
}

/** UTC ms timestamp of 00:00 Europe/Paris on the given Paris day. */
export function parisDayStartUtcMs(day: string): number {
  const naiveUtc = Date.parse(`${day}T00:00:00Z`);
  let start = naiveUtc - parisOffsetMs(new Date(naiveUtc));
  // Refine once: on DST transition days the offset at the naive instant
  // differs from the offset at true Paris midnight.
  start = naiveUtc - parisOffsetMs(new Date(start));
  return start;
}

/** Shifts a YYYY-MM-DD day label by `delta` days (TZ-free day math). */
export function shiftDay(day: string, delta: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta, 12)).toISOString().slice(0, 10);
}

/** Shifts a YYYY-MM-DD day label by `delta` months, clamping the day of month. */
export function shiftMonth(day: string, delta: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const total = m - 1 + delta;
  const ty = y + Math.floor(total / 12);
  const tm = (((total % 12) + 12) % 12) + 1;
  const lastDay = new Date(Date.UTC(ty, tm, 0)).getUTCDate();
  const td = Math.min(d, lastDay);
  return new Date(Date.UTC(ty, tm - 1, td, 12)).toISOString().slice(0, 10);
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Calendar days between two Europe/Paris day labels: `laterDay − earlierDay`.
 * Rounds instead of truncating so DST transitions (23h/25h days) still yield
 * exact calendar differences. This is the canonical "J-N" convention: J-5 is
 * the Paris calendar day five days before the event day, regardless of the
 * event's start time.
 */
export function parisDayDiff(laterDay: string, earlierDay: string): number {
  return Math.round(
    (parisDayStartUtcMs(laterDay) - parisDayStartUtcMs(earlierDay)) / DAY_MS
  );
}

/** Calendar days between two instants' Paris days: event day − `at` day. */
export function parisDaysUntil(eventAt: Date, at: Date): number {
  return parisDayDiff(toParisDay(eventAt), toParisDay(at));
}

/**
 * UTC ms for a naive "YYYY-MM-DD[THH:mm[:ss]]" interpreted as Europe/Paris
 * wall time. Airtable date+time fields are Paris-local; parsing them with
 * `new Date()` would use the server's timezone instead.
 */
export function parisWallClockUtcMs(value: string): number {
  const [datePart, timePart = "00:00"] = value.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh = 0, mm = 0, ss = 0] = timePart.split(":").map(Number);
  const naiveUtc = Date.UTC(y, m - 1, d, hh, mm, ss);
  let t = naiveUtc - parisOffsetMs(new Date(naiveUtc));
  // Refine once for DST transitions, same trick as parisDayStartUtcMs.
  t = naiveUtc - parisOffsetMs(new Date(t));
  return t;
}

/**
 * Parses an event start string: explicit instants (…Z or ±HH:mm) are used
 * as-is; naive "YYYY-MM-DD[THH:mm]" values are Europe/Paris wall clock.
 */
export function parseParisDateTime(value: string): Date {
  if (!value) return new Date(NaN);
  if (/[zZ]$/.test(value) || /[+-]\d{2}:?\d{2}$/.test(value)) {
    return new Date(value);
  }
  return new Date(parisWallClockUtcMs(value));
}
