/**
 * facilities.js — typed wrappers around /api/v1/facilities endpoints.
 *
 * Facilities are the hospitals / sites this deployment serves; each row
 * carries the client-HIS identifiers (facilityGuid, companyID) used by
 * the outbound integrations.
 */
import { API_URL } from "../config";

const HEADERS_JSON = {
  "Content-Type": "application/json",
  "ngrok-skip-browser-warning": "true",
};

// FastAPI reports validation failures as `detail: [{loc, msg, type}, ...]`.
// Stringifying that array yields "[object Object]" — flatten it to
// "field: message" lines instead.
function formatDetail(detail) {
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = Array.isArray(e.loc)
          ? e.loc.filter((p) => p !== "body").join(".")
          : "";
        return field ? `${field}: ${e.msg}` : e.msg;
      })
      .join("; ");
  }
  return null;
}

async function jsonOrThrow(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    // empty body — fine for 204s
  }
  if (!res.ok) {
    const msg =
      (body && (formatDetail(body.detail) || body.message)) ||
      `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

export async function listFacilities({ includeInactive = false } = {}) {
  const res = await fetch(
    `${API_URL}/facilities?include_inactive=${includeInactive}`,
    { headers: { "ngrok-skip-browser-warning": "true" } },
  );
  return jsonOrThrow(res);
}

export async function createFacility(payload) {
  const res = await fetch(`${API_URL}/facilities`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function patchFacility(id, payload) {
  const res = await fetch(`${API_URL}/facilities/${id}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function deleteFacility(id) {
  const res = await fetch(`${API_URL}/facilities/${id}`, {
    method: "DELETE",
    headers: HEADERS_JSON,
  });
  return jsonOrThrow(res);
}

// ── Locations (LOCATION_MASTER) ──────────────────────────────────────────
export async function listLocations({ facilityId, includeInactive = false } = {}) {
  const q = new URLSearchParams();
  if (facilityId) q.set("facility_id", facilityId);
  q.set("include_inactive", String(includeInactive));
  const res = await fetch(`${API_URL}/locations?${q}`, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return jsonOrThrow(res);
}

export async function createLocation(payload) {
  const res = await fetch(`${API_URL}/locations`, {
    method: "POST",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function patchLocation(id, payload) {
  const res = await fetch(`${API_URL}/locations/${id}`, {
    method: "PATCH",
    headers: HEADERS_JSON,
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

export async function deleteLocation(id) {
  const res = await fetch(`${API_URL}/locations/${id}`, {
    method: "DELETE",
    headers: HEADERS_JSON,
  });
  return jsonOrThrow(res);
}
