import type { AirtableRecord } from "@/lib/airtable/client";

const FUTURE = new Date(Date.now() + 30 * 86400_000).toISOString();
const PAST = new Date(Date.now() - 30 * 86400_000).toISOString();

const ATTACHMENT = {
  id: "attX",
  url: "https://v5.airtableusercontent.com/x.jpg",
  width: 1200,
  height: 628,
};

export function post(overrides: Record<string, unknown> = {}): AirtableRecord {
  return {
    id: "recPOST000000001",
    createdTime: "2026-01-01T00:00:00.000Z",
    fields: {
      Event: ["recEVT0000000001"],
      "Webflow status (from Event)": ["EN VENTE"],
      "Name (from Type) (from Event)": ["Chronologic"],
      "Date (from Event)": [FUTURE],
      "Date (French) (from Event)": ["SAMEDI 26 SEPTEMBRE"],
      "Name (from Venue) (from Event)": ["YOYO - Palais de Tokyo"],
      "City (from Venue) (from Event)": ["Paris"],
      "Tickets sales URL (from Event)": ["https://shotgun.live/events/x"],
      "Facebook Event URL (from Event)": ["https://facebook.com/events/1"],
      "Facebook covers (specific) (from Event)": [ATTACHMENT],
      "Feature (from Type) (from Event)": [ATTACHMENT],
      ...overrides,
    },
  };
}

export function eventType(
  overrides: Record<string, unknown> = {}
): AirtableRecord {
  return {
    id: "recTYPE000000001",
    createdTime: "2026-01-01T00:00:00.000Z",
    fields: {
      Name: "Chronologic",
      Slogan: "The best dance tracks from every decade",
      Description: "La time machine musicale",
      "Square (1:1)": [ATTACHMENT],
      Feature: [ATTACHMENT],
      "Instagram URL": "https://www.instagram.com/chronologicsoiree",
      "Facebook URL": "https://www.facebook.com/ChronologicSoiree",
      "Website URL": "https://www.chronologic-soiree.com/",
      "SEO - Title tag": "Chronologic - soirée • party • paris",
      "SEO - Meta description": "Une time machine musicale",
      "GA4 - Measurement ID": "G-XXXXXX",
      ...overrides,
    },
  };
}

export const futureDate = FUTURE;
export const pastDate = PAST;
export const attachment = ATTACHMENT;
