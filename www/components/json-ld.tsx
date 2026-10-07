import { jsonLd, type JsonLdGraph, type JsonLdNode } from "@platform/discovery";

/** Embeds a JSON-LD graph. Server component — no client JS. */
export function JsonLd({ data }: { data: JsonLdGraph | JsonLdNode }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: jsonLd(data) }}
    />
  );
}
