/**
 * Facilities — embeddable panel for managing facilities (hospitals /
 * sites) and their client-HIS integration identifiers. Hosted inside
 * the Settings page (/settings?section=facilities).
 *
 * Deletes are soft (is_active=false) so mappings, tracking logs and
 * pre-registration rows keep their FK valid.
 */
import { useEffect, useState } from "react";
import { toast } from "react-toastify";
import {
  listFacilities,
  createFacility,
  patchFacility,
  deleteFacility,
} from "../api/facilities";

const fieldClass =
  "bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-lg px-3 py-2 text-sm font-semibold outline-none";

// Mirrors the client facility record: FACILITY_GUID, REGN_NUMBER,
// DISPLAY_NAME, CONTACT_NUMBER, ADDRESS, STREET, CITY, STATE, PINCODE,
// PRIMARY_CONTACT_PERSON. `code` is our internal handle; `client_company_id`
// is the partner companyID used in outbound payloads.
const EMPTY_FORM = {
  display_name: "",
  code: "",
  facility_guid: "",
  regn_number: "",
  contact_number: "",
  primary_contact_person: "",
  address: "",
  street: "",
  city: "",
  state: "",
  pin_code: "",
  client_company_id: "",
};

// Fields the API requires on create.
const REQUIRED = [
  ["display_name", "Display name"],
  ["code", "Code"],
  ["facility_guid", "Facility GUID"],
  ["regn_number", "Registration number"],
  ["contact_number", "Contact number"],
];

const NUMERIC = new Set(["regn_number", "client_company_id"]);

// Turn a form state object into the API payload (blank string → omitted,
// numeric fields → Number).
function toPayload(form) {
  const payload = {};
  for (const [k, v] of Object.entries(form)) {
    const trimmed = typeof v === "string" ? v.trim() : v;
    if (trimmed === "" || trimmed === null || trimmed === undefined) continue;
    payload[k] = NUMERIC.has(k) ? Number(trimmed) : trimmed;
  }
  return payload;
}

export default function Facilities() {
  const [facilities, setFacilities] = useState([]);
  const [showInactive, setShowInactive] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState(EMPTY_FORM);

  const reload = async () => {
    try {
      const res = await listFacilities({ includeInactive: showInactive });
      setFacilities(res.facilities || []);
    } catch (err) {
      toast.error(err.message);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);

  const submit = async () => {
    const missing = REQUIRED.filter(([k]) => !String(form[k] ?? "").trim());
    if (missing.length) {
      toast.error(`Required: ${missing.map(([, label]) => label).join(", ")}`);
      return;
    }
    try {
      await createFacility(toPayload(form));
      toast.success("Facility created");
      setForm(EMPTY_FORM);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const startEdit = (f) => {
    setEditingId(f.id);
    setEditForm({
      display_name: f.display_name || "",
      code: f.code || "",
      facility_guid: f.facility_guid || "",
      regn_number: f.regn_number ?? "",
      contact_number: f.contact_number || "",
      primary_contact_person: f.primary_contact_person || "",
      address: f.address || "",
      street: f.street || "",
      city: f.city || "",
      state: f.state || "",
      pin_code: f.pin_code || "",
      client_company_id: f.client_company_id ?? "",
    });
  };

  const saveEdit = async () => {
    try {
      await patchFacility(editingId, toPayload(editForm));
      toast.success("Facility updated");
      setEditingId(null);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const deactivate = async (f) => {
    try {
      await deleteFacility(f.id);
      toast.success(`${f.display_name} deactivated`);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const reactivate = async (f) => {
    try {
      await patchFacility(f.id, { is_active: true });
      toast.success(`${f.display_name} reactivated`);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-4 border-b border-primary/8 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-text-main">Facilities</h2>
          <p className="text-xs text-text-muted mt-0.5">
            Hospitals / sites served by this deployment, with their client
            integration IDs (facility GUID, company ID).
          </p>
        </div>
        <label className="inline-flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="accent-[var(--color-primary,#4f46e5)]"
          />
          <span className="text-xs font-semibold text-text-muted">Show inactive</span>
        </label>
      </div>

      <div className="p-5 flex flex-col gap-4">
        {/* ── Create form ── */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <input className={fieldClass} placeholder="Display name *"
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <input className={fieldClass} placeholder="Code (e.g. MAIN) *"
            value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          <input className={fieldClass} placeholder="Facility GUID *"
            value={form.facility_guid}
            onChange={(e) => setForm({ ...form, facility_guid: e.target.value })} />
          <input className={fieldClass} placeholder="Registration number *" type="number"
            value={form.regn_number}
            onChange={(e) => setForm({ ...form, regn_number: e.target.value })} />
          <input className={fieldClass} placeholder="Contact number *"
            value={form.contact_number}
            onChange={(e) => setForm({ ...form, contact_number: e.target.value })} />
          <input className={fieldClass} placeholder="Primary contact person"
            value={form.primary_contact_person}
            onChange={(e) => setForm({ ...form, primary_contact_person: e.target.value })} />
          <input className={fieldClass} placeholder="Address"
            value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
          <input className={fieldClass} placeholder="Street"
            value={form.street} onChange={(e) => setForm({ ...form, street: e.target.value })} />
          <input className={fieldClass} placeholder="City"
            value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
          <input className={fieldClass} placeholder="State"
            value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} />
          <input className={fieldClass} placeholder="PIN code"
            value={form.pin_code} onChange={(e) => setForm({ ...form, pin_code: e.target.value })} />
          <div className="flex gap-2">
            <input className={`${fieldClass} min-w-0 flex-1`} placeholder="Company ID" type="number"
              value={form.client_company_id}
              onChange={(e) => setForm({ ...form, client_company_id: e.target.value })} />
            <button onClick={submit}
              className="bg-primary text-white rounded-lg px-4 py-2 text-sm font-bold hover:opacity-90 shrink-0">
              Add
            </button>
          </div>
        </div>

        {/* ── List ── */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-text-muted border-b border-primary/8">
                <th className="py-2 pr-3 font-semibold">Name</th>
                <th className="py-2 pr-3 font-semibold">Code</th>
                <th className="py-2 pr-3 font-semibold">City</th>
                <th className="py-2 pr-3 font-semibold">Facility GUID</th>
                <th className="py-2 pr-3 font-semibold">Company ID</th>
                <th className="py-2 pr-3 font-semibold">Status</th>
                <th className="py-2 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {facilities.map((f) =>
                editingId === f.id ? (
                  <tr key={f.id} className="border-b border-primary/5">
                    <td className="py-2 pr-3">
                      <input className={`${fieldClass} w-full`} value={editForm.display_name}
                        onChange={(e) =>
                          setEditForm({ ...editForm, display_name: e.target.value })} />
                    </td>
                    <td className="py-2 pr-3">
                      <input className={`${fieldClass} w-24`} value={editForm.code}
                        onChange={(e) => setEditForm({ ...editForm, code: e.target.value })} />
                    </td>
                    <td className="py-2 pr-3">
                      <input className={`${fieldClass} w-28`} value={editForm.city}
                        onChange={(e) => setEditForm({ ...editForm, city: e.target.value })} />
                    </td>
                    <td className="py-2 pr-3">
                      <input className={`${fieldClass} w-full`} value={editForm.facility_guid}
                        onChange={(e) =>
                          setEditForm({ ...editForm, facility_guid: e.target.value })} />
                    </td>
                    <td className="py-2 pr-3">
                      <input className={`${fieldClass} w-24`} type="number"
                        value={editForm.client_company_id}
                        onChange={(e) =>
                          setEditForm({ ...editForm, client_company_id: e.target.value })} />
                    </td>
                    <td className="py-2 pr-3" />
                    <td className="py-2 text-right whitespace-nowrap">
                      <button onClick={saveEdit}
                        className="text-primary font-bold text-xs px-2 py-1 hover:underline">
                        Save
                      </button>
                      <button onClick={() => setEditingId(null)}
                        className="text-text-muted font-bold text-xs px-2 py-1 hover:underline">
                        Cancel
                      </button>
                    </td>
                  </tr>
                ) : (
                  <tr key={f.id} className="border-b border-primary/5">
                    <td className="py-2.5 pr-3 font-semibold text-text-main">{f.display_name}</td>
                    <td className="py-2.5 pr-3 text-text-muted">{f.code}</td>
                    <td className="py-2.5 pr-3 text-text-muted">{f.city || "—"}</td>
                    <td className="py-2.5 pr-3 text-text-muted font-mono text-xs truncate max-w-[220px]"
                      title={f.facility_guid || ""}>
                      {f.facility_guid || "—"}
                    </td>
                    <td className="py-2.5 pr-3 text-text-muted">{f.client_company_id ?? "—"}</td>
                    <td className="py-2.5 pr-3">
                      <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                        f.is_active
                          ? "bg-success/10 text-success"
                          : "bg-danger/10 text-danger"
                      }`}>
                        {f.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="py-2.5 text-right whitespace-nowrap">
                      <button onClick={() => startEdit(f)}
                        className="text-primary font-bold text-xs px-2 py-1 hover:underline">
                        Edit
                      </button>
                      {f.is_active ? (
                        <button onClick={() => deactivate(f)}
                          className="text-danger font-bold text-xs px-2 py-1 hover:underline">
                          Deactivate
                        </button>
                      ) : (
                        <button onClick={() => reactivate(f)}
                          className="text-success font-bold text-xs px-2 py-1 hover:underline">
                          Reactivate
                        </button>
                      )}
                    </td>
                  </tr>
                ),
              )}
              {facilities.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-sm text-text-muted">
                    No facilities yet — add the first one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
