import { NextResponse } from "next/server";
import { createRecord } from "@/lib/airtable/client";
import { SITES } from "@/lib/brands";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const VALID_BRAND_SLUGS = new Set(SITES.map((s) => s.slug));

// Naive in-memory rate limit: 5 submissions / IP / minute (best-effort;
// per-instance on serverless — enough to stop dumb floods).
const hits = new Map<string, number[]>();
const RATE_LIMIT = 5;
const WINDOW_MS = 60_000;

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 10_000) hits.clear();
  return recent.length > RATE_LIMIT;
}

const TABLE = process.env.NEWSLETTER_AIRTABLE_TABLE ?? "Newsletter signups";

export async function POST(request: Request) {
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }

  // Honeypot: a filled "company" field means a bot — fake success, no write.
  if (typeof body.company === "string" && body.company.trim() !== "") {
    return NextResponse.json({ ok: true });
  }

  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  if (isRateLimited(ip)) {
    return NextResponse.json({ error: "rate_limited" }, { status: 429 });
  }

  const firstName = String(body.firstName ?? "").trim();
  const lastName = String(body.lastName ?? "").trim();
  const email = String(body.email ?? "").trim();
  const site = String(body.site ?? "");
  const brands = Array.isArray(body.brands)
    ? body.brands.map(String).filter((b) => VALID_BRAND_SLUGS.has(b))
    : [];

  if (
    !firstName ||
    !lastName ||
    firstName.length > 256 ||
    lastName.length > 256 ||
    !EMAIL_RE.test(email) ||
    !VALID_BRAND_SLUGS.has(site)
  ) {
    return NextResponse.json({ error: "invalid_fields" }, { status: 400 });
  }

  // Optional forward to the legacy automation endpoint (e.g. Make webhook).
  const webhookUrl = process.env.NEWSLETTER_WEBHOOK_URL;
  if (webhookUrl) {
    try {
      const res = await fetch(webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firstName, lastName, email, site, brands }),
      });
      if (!res.ok) throw new Error(`webhook ${res.status}`);
    } catch (err) {
      console.error("newsletter webhook error:", err);
      return NextResponse.json({ error: "upstream_failed" }, { status: 502 });
    }
    return NextResponse.json({ ok: true });
  }

  try {
    await createRecord(TABLE, {
      Email: email,
      "First name": firstName,
      "Last name": lastName,
      Site: site,
      Brands: brands,
    });
  } catch (err) {
    console.error("newsletter upstream error:", err);
    return NextResponse.json({ error: "upstream_failed" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
