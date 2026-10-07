import type { Metadata } from "next";
import { headers } from "next/headers";
import { siteByHost, DEFAULT_SITE } from "@/lib/brands";
import { getPublicBrand } from "@/lib/data/public";
import {
  discovery,
  siteOrigin,
  webPageEntity,
  webSiteEntity,
} from "@/lib/discovery/entities";

export async function generateMetadata(): Promise<Metadata> {
  const host = (await headers()).get("host") ?? "";
  const site = siteByHost(host) ?? DEFAULT_SITE;
  const brand = await getPublicBrand(site);
  const webSite = webSiteEntity(site, brand);
  const page = webPageEntity({
    site,
    webSite,
    path: "/politique-de-confidentialite",
    title: `Politique de confidentialité — ${brand.name}`,
    description: `Politique de confidentialité de ${brand.name}.`,
  });
  return discovery.seo.nextMetadata(page, {
    metadataBase: siteOrigin(site),
  }) as Metadata;
}

export default function PolitiqueDeConfidentialite() {
  return (
    <main className="legal-content">
      <h1>Politique de confidentialité</h1>
      <p>
        <em>Dernière mise à jour : 21/04/2023</em>
      </p>
      <p>
        La présente politique de confidentialité décrit la manière dont la SARL
        Tout Baigne Production (ci-après &quot;nous&quot;, &quot;notre&quot; ou
        &quot;nos&quot;), au capital social de 500 euros, immatriculée au RCS de
        Paris sous le numéro 521140194, dont le siège social est situé 3 passage
        Rauch 75011 Paris, collecte, utilise et protège les données à caractère
        personnel des utilisateurs (ci-après &quot;vous&quot;,
        &quot;votre&quot; ou &quot;vos&quot;) sur le site web
        toutbaigneproduction.com (ci-après le &quot;Site&quot;). Nous nous
        engageons à respecter la réglementation en vigueur relative à la
        protection des données à caractère personnel, en particulier le
        Règlement (UE) 2016/679 du Règlement Général sur la Protection des
        Données (ci-après « le RGPD »), et la Loi informatique et liberté du 6
        janvier 1978 telle que modifiée ainsi que ses textes d&apos;application
        (ci-après ensemble « la Règlementation Applicable »).
      </p>
      <h2>1. Responsable du traitement des données</h2>
      <p>
        Le responsable du traitement des données à caractère personnel
        collectées sur le Site est la SARL Tout Baigne Production.
      </p>
      <h2>2. Données collectées et finalités</h2>
      <p>
        Nous collectons et traitons les données à caractère personnel que vous
        nous fournissez volontairement lorsque vous vous inscrivez à notre
        newsletter, ou que vous réservez des billets via nos partenaires de
        billetterie. Ces données sont :
      </p>
      <ul>
        <li>Nom</li>
        <li>Prénom</li>
        <li>Civilité</li>
        <li>Adresse email</li>
        <li>Numéro de téléphone mobile (facultatif)</li>
        <li>Date de naissance</li>
        <li>Code postal</li>
        <li>Préférences / choix d&apos;évènements</li>
      </ul>
      <p>
        Les finalités de cette collecte sont : vous communiquer des informations
        commerciales concernant nos événements à venir.
      </p>
      <h2>3. Base légale du traitement</h2>
      <p>
        Le traitement de vos données à caractère personnel repose sur votre
        consentement, conformément à l&apos;article 6.1.a du RGPD.
      </p>
      <h2>4. Destinataires des données</h2>
      <p>
        Les données à caractère personnel que nous collectons sont transmises à
        notre partenaire Sendinblue SASU (7 Rue de Madrid, 75008 Paris, France),
        qui assure le stockage et la gestion de ces données. La société Webflow
        (398 11th Street, 2nd Floor San Francisco, CA 94103, États-Unis),
        hébergeur de notre Site, n&apos;a accès à aucune donnée personnelle.
      </p>
      <h2>5. Durée de conservation des données</h2>
      <p>
        Nous conservons vos données à caractère personnel aussi longtemps que
        vous êtes abonné(e) à notre newsletter. Vous pouvez vous désabonner à
        tout moment en utilisant le lien de désabonnement présent dans chaque
        e-mail.
      </p>
      <h2>6. Vos droits</h2>
      <p>
        Conformément au RGPD, vous disposez des droits suivants sur vos données
        à caractère personnel :
      </p>
      <ul>
        <li>
          Droit d&apos;accès : vous pouvez demander l&apos;accès à vos données
          personnelles.
        </li>
        <li>
          Droit de rectification : vous pouvez demander la correction de vos
          données personnelles inexactes.
        </li>
        <li>
          Droit à l&apos;effacement : vous pouvez demander la suppression de vos
          données personnelles.
        </li>
        <li>
          Droit à la limitation du traitement : vous pouvez demander la
          limitation du traitement de vos données personnelles.
        </li>
        <li>
          Droit à la portabilité des données : vous pouvez demander à recevoir
          vos données personnelles dans un format structuré, couramment utilisé
          et lisible par machine.
        </li>
        <li>
          Droit d&apos;opposition : vous pouvez vous opposer au traitement de
          vos données personnelles.
        </li>
      </ul>
      <p>
        Pour exercer vos droits, veuillez nous contacter à l&apos;adresse e-mail
        suivante : yo@toutbaigneproduction.com. Nous nous engageons à répondre à
        votre demande dans un délai d&apos;un mois, conformément aux
        dispositions du RGPD.
      </p>
      <h2>7. Sécurité des données</h2>
      <p>
        Nous mettons en œuvre des mesures techniques et organisationnelles
        appropriées pour assurer la sécurité de vos données à caractère
        personnel et les protéger contre la destruction, la perte,
        l&apos;altération, la divulgation non autorisée ou l&apos;accès
        accidentel ou illégal.
      </p>
      <h2>8. Transferts de données en dehors de l&apos;UE</h2>
      <p>
        Dans le cadre de l&apos;utilisation de la plateforme Sendinblue, vos
        données à caractère personnel peuvent être transférées en dehors de
        l&apos;Union européenne. Sendinblue garantit la conformité avec le RGPD
        en mettant en place des mesures adéquates pour assurer un niveau de
        protection approprié des données à caractère personnel.
      </p>
      <h2>9. Modifications de la présente politique de confidentialité</h2>
      <p>
        Nous nous réservons le droit de modifier cette politique de
        confidentialité à tout moment. Les modifications seront publiées sur
        cette page avec la date de la dernière mise à jour. Nous vous
        encourageons à consulter régulièrement cette politique de
        confidentialité pour rester informé(e) de notre engagement en matière de
        protection de vos données à caractère personnel.
      </p>
      <h2>10. Contact</h2>
      <p>
        Pour toute question ou demande concernant cette politique de
        confidentialité, veuillez nous contacter à l&apos;adresse e-mail
        suivante : yo@toutbaigneproduction.com.
      </p>
    </main>
  );
}
