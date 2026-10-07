export interface AirtableRecord {
  id: string;
  fields: Record<string, unknown>;
  createdTime: string;
}

export interface AirtableListOptions {
  filterByFormula?: string;
  sort?: { field: string; direction?: "asc" | "desc" }[];
  maxRecords?: number;
  pageSize?: number;
  fields?: string[];
  offset?: string;
  view?: string;
  pageDelayMs?: number;
  revalidate?: number | false;
  tags?: string[];
}

export interface AirtableListResponse {
  records: AirtableRecord[];
  offset?: string;
}

const AIRTABLE_API = "https://api.airtable.com/v0";

function getConfig() {
  const apiKey = process.env.AIRTABLE_API_KEY;
  const baseId = process.env.AIRTABLE_BASE_ID;
  if (!apiKey || !baseId) {
    throw new Error("Airtable API key and base ID are required");
  }
  return { apiKey, baseId };
}

function cacheOptions(options: { revalidate?: number | false; tags?: string[] }) {
  if (options.revalidate === false) return { cache: "no-store" as const };
  return {
    next: {
      revalidate: options.revalidate ?? 900,
      ...(options.tags ? { tags: options.tags } : {}),
    },
  };
}

export async function listTable(
  tableName: string,
  options: AirtableListOptions = {}
): Promise<AirtableRecord[]> {
  const { apiKey, baseId } = getConfig();
  const records: AirtableRecord[] = [];
  const seenIds = new Set<string>();
  let offset: string | undefined;
  let iteratorErrorCount = 0;
  let rateLimitCount = 0;

  while (true) {
    if (options.pageDelayMs) await new Promise((r) => setTimeout(r, options.pageDelayMs));
    const params = new URLSearchParams();
    if (options.filterByFormula) params.set("filterByFormula", options.filterByFormula);
    if (options.maxRecords) params.set("maxRecords", String(options.maxRecords));
    if (options.pageSize) params.set("pageSize", String(options.pageSize));
    if (options.fields) options.fields.forEach((f) => params.append("fields[]", f));
    if (options.sort) {
      options.sort.forEach((s, i) => {
        params.append(`sort[${i}][field]`, s.field);
        params.append(`sort[${i}][direction]`, s.direction ?? "asc");
      });
    }
    if (options.view) params.set("view", options.view);
    if (offset) params.set("offset", offset);

    const url = `${AIRTABLE_API}/${encodeURIComponent(baseId)}/${encodeURIComponent(tableName)}?${params.toString()}`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` },
      ...cacheOptions(options),
    });

    if (!res.ok) {
      const body = await res.text();
      const isIteratorError =
        res.status === 422 && body.includes("LIST_RECORDS_ITERATOR_NOT_AVAILABLE");

      if (isIteratorError && iteratorErrorCount < 3) {
        iteratorErrorCount++;
        offset = undefined;
        continue;
      }

      if (res.status === 429 && rateLimitCount < 4) {
        rateLimitCount++;
        const retryAfter = Number(res.headers.get("retry-after")) || 0;
        await new Promise((r) => setTimeout(r, Math.max(30_000, retryAfter * 1000)));
        continue;
      }

      throw new Error(`Airtable error ${res.status}: ${body}`);
    }

    const data = (await res.json()) as AirtableListResponse;
    rateLimitCount = 0;

    for (const record of data.records) {
      if (!seenIds.has(record.id)) {
        seenIds.add(record.id);
        records.push(record);
      }
    }

    offset = data.offset;
    if (!offset) break;
  }

  return records;
}

export async function getRecord(
  tableName: string,
  recordId: string,
  options: { revalidate?: number | false; tags?: string[] } = {}
): Promise<AirtableRecord | null> {
  const { apiKey, baseId } = getConfig();
  const url = `${AIRTABLE_API}/${encodeURIComponent(baseId)}/${encodeURIComponent(tableName)}/${encodeURIComponent(recordId)}`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${apiKey}` },
    ...cacheOptions(options),
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`Airtable error ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as AirtableRecord;
}

export async function createRecord(
  tableName: string,
  fields: Record<string, unknown>
): Promise<AirtableRecord> {
  const { apiKey, baseId } = getConfig();
  const url = `${AIRTABLE_API}/${encodeURIComponent(baseId)}/${encodeURIComponent(tableName)}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ fields }),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Airtable create error ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as AirtableRecord;
}

export function fieldAsString(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.length > 0) return String(value[0]);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return undefined;
}

export function fieldAsStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (value === null || value === undefined) return [];
  return [String(value)];
}

export function fieldAsNumber(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (Array.isArray(value) && value.length > 0 && typeof value[0] === "number") {
    return value[0];
  }
  return undefined;
}

export interface AirtableAttachment {
  id: string;
  url: string;
  filename?: string;
  width?: number;
  height?: number;
  type?: string;
  thumbnails?: {
    small?: { url: string; width?: number; height?: number };
    large?: { url: string; width?: number; height?: number };
  };
}

export function fieldAsAttachment(value: unknown): AirtableAttachment | undefined {
  const first = Array.isArray(value) ? value[0] : value;
  if (first && typeof first === "object" && "url" in (first as object)) {
    return first as AirtableAttachment;
  }
  return undefined;
}
