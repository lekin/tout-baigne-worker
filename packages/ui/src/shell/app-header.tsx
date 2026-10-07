import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "../cn";

// Shared TBP app header — same shell as Admin: bordered card header,
// max-w-6xl container, brand link on the left, slots on the right.
export function AppHeader({
  title,
  homeHref = "/",
  email,
  children,
  leading,
  edge,
  sticky = false,
}: {
  title: string;
  homeHref?: string;
  email?: string;
  /** Extra content rendered to the left of the brand link (e.g. nav menu). */
  leading?: ReactNode;
  /** Right-side slot: org switcher, theme toggle, logout, etc. */
  children?: ReactNode;
  /** Pinned to the right edge of the window, outside the max-w container. */
  edge?: ReactNode;
  /** Pin the header to the top of the viewport while scrolling. */
  sticky?: boolean;
}) {
  return (
    <header
      className={cn(
        "border-b bg-card",
        sticky && "sticky top-0 z-50"
      )}
    >
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
      {edge && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2">
          {edge}
        </div>
      )}
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
