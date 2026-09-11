// Shared row shape for the live-sales table. Fields not available to a
// given consumer (Backstage has no weather/demographics/trend) are left
// undefined and the cell renders "—". This lets Admin and Backstage use
// the exact same table component with the same columns.
export interface SalesEvent {
  id: string;
  name: string;
  date: string;
  venue: string;
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
  // Optional rich fields — Admin fills these, Backstage doesn't
  days_until?: number | null;
  trend_status?: "ahead" | "on_track" | "behind";
  trend_label?: string;
  trend_total?: number;
  trend_days_before?: number;
  weather_temp?: number | null;
  weather_label?: string;
  conjoncture_status?: "favorable" | "mixed" | "neutral" | "unfavorable" | "unknown";
  conjoncture_label?: string;
  demo_gender_female?: number;
  demo_gender_male?: number;
  demo_gender_other?: number;
  demo_avg_age?: number | null;
  demo_buyers?: number;
  lifecycle_dist?: Record<string, number>;
  tickets_sales_url?: string;
}
