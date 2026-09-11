// Shared row shape for the live-sales table, including the tooltip payloads.
// Fields not available to a given consumer are left undefined and the cell
// renders "—". Admin and Backstage use the exact same table component.

import type { Asset } from "../assets/types";

export interface TrendComparison {
  method: "same_period" | "latest";
  total: number;
  count: number;
  entries: { date: string; name: string; total: number }[];
}

export interface ConjonctureReason {
  label: string;
  detail: string;
  influence: -1 | 0 | 1;
  source: string;
  sourceUrl?: string;
}

export interface WeatherHour {
  time: string;
  temperature: number;
  precipitation: number;
  windSpeed?: number | null;
  weatherCode?: number | null;
  isDay?: number | null;
}

export interface SalesEvent {
  id: string;
  name: string;
  date: string;
  venue: string;
  venue_city?: string;
  brand_name: string;
  status?: string;
  sales_status?: string;
  start_time?: string;
  // Sales metrics
  tickets_today: number;
  tickets_yesterday: number;
  tickets_week: number;
  tickets_month: number;
  tickets_total: number;
  sold?: number;
  left?: number;
  capacity?: number;
  percentage?: number;
  // Trend tooltip
  days_until?: number | null;
  trend_status?: "ahead" | "on_track" | "behind";
  trend_total?: number;
  trend_days_before?: number;
  trend_benchmark_method?: "same_period" | "latest";
  trend_comparisons?: TrendComparison[];
  // Conjoncture tooltip
  conjoncture_status?: "favorable" | "mixed" | "neutral" | "unfavorable" | "unknown";
  conjoncture_label?: string;
  conjoncture_city?: string;
  conjoncture_zone?: string;
  conjoncture_academy?: string;
  conjoncture_date?: string;
  conjoncture_reasons?: ConjonctureReason[];
  conjoncture_limitations?: string[];
  // Demography tooltip
  demo_buyers?: number;
  demo_avg_age?: number | null;
  demo_gender_female?: number;
  demo_gender_male?: number;
  demo_gender_other?: number;
  demo_age_known?: number;
  demo_age_bands?: Record<string, number>;
  lifecycle_dist?: Record<string, number>;
  // Weather tooltip
  weather_start_temp?: number | null;
  weather_end_temp?: number | null;
  weather_precip_total?: number | null;
  weather_sky_emoji?: string;
  weather_sky_label?: string;
  weather_opening_time?: string;
  weather_opening_is_fallback?: boolean;
  weather_wind_speed?: number | null;
  weather_hours?: WeatherHour[];
  weather_available_from?: string;
  tickets_sales_url?: string;
  // Partner-safe visual assets (detail view) — materialized from Airtable
  // "Social media assets" into events.raw.assets.
  assets?: Asset[];
}
