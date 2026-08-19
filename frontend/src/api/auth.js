/**
 * auth.js — thin client for the /auth/* endpoints.
 *
 * All requests go through the global fetch shim (see main.jsx), so cookies
 * and the CSRF header are attached automatically.
 */
import { API_URL } from "../config";

async function jsonOrThrow(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const detail = body?.detail || `Request failed (${res.status})`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

/** Public — is auth enforcement on? Returns { auth_enabled }. */
export async function getAuthConfig() {
  const res = await fetch(`${API_URL}/auth/config`);
  return jsonOrThrow(res);
}

/** Current session; throws with status 401 when not logged in. */
export async function getMe() {
  const res = await fetch(`${API_URL}/auth/me`);
  return jsonOrThrow(res);
}

export async function login(username, password) {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return jsonOrThrow(res);
}

export async function logout() {
  const res = await fetch(`${API_URL}/auth/logout`, { method: "POST" });
  return jsonOrThrow(res);
}

// ── Account management (needs accounts.manage_staff) ──────────────────────
export async function listAccounts() {
  return jsonOrThrow(await fetch(`${API_URL}/auth/accounts`));
}

export async function createAccount({ username, password, role }) {
  const res = await fetch(`${API_URL}/auth/accounts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, role }),
  });
  return jsonOrThrow(res);
}

export async function updateAccount(id, patch) {
  const res = await fetch(`${API_URL}/auth/accounts/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow(res);
}

export async function patchAccountPermissions(id, { grant = [], revoke = [] }) {
  const res = await fetch(`${API_URL}/auth/accounts/${id}/permissions`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ grant, revoke }),
  });
  return jsonOrThrow(res);
}
