"use client";

import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const STORAGE_KEY = "theme";

let currentTheme: Theme = "system";
let currentResolved: ResolvedTheme = "light";
let initialized = false;
let listenersReady = false;
const listeners = new Set<() => void>();

function systemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      return stored;
    }
  } catch {}
  return "system";
}

function disableTransitionsTemporarily(): () => void {
  const style = document.createElement("style");
  style.appendChild(
    document.createTextNode("*,*::before,*::after{transition:none!important}")
  );
  document.head.appendChild(style);
  return () => {
    window.getComputedStyle(document.body);
    setTimeout(() => document.head.removeChild(style), 1);
  };
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
  root.style.colorScheme = resolved;
}

function emit() {
  for (const listener of listeners) listener();
}

// Reads the stored preference once on the client. The inline script in the
// root layout has already applied the right class before paint.
function ensureInitialized() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  currentTheme = readTheme();
  currentResolved = currentTheme === "system" ? systemTheme() : currentTheme;
}

function ensureListeners() {
  if (listenersReady || typeof window === "undefined") return;
  listenersReady = true;

  // Keep the DOM in sync even if the inline script was bypassed.
  applyTheme(currentResolved);

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      if (currentTheme !== "system") return;
      currentResolved = systemTheme();
      applyTheme(currentResolved);
      emit();
    });

  window.addEventListener("storage", (e) => {
    if (e.key !== STORAGE_KEY) return;
    setTheme(readTheme());
  });
}

function subscribe(callback: () => void): () => void {
  ensureInitialized();
  ensureListeners();
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

function getThemeSnapshot(): Theme {
  ensureInitialized();
  return currentTheme;
}

function getResolvedSnapshot(): ResolvedTheme {
  ensureInitialized();
  return currentResolved;
}

function getServerThemeSnapshot(): Theme {
  return "system";
}

function getServerResolvedSnapshot(): ResolvedTheme {
  return "light";
}

export function setTheme(next: Theme) {
  ensureInitialized();
  currentTheme = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
    // Mirror to a cookie so the server can apply the class on next render.
    document.cookie = `theme=${next};path=/;max-age=31536000;SameSite=Lax`;
  } catch {}
  const resolved = next === "system" ? systemTheme() : next;
  if (resolved !== currentResolved) {
    currentResolved = resolved;
    const restore = disableTransitionsTemporarily();
    applyTheme(resolved);
    restore();
  }
  emit();
}

export function useTheme(): ThemeContextValue {
  const theme = useSyncExternalStore(
    subscribe,
    getThemeSnapshot,
    getServerThemeSnapshot
  );
  const resolvedTheme = useSyncExternalStore(
    subscribe,
    getResolvedSnapshot,
    getServerResolvedSnapshot
  );
  return { theme, resolvedTheme, setTheme };
}

// Kept for API parity: the theme state lives in a module store, so no context
// is needed — the provider is a passthrough.
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return children;
}
