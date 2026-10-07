import { describe, expect, it } from "vitest";
import { mapPostToPublicEvent, normalizeStatus } from "../map-event";
import { pastDate, post } from "./fixtures";

describe("mapPostToPublicEvent", () => {
  it("maps a fully-populated on-sale post", () => {
    const e = mapPostToPublicEvent(post());
    expect(e).not.toBeNull();
    expect(e!.title).toBe("Chronologic");
    expect(e!.status).toBe("on_sale");
    expect(e!.badgeText).toBe("EN VENTE");
    expect(e!.dateLabel).toBe("SAMEDI 26 SEPTEMBRE");
    expect(e!.venueCity).toBe("YOYO - Palais de Tokyo · Paris");
    expect(e!.ticketUrl).toBe("https://shotgun.live/events/x");
    expect(e!.imagePath).toBe("/api/event-image/recPOST000000001");
  });

  it("flags sold-out posts via badge text", () => {
    const e = mapPostToPublicEvent(
      post({ "Webflow status (from Event)": [" Sold out"] })
    );
    expect(e!.status).toBe("sold_out");
  });

  it("flags sold-out posts via the Sold-out flag", () => {
    const e = mapPostToPublicEvent(
      post({ "Sold-out (from Event)": [true] })
    );
    expect(e!.status).toBe("sold_out");
  });

  it("marks announced events (opening later / soon)", () => {
    for (const badge of [
      "EN VENTE À PARTIR DU 11/09/2026 à 12:00",
      "EN VENTE PROCHAINEMENT",
    ]) {
      const e = mapPostToPublicEvent(
        post({ "Webflow status (from Event)": [badge] })
      );
      expect(e!.status).toBe("announced");
    }
  });

  it("falls back to brand feature image when no specific cover", () => {
    const e = mapPostToPublicEvent(
      post({ "Facebook covers (specific) (from Event)": undefined })
    );
    expect(e!.imagePath).toBe("/api/event-image/recPOST000000001");
  });

  it("omits imagePath when no image at all", () => {
    const e = mapPostToPublicEvent(
      post({
        "Facebook covers (specific) (from Event)": undefined,
        "Feature (from Type) (from Event)": undefined,
      })
    );
    expect(e!.imagePath).toBeUndefined();
  });

  it("rejects archived and draft posts", () => {
    expect(
      mapPostToPublicEvent(post({ "Is Archived (Webflow item)": true }))
    ).toBeNull();
    expect(
      mapPostToPublicEvent(post({ "Is Draft (Webflow item)": true }))
    ).toBeNull();
  });

  it("rejects finished events (TERMINÉ badge or past date)", () => {
    expect(
      mapPostToPublicEvent(
        post({ "Webflow status (from Event)": ["TERMINÉ"] })
      )
    ).toBeNull();
    expect(
      mapPostToPublicEvent(post({ "Date (from Event)": [pastDate] }))
    ).toBeNull();
  });

  it("rejects posts missing essentials (title, date, event link)", () => {
    expect(
      mapPostToPublicEvent(post({ "Name (from Type) (from Event)": undefined }))
    ).toBeNull();
    expect(
      mapPostToPublicEvent(post({ "Date (from Event)": undefined }))
    ).toBeNull();
    expect(mapPostToPublicEvent(post({ Event: undefined }))).toBeNull();
  });

  it("tolerates missing venue/city and optional links", () => {
    const e = mapPostToPublicEvent(
      post({
        "Name (from Venue) (from Event)": undefined,
        "City (from Venue) (from Event)": undefined,
        "Tickets sales URL (from Event)": undefined,
        "Tickets sales URL (from Event) 2": undefined,
        "Facebook Event URL (from Event)": undefined,
      })
    );
    expect(e).not.toBeNull();
    expect(e!.venueCity).toBe("");
    expect(e!.ticketUrl).toBeUndefined();
    expect(e!.facebookUrl).toBeUndefined();
  });
});

describe("normalizeStatus", () => {
  it("maps known badge texts", () => {
    expect(normalizeStatus("EN VENTE")).toBe("on_sale");
    expect(normalizeStatus("🔥 Last chance")).toBe("on_sale");
    expect(normalizeStatus("🚀 Selling fast")).toBe("on_sale");
    expect(normalizeStatus(" Sold out")).toBe("sold_out");
    expect(normalizeStatus("🕒 WAITING LIST")).toBe("sold_out");
    expect(normalizeStatus("EN VENTE PROCHAINEMENT")).toBe("announced");
    expect(normalizeStatus("EN VENTE À PARTIR DU 01/01/2027 à 10:00")).toBe(
      "announced"
    );
  });
});
