"use client";

import { useEffect, useState } from "react";

const formatter = new Intl.DateTimeFormat("fr-FR", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Paris",
});

// Live Paris clock for app headers — helps interpret Paris-local sales data.
export function ParisClock() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <span
      className="text-sm text-muted-foreground tabular-nums whitespace-nowrap"
      title="Europe/Paris"
    >
      {now ? `${formatter.format(now)} Paris` : "Paris"}
    </span>
  );
}
