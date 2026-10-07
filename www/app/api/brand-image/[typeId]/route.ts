import { NextResponse } from "next/server";
import { resolveBrandImageUrl } from "@/lib/data/public";

/**
 * Redirects to a fresh signed Airtable attachment URL for a brand image.
 * `?kind=logo|feature` — allowlisted fields only.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ typeId: string }> }
) {
  const { typeId } = await params;
  const kind = new URL(request.url).searchParams.get("kind");
  if (!/^rec[a-zA-Z0-9]{14}$/.test(typeId) || (kind !== "logo" && kind !== "feature")) {
    return new NextResponse("Not found", { status: 404 });
  }
  const url = await resolveBrandImageUrl(typeId, kind);
  if (!url) {
    return new NextResponse("Not found", { status: 404 });
  }
  return NextResponse.redirect(url, {
    headers: { "Cache-Control": "public, max-age=300" },
  });
}
