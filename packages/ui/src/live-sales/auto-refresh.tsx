"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Re-renders the surrounding Server Component on an interval — the RPC
// re-runs, so sales data and the "Last updated" timestamp stay fresh,
// matching Admin's 30s poll.
export function LiveSalesAutoRefresh({
  intervalMs = 30_000,
}: {
  intervalMs?: number;
}) {
  const router = useRouter();
  useEffect(() => {
    const interval = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(interval);
  }, [router, intervalMs]);
  return null;
}
