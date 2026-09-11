// Minimal safe row for the shared live-sales surface. Backstage fills it
// from get_backstage_events(); Admin can fill it from its richer loaders.
export interface SalesEvent {
  id: string;
  name: string;
  date: string;
  brand_name: string;
  tickets_today: number;
  tickets_yesterday: number;
  tickets_week: number;
  tickets_month: number;
  tickets_total: number;
}
