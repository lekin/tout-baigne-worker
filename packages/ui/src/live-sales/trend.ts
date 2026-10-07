export type SalesTrend = "ahead" | "on_track" | "behind";

// Mirrors @tbp/growth DEFAULT_STATUS_THRESHOLDS (ahead ≥1.2, behind ≤0.8)
// minus its at_risk band (≤0.5), removed for now — keep both in sync.
export function classifySalesTrend(
  primaryTotal: number,
  benchmarkTotal: number
): SalesTrend {
  if (benchmarkTotal <= 0) {
    return primaryTotal > 0 ? "ahead" : "on_track";
  }
  const ratio = primaryTotal / benchmarkTotal;
  if (ratio <= 0.8) return "behind";
  if (ratio >= 1.2) return "ahead";
  return "on_track";
}

export const SALES_TREND_LABELS: Record<SalesTrend, string> = {
  ahead: "En avance",
  on_track: "On track",
  behind: "En retard",
};

export const SALES_TREND_COLORS: Record<SalesTrend, string> = {
  ahead: "#34d399",
  on_track: "#38bdf8",
  behind: "#f87171",
};

export const SALES_BENCHMARK_LABELS: Record<
  "same_period" | "latest",
  string
> = {
  same_period: "même période",
  latest: "derniers events",
};

export const SALES_TREND_ORDER: Record<SalesTrend, number> = {
  ahead: 0,
  on_track: 1,
  behind: 2,
};

/**
 * Label for the relative offset a benchmark comparison is taken at.
 * Hour-aligned under two days ("à H-9"), day-rounded beyond ("à J-3"),
 * "à J" at doors — shared by the admin live-sales tooltip, the sales-curve
 * pill and the Backstage table so all surfaces describe the same instant.
 */
export function formatComparisonOffset(hoursBefore: number): string {
  if (hoursBefore <= 0) return "à J";
  if (hoursBefore < 48) return `à H-${hoursBefore}`;
  return `à J-${Math.floor(hoursBefore / 24)}`;
}
