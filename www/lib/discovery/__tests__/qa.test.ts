/**
 * Answer-engine QA — TBP-specific questions asked against the canonical
 * entity ecosystem. The mechanism (`geo.answerEngine`) is generic; these
 * fixtures and questions are TBP-owned.
 */

import { describe, expect, it } from "vitest";
import { answerEngine } from "@platform/discovery";
import {
  brandEntity,
  eventEntity,
  eventBrand,
  siteEcosystem,
  TBP_ORGANIZATION,
  eventSlug,
  eventPostId,
} from "@/lib/discovery/entities";
import type { SiteConfig } from "@/lib/brands";
import type { PublicBrand, PublicEvent } from "@/lib/domain/types";

const globalSite = {
  slug: "tout-baigne",
  domains: ["www.toutbaigneproduction.com"],
  isGlobal: true,
  websiteId: "recO62Zhs9JEI8bJa",
} as SiteConfig;

const chronoSite = {
  slug: "chronologic",
  domains: ["www.chronologic-soiree.com"],
  isGlobal: false,
  websiteId: "recgbuvJwVsm77wnv",
  eventTypeId: "recK5YvAwttrG5eaO",
} as SiteConfig;

const watSite = {
  slug: "we-are-the-90s",
  domains: ["www.wearethe90s.com"],
  isGlobal: false,
  websiteId: "reciKHAhqZZuoUda4",
  eventTypeId: "reccm8TGn7aLyTWK2",
} as SiteConfig;

function brand(name: string, extra: Partial<PublicBrand> = {}): PublicBrand {
  return { id: "recT", slug: name, name, socials: {}, seo: {}, ...extra };
}

function ev(overrides: Partial<PublicEvent>): PublicEvent {
  return {
    id: "recPOST123456789",
    eventId: "recEVT1234567890",
    title: "Chronologic",
    date: "2030-10-02T23:00:00+02:00",
    dateLabel: "MERCREDI 2 OCTOBRE",
    venue: "La Machine du Moulin Rouge",
    city: "Paris",
    venueCity: "La Machine du Moulin Rouge · Paris",
    badgeText: "EN VENTE",
    status: "on_sale",
    ticketUrl: "https://shotgun.live/events/x",
    ...overrides,
  };
}

const chronoBrand = brandEntity(chronoSite, brand("Chronologic"));
const watBrand = brandEntity(
  watSite,
  brand("We Are The 90's", { tagline: "The 90's are back" })
);

const events: PublicEvent[] = [
  ev({ id: "recPOSTAAAAAAAA1", title: "Chronologic" }),
  ev({
    id: "recPOSTBBBBBBB2",
    title: "We Are The 90's",
    venue: "YOYO - Palais de Tokyo",
    city: "Paris",
    venueCity: "YOYO - Palais de Tokyo · Paris",
    date: "2030-11-14T23:00:00+01:00",
  }),
];

const allBrands = [chronoBrand, watBrand];
const eventEntities = events.map((e) =>
  eventEntity(globalSite, e, eventBrand(e, allBrands))
);
const ecosystem = [TBP_ORGANIZATION, ...allBrands, ...eventEntities];
const engine = answerEngine(ecosystem, {
  locale: "fr",
  now: new Date("2030-01-01T00:00:00Z"),
});

describe("event slugs", () => {
  it("produces a stable, resolvable slug", () => {
    const slug = eventSlug(events[0]);
    expect(slug).toBe("chronologic-2030-10-02-la-machine-du-moulin-rouge-recPOSTAAAAAAAA1");
    expect(eventPostId(slug)).toBe("recPOSTAAAAAAAA1");
  });
});

describe("TBP answer-engine QA", () => {
  it("What is Chronologic in Paris?", () => {
    const a = engine.ask("What is Chronologic in Paris?");
    expect(a.entities.map((e) => e.id)).toContain(chronoBrand.id);
    expect(a.answer).toContain("Chronologic");
  });

  it("Who organizes Chronologic?", () => {
    const a = engine.ask("Who organizes Chronologic?");
    expect(a.intent).toBe("who-organizes");
    expect(a.answer).toBe("Tout Baigne Production");
  });

  it("What are the next Tout Baigne events?", () => {
    const a = engine.ask("What are the next Tout Baigne events?");
    expect(a.entities.map((e) => e.id)).toContain("event:recPOSTAAAAAAAA1");
    expect(a.entities.map((e) => e.id)).toContain("event:recPOSTBBBBBBB2");
  });

  it("Where can I dance to 90s music in Paris?", () => {
    const a = engine.ask("Where can I dance to 90s music in Paris?");
    expect(a.entities.map((e) => e.id)).toContain("event:recPOSTBBBBBBB2");
    expect(a.answer).toContain("YOYO");
  });

  it("Which Tout Baigne events take place at La Machine du Moulin Rouge?", () => {
    const a = engine.ask(
      "Which Tout Baigne events take place at La Machine du Moulin Rouge?"
    );
    expect(a.entities.map((e) => e.id)).toContain("event:recPOSTAAAAAAAA1");
    expect(a.entities.map((e) => e.id)).not.toContain("event:recPOSTBBBBBBB2");
  });

  it("Qui organise Chronologic ?", () => {
    const a = engine.ask("Qui organise Chronologic ?");
    expect(a.answer).toBe("Tout Baigne Production");
  });
});

describe("ecosystem wiring", () => {
  it("events on the global site resolve to their real brand", () => {
    expect(eventEntities[0].brand?.id).toBe(chronoBrand.id);
    expect(eventEntities[1].brand?.id).toBe(watBrand.id);
  });

  it("the global site entity graph is complete", () => {
    const eco = siteEcosystem({
      site: globalSite,
      brand: brand("Tout Baigne Production"),
      events,
      allBrands,
    });
    const ids = eco.entities.map((e) => e.id);
    expect(ids).toContain(TBP_ORGANIZATION.id);
    expect(ids).toContain(chronoBrand.id);
    expect(ids).toContain(watBrand.id);
    expect(ids).toContain("event:recPOSTAAAAAAAA1");
  });
});
