import { NextResponse, type NextRequest } from "next/server";
import { DEFAULT_SITE, siteByHost, siteBySlug } from "@/lib/brands";

/**
 * Resolves the public Host → internal /{brand} route tree.
 * The brand slug stays internal: public URLs are always `/`, `/...` on the
 * brand's own domain (canonical/OG/sitemap use the public host, not the
 * rewritten path).
 *
 * Dev helpers: `?brand=<slug>` override, `*.localhost` subdomains.
 */
export function proxy(request: NextRequest) {
  const host = request.headers.get("host") ?? "";
  const override = request.nextUrl.searchParams.get("brand");
  const site =
    (override ? siteBySlug(override) : undefined) ??
    siteByHost(host) ??
    DEFAULT_SITE;

  const url = request.nextUrl.clone();
  url.searchParams.delete("brand");
  url.pathname = `/${site.slug}${url.pathname === "/" ? "" : url.pathname}`;

  return NextResponse.rewrite(url);
}

export const config = {
  matcher: [
    "/((?!api|_next|sitemap.xml|robots.txt|favicon.ico|.*\\.[a-zA-Z0-9]+$).*)",
  ],
};
