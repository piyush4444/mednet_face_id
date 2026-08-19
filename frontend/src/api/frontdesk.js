/**
 * frontdesk.js — typed wrappers around /api/v1/frontdesk/* endpoints.
 *
 * Centralises fetch boilerplate (base URL, ngrok header, JSON parsing,
 * error throwing) so page components stay focused on UI.
 */
import { API_URL } from "../config";

const HEADERS_JSON = {
  "Content-Type": "application/json",
  "ngrok-skip-browser-warning": "true",
};

async function jsonOrThrow(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    // empty body — fine for 204s
  }
  if (!res.ok) {
    const msg =
      (body && (body.detail || body.message)) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

// ── Scan & visits ────────────────────────────────────────────────────────
export async function scanFrame(blob) {
  const form = new FormData();
  form.append("image", blob, "scan.jpg");
  const res = await fetch(`${API_URL}/frontdesk/scan`, {
    method: "POST",
    body: form,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function createVisit(payload) {
  const res = await fetch(`${API_URL}/frontdesk/visits`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function listTodayVisits({ departmentId, status } = {}) {
  const q = new URLSearchParams();
  if (departmentId) q.set("department_id", departmentId);
  if (status) q.set("status", status);
  const url = `${API_URL}/frontdesk/visits/today${q.toString() ? `?${q}` : ""}`;
  const res = await fetch(url, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function updateVisitStatus(visitId, status) {
  const res = await fetch(`${API_URL}/frontdesk/visits/${visitId}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify({ status }),
  });
  return jsonOrThrow(res);
}

export async function reprintVisit(visitId) {
  const res = await fetch(
    `${API_URL}/frontdesk/visits/${visitId}/reprint`,
    {
      method: "POST",
      headers: HEADERS_JSON,
    },
  );
  return jsonOrThrow(res);
}

export async function searchPatients(query, limit = 10) {
  const q = new URLSearchParams({ q: query, limit: String(limit) });
  const res = await fetch(`${API_URL}/frontdesk/search?${q}`, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function listPatientsByDate(dateIso) {
  const res = await fetch(
    `${API_URL}/frontdesk/patients/by-date/${encodeURIComponent(dateIso)}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

export async function listVisitsByDate(dateIso) {
  const res = await fetch(
    `${API_URL}/frontdesk/visits/by-date/${encodeURIComponent(dateIso)}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

export async function getVisitHistory(visitId) {
  const res = await fetch(`${API_URL}/frontdesk/visits/${visitId}/history`, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function listPatientVisits(patientId, limit = 50) {
  const res = await fetch(
    `${API_URL}/frontdesk/patients/${patientId}/visits?limit=${limit}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

// ── Admin: departments / doctors / rooms ─────────────────────────────────
export async function listDepartments({ includeInactive = false } = {}) {
  const res = await fetch(
    `${API_URL}/frontdesk/admin/departments?include_inactive=${includeInactive}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

export async function createDepartment(payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/departments`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function patchDepartment(id, payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/departments/${id}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function deleteDepartment(id) {
  const res = await fetch(`${API_URL}/frontdesk/admin/departments/${id}`, {
    method: "DELETE",
    headers: HEADERS_JSON,
  });
  return jsonOrThrow(res);
}

export async function listRooms({ includeInactive = false } = {}) {
  const res = await fetch(
    `${API_URL}/frontdesk/admin/rooms?include_inactive=${includeInactive}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

export async function createRoom(payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/rooms`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function patchRoom(id, payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/rooms/${id}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function deleteRoom(id) {
  const res = await fetch(`${API_URL}/frontdesk/admin/rooms/${id}`, {
    method: "DELETE",
    headers: HEADERS_JSON,
  });
  return jsonOrThrow(res);
}

export async function listDoctors({ departmentId, includeInactive = false } = {}) {
  const q = new URLSearchParams();
  if (departmentId) q.set("department_id", departmentId);
  q.set("include_inactive", String(includeInactive));
  const res = await fetch(`${API_URL}/frontdesk/admin/doctors?${q}`, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function createDoctor(payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/doctors`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function patchDoctor(id, payload) {
  const res = await fetch(`${API_URL}/frontdesk/admin/doctors/${id}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function deleteDoctor(id) {
  const res = await fetch(`${API_URL}/frontdesk/admin/doctors/${id}`, {
    method: "DELETE",
    headers: HEADERS_JSON,
  });
  return jsonOrThrow(res);
}
