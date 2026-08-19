import { useSyncExternalStore } from "react";

let connected = false;
const listeners = new Set();

export function setConnected(value) {
  if (connected === value) return;
  connected = value;
  listeners.forEach((l) => l());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return connected;
}

export function useConnected() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
