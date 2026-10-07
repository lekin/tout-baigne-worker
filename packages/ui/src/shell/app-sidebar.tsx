"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import { Tooltip } from "@base-ui/react/tooltip";
import { Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { cn } from "../cn";

export interface SidebarNavItem {
  href: string;
  label: string;
  icon?: ReactNode;
  /** Colored dot signaling something new on this item (unread, alert…). */
  indicator?: "info" | "warning" | "critical";
  /** Rich hover flyout rendered next to the item (unread previews…). Shows
   *  in expanded mode too, and replaces the label tooltip when collapsed. */
  preview?: ReactNode;
}

export interface SidebarNavSection {
  title: string;
  items: SidebarNavItem[];
}

interface SidebarContextValue {
  collapsed: boolean;
  toggleCollapsed: () => void;
  mobileOpen: boolean;
  setMobileOpen: (open: boolean) => void;
}

const INDICATOR_TONE = {
  info: "bg-blue-500",
  warning: "bg-amber-500",
  critical: "bg-red-500",
} as const;

const SidebarContext = createContext<SidebarContextValue | null>(null);

export function useSidebar() {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar must be used within SidebarProvider");
  return ctx;
}

function isCollapsedByDefault(pathname: string, paths: string[]) {
  return paths.some((p) =>
    p === "/"
      ? pathname === "/"
      : pathname === p || pathname.startsWith(`${p}/`)
  );
}

export function SidebarProvider({
  initialCollapsed,
  defaultCollapsedPaths = [],
  cookieName,
  children,
}: {
  /** Saved preference read server-side from `cookieName`. `null` = no saved
   *  choice → the per-route default applies until the user toggles. */
  initialCollapsed: boolean | null;
  /** Routes where the sidebar starts collapsed (e.g. live-sales pages). */
  defaultCollapsedPaths?: string[];
  /** Persist explicit toggle choices here — use a per-app cookie name
   *  (localhost cookies are shared across ports). */
  cookieName?: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [explicit, setExplicit] = useState<boolean | null>(initialCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);

  const collapsed =
    explicit ?? isCollapsedByDefault(pathname, defaultCollapsedPaths);

  const toggleCollapsed = useCallback(() => {
    const next = !collapsed;
    setExplicit(next);
    if (cookieName) {
      document.cookie = `${cookieName}=${next ? "1" : "0"}; path=/; max-age=31536000; samesite=lax`;
    }
  }, [collapsed, cookieName]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "b") {
        e.preventDefault();
        toggleCollapsed();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [toggleCollapsed]);

  const value = useMemo(
    () => ({ collapsed, toggleCollapsed, mobileOpen, setMobileOpen }),
    [collapsed, toggleCollapsed, mobileOpen]
  );

  return (
    <SidebarContext.Provider value={value}>
      {/* Exposes the current sidebar width so viewport-relative content (e.g.
          full-bleed tables) can size against the space actually left to it.
          display:contents keeps this purely a CSS-var scope, and the md:
          variant leaves --sidebar-w unset on mobile where the rail is hidden. */}
      <div
        className={cn(
          "contents",
          collapsed ? "md:[--sidebar-w:3.5rem]" : "md:[--sidebar-w:15rem]"
        )}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

export function SidebarTrigger({ className }: { className?: string }) {
  const { setMobileOpen } = useSidebar();
  return (
    <button
      type="button"
      aria-label="Open menu"
      onClick={() => setMobileOpen(true)}
      className={cn(
        "inline-flex size-9 items-center justify-center rounded-md transition-colors hover:bg-muted md:hidden",
        className
      )}
    >
      <Menu className="size-5" />
    </button>
  );
}

export function AppSidebar({
  title,
  homeHref = "/",
  sections,
  footer,
}: {
  title: string;
  homeHref?: string;
  sections: SidebarNavSection[];
  footer?: ReactNode;
}) {
  const { collapsed, mobileOpen, setMobileOpen } = useSidebar();

  return (
    <>
      <aside
        className={cn(
          // z-60 keeps the menu above page content — the live-sales table is
          // viewport-bleed and would otherwise paint over the open sidebar.
          "sticky top-0 left-0 z-[60] hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-in-out md:flex",
          collapsed ? "w-14" : "w-60"
        )}
      >
        <SidebarContent
          title={title}
          homeHref={homeHref}
          sections={sections}
          footer={footer}
          collapsed={collapsed}
        />
      </aside>

      <Dialog.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-[70] bg-black/50 transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 md:hidden" />
          <Dialog.Popup className="fixed inset-y-0 left-0 z-[80] flex w-72 max-w-[85vw] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground shadow-xl transition-transform duration-200 ease-out data-[ending-style]:-translate-x-full data-[starting-style]:-translate-x-full md:hidden">
            <Dialog.Title className="sr-only">{title}</Dialog.Title>
            <SidebarContent
              title={title}
              homeHref={homeHref}
              sections={sections}
              footer={footer}
              collapsed={false}
              onNavigate={() => setMobileOpen(false)}
              mobile
            />
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}

function SidebarContent({
  title,
  homeHref,
  sections,
  footer,
  collapsed,
  onNavigate,
  mobile = false,
}: {
  title: string;
  homeHref: string;
  sections: SidebarNavSection[];
  footer?: ReactNode;
  collapsed: boolean;
  onNavigate?: () => void;
  mobile?: boolean;
}) {
  const { toggleCollapsed } = useSidebar();

  return (
    <>
      <div
        className={cn(
          "flex h-14 shrink-0 items-center border-b border-sidebar-border",
          collapsed ? "justify-center" : "justify-between gap-2 px-4"
        )}
      >
        {!collapsed && (
          <Link
            href={homeHref}
            onClick={onNavigate}
            className="truncate font-semibold text-lg"
          >
            {title}
          </Link>
        )}
        {mobile ? (
          <Dialog.Close
            aria-label="Close menu"
            className="inline-flex size-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          >
            <X className="size-5" />
          </Dialog.Close>
        ) : (
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          >
            {collapsed ? (
              <PanelLeftOpen className="size-4" />
            ) : (
              <PanelLeftClose className="size-4" />
            )}
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto p-2">
        <Tooltip.Provider delay={200}>
          {sections.map((section, i) => (
            <div key={section.title}>
              {i > 0 && (
                <div
                  className={cn(
                    "my-2 border-t border-sidebar-border",
                    collapsed ? "mx-1" : "mx-2"
                  )}
                />
              )}
              {!collapsed && (
                <div className="px-2 pb-1.5 pt-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {section.title}
                </div>
              )}
              <ul className="space-y-0.5">
                {section.items.map((item) => (
                  <SidebarLink
                    key={item.href}
                    item={item}
                    collapsed={collapsed}
                    onNavigate={onNavigate}
                  />
                ))}
              </ul>
            </div>
          ))}
        </Tooltip.Provider>
      </nav>

      {footer && (
        <div className="shrink-0 border-t border-sidebar-border p-2">
          {footer}
        </div>
      )}
    </>
  );
}

function SidebarLink({
  item,
  collapsed,
  onNavigate,
}: {
  item: SidebarNavItem;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const active =
    item.href === "/"
      ? pathname === "/"
      : pathname === item.href || pathname.startsWith(`${item.href}/`);

  const link = (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? item.label : undefined}
      className={cn(
        "relative flex items-center rounded-md text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-sidebar-ring [&_svg]:size-4 [&_svg]:shrink-0",
        collapsed ? "mx-auto size-9 justify-center" : "gap-2.5 px-2.5 py-2",
        active
          ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
          : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
      )}
    >
      {item.icon}
      {!collapsed && <span className="truncate">{item.label}</span>}
      {item.indicator && (
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            INDICATOR_TONE[item.indicator],
            collapsed ? "absolute right-1 top-1" : "ml-auto"
          )}
        />
      )}
    </Link>
  );

  if (!collapsed && !item.preview) return <li>{link}</li>;

  return (
    <li>
      <Tooltip.Root>
        <Tooltip.Trigger render={link} />
        <Tooltip.Portal>
          <Tooltip.Positioner side="right" sideOffset={10} className="z-50">
            {item.preview ? (
              // The popup is hoverable (Base UI default) so links inside the
              // preview stay clickable; the preview carries its own chrome.
              <Tooltip.Popup className="w-72">{item.preview}</Tooltip.Popup>
            ) : (
              <Tooltip.Popup className="rounded-md bg-popover px-2.5 py-1.5 text-xs font-medium text-popover-foreground shadow-md ring-1 ring-foreground/10">
                {item.label}
              </Tooltip.Popup>
            )}
          </Tooltip.Positioner>
        </Tooltip.Portal>
      </Tooltip.Root>
    </li>
  );
}
