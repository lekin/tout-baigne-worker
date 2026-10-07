# www — sites publics TBP

Une seule app Next.js sert tous les sites publics. Le `Host` détermine la marque
(`proxy.ts` → `app/[brand]/`), les données viennent d'Airtable en direct.

```text
Airtable → lib/data (cache taggé) → lib/domain (PublicEvent/PublicBrand) → BrandShell
```

## Sites servis

| Slug interne | Domaines | Source des events |
|---|---|---|
| `tout-baigne` | toutbaigneproduction.com (+localhost) | `Websites` `recO62Zhs9JEI8bJa` |
| `we-are-the-90s` | wearethe90s.com | `reciKHAhqZZuoUda4` |
| `chronologic` | chronologic-soiree.com | `recgbuvJwVsm77wnv` |
| `la-bug-de-lan-2000` | labugdelan2000.com | `reccj2I2s3Xu0sRci` |

Un event apparaît sur un site s'il a un record `Events Websites Posts` lié au
`Websites` record du site, non draft, non archivé, et à venir. Le badge affiché
vient de la formule `Webflow status` (EN VENTE / Sold out / EN VENTE À PARTIR…).

## Dev

```bash
npm install
cp ../.env .env.local   # ou définir les vars ci-dessous
npm run dev             # http://localhost:3100 (ou 3000)
```

Tester une marque en local : `?brand=chronologic`, `?brand=we-are-the-90s`,
`?brand=la-bug-de-lan-2000` — ou sous-domaine `chronologic.localhost`.

## Env vars

- `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` — lecture Events/Event types/Websites/Posts
- Newsletter (l'une des deux) :
  - `NEWSLETTER_WEBHOOK_URL` — endpoint legacy (ex. Make) recevant
    `{firstName, lastName, email, site, brands}` en POST JSON
  - `NEWSLETTER_AIRTABLE_TABLE` — table Airtable cible (défaut `Newsletter signups`,
    champs : Email, First name, Last name, Site, Brands)
- `BOOKING_FORM_URL` — formulaire Airtable Info Booking

## Caching

Fetchs Airtable : `revalidate: 900s`, tags `site:<slug>`, `site:<slug>:events`,
`brand:<slug>` → `revalidateTag()` pourra rafraîchir via webhook plus tard.

## Images

Les URLs d'attachments Airtable expirent (~2h). Les `<img>` pointent vers
`/api/event-image/[postId]` et `/api/brand-image/[typeId]` qui redirectent vers
une URL signée fraîche. V1 assumée — plus tard : ingest → object storage/CDN.

## Publier un event sur un site

Créer un record `Events Websites Posts` lié à l'event + au `Websites` record du
site (le bouton/Make actuel le fait déjà). Sans draft/archivé → visible au
prochain revalidate.

## Vérifs

`npm run build` · `npm run typecheck` · `npm run lint` · `npm test`
