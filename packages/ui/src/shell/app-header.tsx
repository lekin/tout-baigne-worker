import Link from "next/link";
import type { ReactNode } from "react";

// Shared TBP app header — same shell as Admin: bordered card header,
// max-w-6xl container, brand link on the left, slots on the right.
export function AppHeader({
  title,
  homeHref = "/",
  email,
  children,
  leading,
}: {
  title: string;
  homeHref?: string;
  email?: string;
  /** Extra content rendered to the left of the brand link (e.g. nav menu). */
  leading?: ReactNode;
  /** Right-side slot: org switcher, theme toggle, logout, etc. */
  children?: ReactNode;
}) {
  return (
    <header className="border-b bg-card">
      <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-4">
          {leading}
          <Link href={homeHref} className="font-semibold text-lg">
            {title}
          </Link>
        </div>
        <div className="flex items-center gap-4">
          {email && (
            <div className="text-sm text-muted-foreground hidden sm:block">
              {email}
            </div>
          )}
          {children}
        </div>
      </div>
    </header>
  );
}

export function AppPage({ children }: { children: ReactNode }) {
  return <div className="min-h-screen flex flex-col">{children}</div>;
}

export function AppMain({ children }: { children: ReactNode }) {
  return (
    <main className="flex-1 p-6 max-w-6xl mx-auto w-full">{children}</main>
  );
}
