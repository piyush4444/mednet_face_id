import { API_URL } from "../config";

async function jsonOrThrow(res) {
  if (!(res instanceof Response)) {
    throw new Error("Attendance API returned an invalid browser response");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty response */
  }
  if (!res.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("; ")
      : detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return body;
}

export const getAttendanceConfig = async () =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/config`));

export const saveAttendanceConfig = async (payload) =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));

export const getAttendanceDashboard = async (date) =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/dashboard?date=${encodeURIComponent(date)}`));

export const getAttendanceObservations = async (date) =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/observations?date=${encodeURIComponent(date)}&limit=100`));

export const getPunchQueue = async () =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/queue?limit=100`));

export const retryPunch = async (id) =>
  jsonOrThrow(await fetch(`${API_URL}/attendance/queue/${id}/retry`, { method: "POST" }));
