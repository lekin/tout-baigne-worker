// Shared row shape for the live-sales table. Fields not available to a
// given consumer are left undefined and the cell renders "—". This lets
// Admin and Backstage use the exact same table component with the same
// columns.
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
  // Computed fields — materialized by Admin, rendered identically in both apps
  days_until?: number | null;
  trend_status?: "ahead" | "on_track" | "behind";
  trend_total?: number;
  trend_days_before?: number;
  weather_start_temp?: number | null;
  weather_end_temp?: number | null;
  weather_precip_total?: number | null;
  weather_sky_emoji?: string;
  weather_sky_label?: string;
  weather_available_from?: string;
  conjoncture_status?: "favorable" | "mixed" | "neutral" | "unfavorable" | "unknown";
  conjoncture_label?: string;
  demo_buyers?: number;
  demo_avg_age?: number | null;
  demo_gender_female?: number;
  demo_gender_male?: number;
  demo_gender_other?: number;
  lifecycle_dist?: Record<string, number>;
  tickets_sales_url?: string;
}
