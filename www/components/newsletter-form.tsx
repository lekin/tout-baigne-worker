"use client";

import { useState } from "react";

interface BrandChoice {
  slug: string;
  label: string;
}

export function NewsletterForm({
  siteSlug,
  choices,
  hideChoices = false,
}: {
  siteSlug: string;
  choices: BrandChoice[];
  /** Brand sites: checkbox hidden but checked, like the Webflow export. */
  hideChoices?: boolean;
}) {
  const [state, setState] = useState<"idle" | "sending" | "done" | "error">(
    "idle"
  );

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const payload = {
      firstName: String(data.get("firstName") ?? "").trim(),
      lastName: String(data.get("lastName") ?? "").trim(),
      email: String(data.get("email") ?? "").trim(),
      brands: data.getAll("brands").map(String),
      site: siteSlug,
      company: String(data.get("company") ?? ""), // honeypot
    };
    setState("sending");
    try {
      const res = await fetch("/api/newsletter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setState(res.ok ? "done" : "error");
      if (res.ok) form.reset();
    } catch {
      setState("error");
    }
  }

  if (state === "done") {
    return <p style={{ fontWeight: 700 }}>Merci pour ton inscription !</p>;
  }

  return (
    <form onSubmit={onSubmit} className="nl-form">
      <label htmlFor="nl-firstname" className="nl-label">
        Prénom
      </label>
      <input
        id="nl-firstname"
        name="firstName"
        type="text"
        required
        maxLength={256}
        className="nl-input"
      />
      <label htmlFor="nl-lastname" className="nl-label">
        Nom
      </label>
      <input
        id="nl-lastname"
        name="lastName"
        type="text"
        required
        maxLength={256}
        className="nl-input"
      />
      <label htmlFor="nl-email" className="nl-label">
        Adresse email
      </label>
      <input
        id="nl-email"
        name="email"
        type="email"
        required
        maxLength={256}
        className="nl-input"
      />

      {hideChoices
        ? choices.map((choice) => (
            <input
              key={choice.slug}
              type="hidden"
              name="brands"
              value={choice.slug}
            />
          ))
        : null}
      {!hideChoices && choices.length > 0 ? (
        <div className="nl-choices">
          {choices.map((choice, i) => (
            <label
              key={choice.slug}
              className="nl-checkbox"
              style={i > 0 ? { display: "none" } : undefined}
            >
              <input type="checkbox" name="brands" value={choice.slug} />
              <span>
                {i === 0 ? "J'accepte de recevoir" : "Recevoir"} des nouvelles
                des soirées{" "}
                <strong className="nl-highlight">{choice.label}</strong>
              </span>
            </label>
          ))}
        </div>
      ) : null}

      {/* honeypot — invisible to humans */}
      <div className="nl-honeypot" aria-hidden="true">
        <label htmlFor="nl-company">Company</label>
        <input id="nl-company" name="company" type="text" tabIndex={-1} autoComplete="off" />
      </div>

      <button type="submit" disabled={state === "sending"} className="nl-submit">
        {state === "sending" ? "…" : "Go!"}
      </button>
      {state === "error" ? (
        <p style={{ fontWeight: 700 }}>
          Oops ! Une erreur est survenue, réessaie dans un instant.
        </p>
      ) : null}
    </form>
  );
}
