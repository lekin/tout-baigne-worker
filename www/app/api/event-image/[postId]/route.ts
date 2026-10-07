import { NextResponse } from "next/server";
import { resolveEventImageUrl } from "@/lib/data/public";

/**
 * Redirects to a fresh signed Airtable attachment URL for an event card image.
 * Airtable URLs expire (~2h); this route keeps images working regardless of
 * page-cache age. V1 — later: ingest to object storage/CDN.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ postId: string }> }
) {
  const { postId } = await params;
  if (!/^rec[a-zA-Z0-9]{14}$/.test(postId)) {
    return new NextResponse("Not found", { status: 404 });
  }
  const url = await resolveEventImageUrl(postId);
  if (!url) {
    return new NextResponse("Not found", { status: 404 });
  }
  return NextResponse.redirect(url, {
    headers: { "Cache-Control": "public, max-age=300" },
  });
}
