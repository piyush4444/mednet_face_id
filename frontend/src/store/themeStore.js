import { useSyncExternalStore } from "react";

// Theme mode ("light" | "dark") shared app-wide, same external-store pattern
// as connectionStore. Persisted to localStorage and mirrored onto
// <html class="dark"> so Tailwind's `dark:` variant and the CSS-variable
// overrides in index.css apply. index.html applies the class pre-paint;
// this store takes over once React boots.

const STORAGE_KEY = "iris-theme";

function readInitialMode() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage unavailable (private mode) — fall through */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

let mode = readInitialMode();
const listeners = new Set();

function applyToDocument() {
  document.documentElement.classList.toggle("dark", mode === "dark");
}

// Keep the DOM class consistent even if the pre-paint script and the
// stored preference somehow disagree.
applyToDocument();

export function setThemeMode(value) {
  if (mode === value) return;
  mode = value;
  applyToDocument();
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function toggleThemeMode() {
  setThemeMode(mode === "dark" ? "light" : "dark");
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return mode;
}

export function useThemeMode() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
