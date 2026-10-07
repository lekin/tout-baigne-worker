import {
  factLabel,
  type EntityRepresentation,
} from "@platform/discovery";

function isUrl(value: string): boolean {
  return /^https?:\/\//.test(value);
}

/** Render ISO datetimes as French dates; other values verbatim. */
function displayValue(predicate: string, value: string): string {
  if (
    (predicate === "start-date" || predicate === "end-date") &&
    !Number.isNaN(Date.parse(value))
  ) {
    return new Intl.DateTimeFormat("fr", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: "Europe/Paris",
    }).format(new Date(value));
  }
  return value;
}

/**
 * Explicit machine-readable facts about an entity — the same canonical facts
 * that feed JSON-LD and the answer engine, visible in semantic HTML so the
 * page never claims more than it shows.
 */
export function EntityFacts({
  rep,
  title,
}: {
  rep: EntityRepresentation;
  title?: string;
}) {
  const rows = rep.facts.filter((f) => f.predicate !== "name");
  return (
    <section className="entity-facts" aria-label="Informations">
      <div className="container-3">
        {title ? <h2 className="entity-facts-title">{title}</h2> : null}
        {rep.statements.map((s, i) => (
          <p key={i} className="entity-facts-statement">
            {s}
          </p>
        ))}
        <dl className="entity-facts-list">
          {rows.map((f, i) => (
            <div key={i} className="entity-fact">
              <dt>{factLabel(f.predicate, "fr")}</dt>
              <dd>
                {isUrl(f.value) ? (
                  <a href={f.value} target="_blank" rel="noopener noreferrer">
                    {f.value}
                  </a>
                ) : (
                  displayValue(f.predicate, f.value)
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
