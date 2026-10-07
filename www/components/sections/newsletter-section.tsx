import { NewsletterForm } from "@/components/newsletter-form";
import { NEWSLETTER_BRAND_CHOICES } from "@/lib/brands";
import type { PublicBrand } from "@/lib/domain/types";

const HEADING =
  "GARDE L'ÉCOUTE,\nCOÛTE QUE COÛTE, \nQUOI QU'IL EN COÛTE.\n\nInscris-toi à la newsletter.";

export function NewsletterSection({
  brand,
  siteSlug,
  isGlobal,
}: {
  brand: PublicBrand;
  siteSlug: string;
  isGlobal: boolean;
}) {
  const choices = isGlobal
    ? NEWSLETTER_BRAND_CHOICES
    : [{ slug: siteSlug, label: brand.name }];

  return (
    <section id="newsletter" className="newsletter-section pattern-bg">
      <div className="container-3">
        <h1 className="section-title nl-heading">{HEADING}</h1>
        <div className="form-block">
          <NewsletterForm
            siteSlug={siteSlug}
            choices={choices}
            hideChoices={!isGlobal}
          />
          <p className="nl-legal">
            En validant ton inscription, tu acceptes de recevoir les newsletters
            de Tout Baigne Production et/ou d&apos;une ou plusieurs de nos
            soirées. Via ce formulaire d&apos;inscription, tu acceptes que tes
            données soient traitées par la SARL Tout Baigne Production en
            qualité de responsable de traitement. Ces données seront destinées
            exclusivement à t&apos;informer sur les évènements relatifs à nos
            événements, et ne seront transmises à aucun tiers sans ton accord.
            Tu pourras exercer tes droits, notamment d&apos;accès et
            d&apos;effacement, en écrivant à yo@toutbaigneproduction.com. Pour
            en savoir plus, tu peux consulter notre{" "}
            <a href="/politique-de-confidentialite" target="_blank">
              Politique de confidentialité
            </a>
            .
          </p>
        </div>
      </div>
    </section>
  );
}
