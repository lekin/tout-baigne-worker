import Link from "next/link";

export default function NotFound() {
  return (
    <main style={{ textAlign: "center", padding: "120px 20px" }}>
      <h1 className="section-title" style={{ fontSize: 34 }}>
        404
      </h1>
      <p style={{ fontSize: 18 }}>Cette page n&apos;existe pas.</p>
      <p>
        <Link href="/" style={{ color: "#fff" }}>
          ← Retour à l&apos;accueil
        </Link>
      </p>
    </main>
  );
}
