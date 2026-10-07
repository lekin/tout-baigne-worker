import { describe, expect, it } from "vitest";
import { mapEventTypeToPublicBrand } from "../map-brand";
import { eventType } from "./fixtures";

describe("mapEventTypeToPublicBrand", () => {
  it("maps a fully-populated event type", () => {
    const b = mapEventTypeToPublicBrand(eventType(), "chronologic");
    expect(b.name).toBe("Chronologic");
    expect(b.slug).toBe("chronologic");
    expect(b.tagline).toBe("The best dance tracks from every decade");
    expect(b.description).toBe("La time machine musicale");
    expect(b.logoPath).toBe("/api/brand-image/recTYPE000000001?kind=logo");
    expect(b.featureImagePath).toBe(
      "/api/brand-image/recTYPE000000001?kind=feature"
    );
    expect(b.socials.instagram).toBe(
      "https://www.instagram.com/chronologicsoiree"
    );
    expect(b.socials.facebook).toBe(
      "https://www.facebook.com/ChronologicSoiree"
    );
    expect(b.socials.website).toBe("https://www.chronologic-soiree.com/");
    expect(b.seo.title).toBe("Chronologic - soirée • party • paris");
    expect(b.seo.ga4).toBe("G-XXXXXX");
  });

  it("tolerates missing optional fields", () => {
    const b = mapEventTypeToPublicBrand(
      eventType({
        Slogan: undefined,
        "Description (short)": undefined,
        Description: undefined,
        "Square (1:1)": undefined,
        "Linktree thumbnail": undefined,
        "Dice image": undefined,
        Logo: undefined,
        Feature: undefined,
        "Instagram URL": undefined,
        "Facebook URL": undefined,
        "Website URL": undefined,
        "SEO - Title tag": undefined,
        "SEO - Meta description": undefined,
        "GA4 - Measurement ID": undefined,
      }),
      "x"
    );
    expect(b.logoPath).toBeUndefined();
    expect(b.featureImagePath).toBeUndefined();
    expect(b.socials).toEqual({});
    expect(b.seo).toEqual({});
  });
});
