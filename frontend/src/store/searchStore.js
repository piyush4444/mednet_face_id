/**
 * searchStore — shared state for the "find a patient on-camera" feature.
 *
 * The search UI (input + controls) lives in the Header so the feature is
 * reachable from anywhere. The Dashboard consumes the same state to drive
 * camera highlighting and the active-cameras map. Using a shared store
 * keeps the two in sync without prop drilling.
 *
 * Mirrors the useSyncExternalStore pattern already used in
 * connectionStore.js so no new deps are introduced.
 */
import { useSyncExternalStore } from "react";

const initialState = {
  open: false,        // popover visibility in the header
  input: "",          // input box text
  user: null,         // resolved patient { id, name, mrn, lastSeenCamera, lastSeenAt, status }
  state: "idle",      // "idle" | "searching" | "found" | "lost" | "paused"
};

let state = { ...initialState };
const listeners = new Set();

function emit() {
  listeners.forEach((l) => l());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

// ── Actions ──────────────────────────────────────────────────────────────
export function openSearch() {
  if (state.open) return;
  state = { ...state, open: true };
  emit();
}

export function closeSearch() {
  if (!state.open) return;
  state = { ...state, open: false };
  emit();
}

export function toggleSearch() {
  state = { ...state, open: !state.open };
  emit();
}

export function setSearchInput(value) {
  if (state.input === value) return;
  state = { ...state, input: value };
  emit();
}

export function setSearchUser(user) {
  state = { ...state, user };
  emit();
}

export function setSearchState(value) {
  if (state.state === value) return;
  state = { ...state, state: value };
  emit();
}

export function clearSearch() {
  state = { ...initialState, open: state.open };
  emit();
}

// ── Hooks ────────────────────────────────────────────────────────────────
export function useSearchStore() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
