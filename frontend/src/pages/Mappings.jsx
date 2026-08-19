/**
 * Shelved: per-facility person roles (PERSON_VISIT_MAPPING).
 * Intentionally not imported or routed by App.jsx; retained for future work.
 *
 * The centralized-identity admin: what a person *is* at each facility
 * (PATIENT / EMPLOYEE / DOCTOR / VISITOR / RELATIVE), their per-facility MRN,
 * and visit counter. Gated by users.write.
 */
/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "react-toastify";
import { listFacilities } from "../api/facilities";
import {
  listMappings, createMapping, patchMapping, deleteMapping, searchUsers,
} from "../api/mappings";

const TYPES = ["PATIENT", "EMPLOYEE", "DOCTOR", "VISITOR", "RELATIVE"];
const SUBTYPES = ["", "VENDOR", "MR", "ATTENDANT", "GUEST"];
const field =
  "bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-2 text-sm font-semibold outline-none";
const fmt = (iso) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }); }
  catch { return iso; }
};

export default function Mappings() {
  const [facilities, setFacilities] = useState([]);
  const [rows, setRows] = useState([]);
  const [facilityId, setFacilityId] = useState("");
  const [personType, setPersonType] = useState("");
  const [adding, setAdding] = useState(false);
  const [editId, setEditId] = useState(null);
  const [editForm, setEditForm] = useState({});

  useEffect(() => {
    (async () => {
      try { setFacilities((await listFacilities()).facilities || []); } catch { /* */ }
    })();
  }, []);

  const reload = useCallback(async () => {
    try {
      const res = await listMappings({
        facilityId: facilityId || undefined,
        personType: personType || undefined,
      });
      setRows(res.mappings || []);
    } catch (err) { toast.error(err.message); }
  }, [facilityId, personType]);

  useEffect(() => { reload(); }, [reload]);

  const startEdit = (m) => {
    setEditId(m.id);
    setEditForm({
      person_type: m.person_type, visitor_subtype: m.visitor_subtype || "",
      mrn: m.mrn || "", is_active: m.is_active,
    });
  };
  const saveEdit = async () => {
    try {
      await patchMapping(editId, {
        person_type: editForm.person_type,
        visitor_subtype: editForm.person_type === "VISITOR" ? (editForm.visitor_subtype || null) : null,
        mrn: editForm.mrn || null,
        is_active: editForm.is_active,
      });
      toast.success("Mapping updated");
      setEditId(null);
      reload();
    } catch (err) { toast.error(err.message); }
  };
  const deactivate = async (m) => {
    try { await deleteMapping(m.id); toast.success("Deactivated"); reload(); }
    catch (err) { toast.error(err.message); }
  };

  const facilityOptions = useMemo(
    () => facilities.map((f) => ({ id: f.id, name: f.display_name })),
    [facilities],
  );

  return (
    <div className="max-w-7xl mx-auto w-full flex flex-col gap-4">
      <div className="flex items-center justify-between px-1 flex-wrap gap-2">
        <h1 className="text-xl font-extrabold text-text-main">Facility roles</h1>
        <button onClick={() => setAdding(true)}
          className="bg-primary text-white rounded-lg px-4 py-2 text-sm font-bold hover:opacity-90">
          + Map a person
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <select className={field} value={facilityId} onChange={(e) => setFacilityId(e.target.value)}>
          <option value="">All facilities</option>
          {facilityOptions.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
        <select className={field} value={personType} onChange={(e) => setPersonType(e.target.value)}>
          <option value="">All types</option>
          {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className="bg-card border border-primary/8 rounded-2xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-text-muted border-b border-primary/8">
              <th className="py-2.5 px-4 font-semibold">Person</th>
              <th className="py-2.5 px-3 font-semibold">Facility</th>
              <th className="py-2.5 px-3 font-semibold">Role</th>
              <th className="py-2.5 px-3 font-semibold">MRN</th>
              <th className="py-2.5 px-3 font-semibold">Visits</th>
              <th className="py-2.5 px-3 font-semibold">Last visit</th>
              <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => editId === m.id ? (
              <tr key={m.id} className="border-b border-primary/5 bg-primary/5">
                <td className="py-2 px-4 font-bold text-text-main">{m.person_name}</td>
                <td className="py-2 px-3 text-text-muted">{m.facility_name}</td>
                <td className="py-2 px-3">
                  <div className="flex gap-1">
                    <select className={`${field} py-1`} value={editForm.person_type}
                      onChange={(e) => setEditForm({ ...editForm, person_type: e.target.value })}>
                      {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    {editForm.person_type === "VISITOR" && (
                      <select className={`${field} py-1`} value={editForm.visitor_subtype}
                        onChange={(e) => setEditForm({ ...editForm, visitor_subtype: e.target.value })}>
                        {SUBTYPES.map((s) => <option key={s} value={s}>{s || "—"}</option>)}
                      </select>
                    )}
                  </div>
                </td>
                <td className="py-2 px-3">
                  <input className={`${field} py-1 w-24`} value={editForm.mrn}
                    onChange={(e) => setEditForm({ ...editForm, mrn: e.target.value })} />
                </td>
                <td className="py-2 px-3 text-text-muted">{m.visit_count}</td>
                <td className="py-2 px-3 text-text-muted">{fmt(m.last_visit_at)}</td>
                <td className="py-2 px-3 text-right whitespace-nowrap">
                  <button onClick={saveEdit} className="text-primary font-bold text-xs px-2 hover:underline">Save</button>
                  <button onClick={() => setEditId(null)} className="text-text-muted font-bold text-xs px-2 hover:underline">Cancel</button>
                </td>
              </tr>
            ) : (
              <tr key={m.id} className="border-b border-primary/5">
                <td className="py-2.5 px-4 font-bold text-text-main">
                  {m.person_name}{m.is_active ? "" : <span className="text-danger text-[11px] font-semibold"> · inactive</span>}
                </td>
                <td className="py-2.5 px-3 text-text-muted">{m.facility_name}</td>
                <td className="py-2.5 px-3">
                  <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-primary/8 text-primary">
                    {m.person_type}{m.visitor_subtype ? ` · ${m.visitor_subtype}` : ""}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-text-muted font-mono">{m.mrn || "—"}</td>
                <td className="py-2.5 px-3 text-text-muted">{m.visit_count}</td>
                <td className="py-2.5 px-3 text-text-muted">{fmt(m.last_visit_at)}</td>
                <td className="py-2.5 px-3 text-right whitespace-nowrap">
                  <button onClick={() => startEdit(m)} className="text-primary font-bold text-xs px-2 hover:underline">Edit</button>
                  {m.is_active && <button onClick={() => deactivate(m)} className="text-danger font-bold text-xs px-2 hover:underline">Deactivate</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="py-8 text-center text-text-muted">No mappings.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {adding && (
        <AddMappingModal facilities={facilityOptions}
          onClose={() => setAdding(false)}
          onDone={() => { setAdding(false); reload(); }} />
      )}
    </div>
  );
}

function AddMappingModal({ facilities, onClose, onDone }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [person, setPerson] = useState(null);
  const [facilityId, setFacilityId] = useState(facilities[0]?.id || "");
  const [type, setType] = useState("VISITOR");
  const [subtype, setSubtype] = useState("");
  const [mrn, setMrn] = useState("");
  const [busy, setBusy] = useState(false);

  const doSearch = async () => {
    if (q.trim().length < 2) return;
    try { setResults(await searchUsers(q.trim())); } catch (err) { toast.error(err.message); }
  };

  const submit = async () => {
    if (!person || !facilityId) return toast.error("Pick a person and a facility");
    setBusy(true);
    try {
      await createMapping({
        person_id: person.id, facility_id: Number(facilityId),
        person_type: type,
        visitor_subtype: type === "VISITOR" ? (subtype || null) : null,
        mrn: mrn || null,
      });
      toast.success("Mapping created");
      onDone();
    } catch (err) { toast.error(err.message); setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-card rounded-2xl border border-primary/10 p-6 w-full max-w-md flex flex-col gap-3" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-text-main">Map a person to a facility</h3>
        {!person ? (
          <>
            <div className="flex gap-2">
              <input className={`${field} flex-1`} placeholder="Search person by name / MRN"
                value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()} />
              <button onClick={doSearch} className="bg-primary text-white rounded-lg px-3 text-sm font-bold">Search</button>
            </div>
            <div className="max-h-48 overflow-y-auto flex flex-col gap-1">
              {results.map((u) => (
                <button key={u.id} onClick={() => setPerson(u)}
                  className="text-left px-3 py-2 rounded-lg hover:bg-primary/5 text-sm font-semibold text-text-main">
                  {u.name} <span className="text-text-muted">· {u.user_type}{u.mrn ? ` · ${u.mrn}` : ""}</span>
                </button>
              ))}
              {q.trim().length >= 2 && results.length === 0 && (
                <p className="text-xs text-text-muted px-3 py-2">No matches — press Search.</p>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center justify-between bg-primary/5 rounded-lg px-3 py-2">
              <span className="text-sm font-bold text-text-main">{person.name}</span>
              <button onClick={() => setPerson(null)} className="text-xs text-primary font-bold hover:underline">change</button>
            </div>
            <select className={field} value={facilityId} onChange={(e) => setFacilityId(e.target.value)}>
              {facilities.map((f) => <option key={f.id} value={f.id}>{f.display_name}</option>)}
            </select>
            <div className="grid grid-cols-2 gap-2">
              <select className={field} value={type} onChange={(e) => setType(e.target.value)}>
                {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              {type === "VISITOR" && (
                <select className={field} value={subtype} onChange={(e) => setSubtype(e.target.value)}>
                  {SUBTYPES.map((s) => <option key={s} value={s}>{s || "subtype…"}</option>)}
                </select>
              )}
            </div>
            <input className={field} placeholder="MRN (optional)" value={mrn} onChange={(e) => setMrn(e.target.value)} />
            <div className="flex gap-2 mt-1">
              <button onClick={submit} disabled={busy}
                className="flex-1 bg-primary text-white rounded-lg px-3 py-2.5 text-sm font-bold hover:opacity-90 disabled:opacity-50">
                {busy ? "Creating…" : "Create mapping"}
              </button>
              <button onClick={onClose} className="bg-background border border-primary/20 text-text-muted rounded-lg px-3 py-2.5 text-sm font-bold">Cancel</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
