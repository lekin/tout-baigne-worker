"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export interface SwitcherOrganization {
  id: string;
  name: string;
}

// Shared org dropdown — writes ?org=<value> and lets the page re-filter.
// valueField chooses what goes in the param: Admin filters Airtable
// promoters by org name; Backstage filters event_access by org id.
export function OrgSwitcher({
  organizations,
  valueField = "id",
}: {
  organizations: SwitcherOrganization[];
  valueField?: "id" | "name";
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const current = searchParams.get("org") ?? "";

  const update = useCallback(
    (value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set("org", value);
      } else {
        params.delete("org");
      }
      const query = params.toString();
      router.push(`${pathname}${query ? `?${query}` : ""}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  if (organizations.length <= 1) return null;

  return (
    <div className="flex items-center gap-2">
      <label
        htmlFor="org-switcher"
        className="text-xs text-muted-foreground hidden sm:inline"
      >
        Org
      </label>
      <select
        id="org-switcher"
        value={current}
        onChange={(e) => update(e.target.value)}
        className="text-sm bg-background border rounded-md px-2 py-1"
      >
        <option value="">All organizations</option>
        {organizations.map((org) => (
          <option key={org.id} value={org[valueField]}>
            {org.name}
          </option>
        ))}
      </select>
    </div>
  );
}
