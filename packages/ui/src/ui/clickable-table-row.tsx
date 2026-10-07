"use client";

import * as React from "react";
import { cn } from "../cn";
import { TableRow } from "./table";

/**
 * Table row that navigates to `href` on click — the shared pattern used by
 * the live-sales event list. Clicks on nested interactive elements (links,
 * buttons, inputs, tooltips…) are left untouched.
 */
export function ClickableTableRow({
  href,
  className,
  onClick,
  ...props
}: React.ComponentProps<typeof TableRow> & { href: string }) {
  return (
    <TableRow
      className={cn("cursor-pointer", className)}
      onClick={(e) => {
        if (
          (e.target as HTMLElement).closest(
            "a, button, input, select, textarea, [role='button']"
          )
        ) {
          return;
        }
        onClick?.(e);
        if (!e.defaultPrevented) window.location.href = href;
      }}
      {...props}
    />
  );
}
