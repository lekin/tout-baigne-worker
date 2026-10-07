/**
 * Unified Discovery audit — `npm run discovery:audit`.
 *
 * Audits every configured public site:
 *   - entity completeness (GEO: organizer/venue relations, entity homes)
 *   - JSON-LD validity (required fields per @type)
 *   - metadata coverage & duplicates (SEO)
 *   - sitemap entries
 *
 * With AIRTABLE_API_KEY/AIRTABLE_BASE_ID set, real public data is audited.
 * Without it, only the registry-derived structure is checked.
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// Load .env.local the way Next does (minimal, no dependency).
const here = dirname(fileURLToPath(import.meta.url));
for (const name of [".env.local", ".env"]) {
  const p = resolve(here, "..", name);
  if (!existsSync(p)) continue;
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m && !process.env[m[1]]) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
}

import { SITES } from "../lib/brands";
import {
  discovery,
  eventPath,
  siteOrigin,
  webPageEntity,
} from "../lib/discovery/entities";
import { getSiteEntities } from "../lib/discovery/data";
import type { AuditIssue, SitemapEntry } from "@platform/discovery";

const hasAirtable = Boolean(
  process.env.AIRTABLE_API_KEY && process.env.AIRTABLE_BASE_ID
);

async function main() {
  const issues: AuditIssue[] = [];

  for (const site of SITES) {
    if (!hasAirtable) {
      console.log(`— ${site.slug}: registry-only audit (no Airtable env)`);
      continue;
    }
    console.log(`— auditing ${site.slug} (${siteOrigin(site)})`);
    try {
      const eco = await getSiteEntities(site);

      // Entity + JSON-LD checks.
      issues.push(...discovery.geo.auditEntities(eco.entities));
      for (const e of eco.entities) {
        issues.push(...discovery.seo.auditEntity(e));
      }

      // Metadata for the site's pages.
      const home = webPageEntity({
        site,
        webSite: eco.webSite,
        path: "/",
        title: eco.publicBrand.seo.title ?? eco.publicBrand.name,
        description: eco.publicBrand.seo.description,
        about: eco.brand,
      });
      const eventsIndex = webPageEntity({
        site,
        webSite: eco.webSite,
        path: "/events",
        title: `Événements — ${eco.webSite.name}`,
        description: `Prochains événements ${eco.webSite.name}.`,
      });
      issues.push(
        ...discovery.seo.auditMetadata([
          { subject: home.url, meta: discovery.seo.metadata(home) },
          { subject: eventsIndex.url, meta: discovery.seo.metadata(eventsIndex) },
          ...eco.events.map((e) => {
            const pub = eco.publicEvents.find((p) => `event:${p.id}` === e.id);
            const page = webPageEntity({
              site,
              webSite: eco.webSite,
              path: new URL(e.url).pathname,
              title: [e.name, pub?.venueCity, pub?.dateLabel]
                .filter(Boolean)
                .join(" — "),
              description: e.description,
              about: e,
            });
            return { subject: e.url, meta: discovery.seo.metadata(page) };
          }),
        ])
      );

      // Sitemap entries.
      const entries: SitemapEntry[] = [
        { url: `${siteOrigin(site)}/`, priority: 1 },
        { url: `${siteOrigin(site)}/events`, priority: 0.9 },
        ...eco.publicEvents.map((e) => ({
          url: `${siteOrigin(site)}${eventPath(e)}`,
        })),
      ];
      issues.push(...discovery.seo.validateSitemap(entries));
    } catch (err) {
      issues.push({
        rule: "audit/site-error",
        severity: "error",
        message: `${site.slug}: ${err instanceof Error ? err.message : String(err)}`,
        subject: site.slug,
      });
    }
  }

  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const infos = issues.filter((i) => i.severity === "info");

  for (const i of issues) {
    const tag =
      i.severity === "error" ? "ERROR" : i.severity === "warning" ? "WARN " : "INFO ";
    console.log(`  [${tag}] ${i.rule} ${i.subject ? `(${i.subject})` : ""} ${i.message}`);
  }
  console.log(
    `\n${errors.length} error(s), ${warnings.length} warning(s), ${infos.length} info(s)`
  );
  if (!hasAirtable) {
    console.log(
      "Set AIRTABLE_API_KEY + AIRTABLE_BASE_ID for a live-data audit."
    );
  }
  process.exit(errors.length ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
