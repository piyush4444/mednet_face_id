/**
 * mappings.js — person × facility mapping admin (/mappings/*).
 * Session cookie + CSRF on mutations, like the rest of the admin app.
 */
import { API_URL } from "../config";

const JSON_H = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}
function mutH() {
  const h = { ...JSON_H };
  const csrf = getCookie("iris_csrf");
  if (csrf) h["X-CSRF-Token"] = csrf;
  return h;
}
async function orThrow(res) {
  let b = null;
  try { b = await res.json(); } catch { /* */ }
  if (!res.ok) throw new Error((b && (b.detail || b.message)) || `HTTP ${res.status}`);
  return b;
}

export function listMappings({ facilityId, personType, includeInactive = true } = {}) {
  const q = new URLSearchParams();
  if (facilityId) q.set("facility_id", facilityId);
  if (personType) q.set("person_type", personType);
  q.set("include_inactive", String(includeInactive));
  return fetch(`${API_URL}/mappings?${q}`, { credentials: "include", headers: JSON_H }).then(orThrow);
}
export const createMapping = (p) =>
  fetch(`${API_URL}/mappings`, { method: "POST", credentials: "include", headers: mutH(), body: JSON.stringify(p) }).then(orThrow);
export const patchMapping = (id, p) =>
  fetch(`${API_URL}/mappings/${id}`, { method: "PATCH", credentials: "include", headers: mutH(), body: JSON.stringify(p) }).then(orThrow);
export const deleteMapping = (id) =>
  fetch(`${API_URL}/mappings/${id}`, { method: "DELETE", credentials: "include", headers: mutH() }).then(orThrow);

// User search for the "add mapping" person picker (GET /users?q=).
export function searchUsers(q) {
  return fetch(`${API_URL}/users?q=${encodeURIComponent(q)}&limit=10`, {
    credentials: "include", headers: JSON_H,
  }).then(orThrow);
}
