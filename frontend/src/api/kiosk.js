/**
 * kiosk.js — typed wrappers for the admin Kiosk section (/kiosk-admin/*)
 * plus the retry actions on /integrations/*. All calls carry the session
 * cookie + CSRF (same helper as the rest of the admin app).
 */
import { API_URL } from "../config";

const HEADERS_JSON = {
  "Content-Type": "application/json",
  "ngrok-skip-browser-warning": "true",
};

function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

function mutHeaders() {
  const h = { ...HEADERS_JSON };
  const csrf = getCookie("iris_csrf");
  if (csrf) h["X-CSRF-Token"] = csrf;
  return h;
}

async function jsonOrThrow(res) {
  let body = null;
  try { body = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    throw new Error((body && (body.detail || body.message)) || `HTTP ${res.status}`);
  }
  return body;
}

const get = (path) =>
  fetch(`${API_URL}${path}`, { credentials: "include", headers: HEADERS_JSON }).then(jsonOrThrow);
const send = (path, method, payload) =>
  fetch(`${API_URL}${path}`, {
    method,
    credentials: "include",
    headers: mutHeaders(),
    body: payload ? JSON.stringify(payload) : undefined,
  }).then(jsonOrThrow);

// ── Devices ──────────────────────────────────────────────────────────────
export const listDevices = () => get(`/kiosk-admin/devices`);
export const createDevice = (p) => send(`/kiosk-admin/devices`, "POST", p);
export const patchDevice = (id, p) => send(`/kiosk-admin/devices/${id}`, "PATCH", p);
export const deleteDevice = (id) => send(`/kiosk-admin/devices/${id}`, "DELETE");
export const provisionAccount = (id, p) =>
  send(`/kiosk-admin/devices/${id}/account`, "POST", p);

// ── Logs ───────────────────────────────────────────────────────────────
export function listActivity({ visitType, limit = 100 } = {}) {
  const q = new URLSearchParams();
  if (visitType) q.set("visit_type", visitType);
  q.set("limit", String(limit));
  return get(`/kiosk-admin/activity?${q}`);
}
export function listPreregs({ status, limit = 100 } = {}) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  q.set("limit", String(limit));
  return get(`/kiosk-admin/preregs?${q}`);
}
export function listExports({ status, limit = 100 } = {}) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  q.set("limit", String(limit));
  return get(`/kiosk-admin/exports?${q}`);
}

// ── Retry (existing integrations endpoints) ───────────────────────────────
export const retryPrereg = (id) => send(`/integrations/preregs/${id}/retry`, "POST");
export const retryExport = (id) => send(`/integrations/exports/${id}/retry`, "POST");
