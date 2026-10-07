import type { PublicBrand } from "@/lib/domain/types";

/**
 * Renders the Airtable `Description` field: paragraphs split on blank lines,
 * newlines → <br/>, `**bold**` → <strong>, [label](url) → <a>.
 * Plain text only — no HTML is injected.
 */
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      parts.push(<strong key={`${keyPrefix}-b${i}`}>{tok.slice(2, -2)}</strong>);
    } else {
      const link = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok);
      if (link) {
        parts.push(
          <a
            key={`${keyPrefix}-l${i}`}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer"
          >
            <strong>{link[1]}</strong>
          </a>
        );
      }
    }
    i += 1;
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function IntroSection({ brand }: { brand: PublicBrand }) {
  if (!brand.description) return null;
  const paragraphs = brand.description
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  return (
    <section className="introduction">
      <div className="container-3">
        <div className="intro-text">
          {paragraphs.map((p, i) => (
            <p key={i} className="intro-paragraph">
              {p.split("\n").flatMap((line, j) => [
                ...(j > 0 ? [<br key={`br-${i}-${j}`} />] : []),
                ...renderInline(line, `${i}-${j}`),
              ])}
            </p>
          ))}
        </div>
      </div>
    </section>
  );
}
