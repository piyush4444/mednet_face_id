/**
 * Locations — embeddable panel for managing LOCATION_MASTER: the named,
 * hierarchical places inside the facility (floor / corridor / room / gate /
 * ward). Hosted inside the Settings page (/settings?section=locations).
 *
 * Add locations and optionally nest them under a parent. The list is
 * rendered as an indented tree by parent chain.
 * Deletes are soft (is_active=false) so tracking logs keep their FK.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "react-toastify";
import {
  listLocations,
  createLocation,
  patchLocation,
  deleteLocation,
} from "../api/facility";
import CustomSelect from "../components/CustomSelect";

const LOCATION_TYPES = ["FLOOR", "CORRIDOR", "ROOM", "GATE", "WARD", "OTHER"];

const fieldClass =
  "bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-lg px-3 py-2 text-sm font-semibold outline-none";

// Order locations as a depth-first tree and tag each with a depth for
// indentation. Orphans (parent filtered out) fall back to root level.
function toTree(locations) {
  const byParent = new Map();
  const ids = new Set(locations.map((l) => l.id));
  for (const loc of locations) {
    const key = loc.parent_location_id && ids.has(loc.parent_location_id)
      ? loc.parent_location_id
      : null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(loc);
  }
  const out = [];
  const walk = (parentKey, depth) => {
    const children = (byParent.get(parentKey) || []).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    for (const child of children) {
      out.push({ ...child, depth });
      walk(child.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

export default function Locations() {
  const [locations, setLocations] = useState([]);
  const [showInactive, setShowInactive] = useState(false);
  const [form, setForm] = useState({
    name: "", location_type: "ROOM", parent_location_id: "", description: "",
  });

  const reload = async () => {
    try {
      const res = await listLocations({ includeInactive: showInactive });
      setLocations(res.locations || []);
    } catch (err) {
      toast.error(err.message);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);

  const tree = useMemo(() => toTree(locations), [locations]);
  const parentOptions = useMemo(
    () => [
      { value: "", label: "— (top level)" },
      ...locations.map((l) => ({ value: l.id, label: `${l.name} (${l.location_type})` })),
    ],
    [locations],
  );

  const submit = async () => {
    if (!form.name.trim()) {
      toast.error("Name is required");
      return;
    }
    try {
      await createLocation({
        name: form.name.trim(),
        location_type: form.location_type,
        parent_location_id: form.parent_location_id
          ? Number(form.parent_location_id)
          : null,
        description: form.description.trim() || null,
      });
      toast.success("Location created");
      setForm({ name: "", location_type: "ROOM", parent_location_id: "", description: "" });
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const deactivate = async (loc) => {
    try {
      await deleteLocation(loc.id);
      toast.success(`${loc.name} deactivated`);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const reactivate = async (loc) => {
    try {
      await patchLocation(loc.id, { is_active: true });
      toast.success(`${loc.name} reactivated`);
      reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-4 border-b border-primary/8 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-bold text-text-main">Locations</h2>
          <p className="text-xs text-text-muted mt-0.5">
            Floors, corridors, rooms and gates inside this facility. Cameras and
            tracking logs reference these.
          </p>
        </div>
        <div className="flex items-center gap-3">
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
      </div>

      <div className="p-5 flex flex-col gap-4">
        {/* Create form */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <input className={fieldClass} placeholder="Name (e.g. Floor 2)"
            value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <CustomSelect
            value={form.location_type}
            onChange={(e) => setForm({ ...form, location_type: e.target.value })}
            options={LOCATION_TYPES.map((t) => ({ value: t, label: t }))}
          />
          <CustomSelect
            value={form.parent_location_id}
            onChange={(e) => setForm({ ...form, parent_location_id: e.target.value })}
            placeholder="Parent…"
            options={parentOptions}
          />
          <input className={fieldClass} placeholder="Description (optional)"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <button onClick={submit}
            className="bg-primary text-white rounded-lg px-3 py-2 text-sm font-bold hover:opacity-90">
            Add location
          </button>
        </div>

        {/* Tree */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-text-muted border-b border-primary/8">
                <th className="py-2 pr-3 font-semibold">Name</th>
                <th className="py-2 pr-3 font-semibold">Type</th>
                <th className="py-2 pr-3 font-semibold">Description</th>
                <th className="py-2 pr-3 font-semibold">Status</th>
                <th className="py-2 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tree.map((loc) => (
                <tr key={loc.id} className="border-b border-primary/5">
                  <td className="py-2.5 pr-3 font-semibold text-text-main">
                    <span style={{ paddingLeft: `${loc.depth * 20}px` }}>
                      {loc.depth > 0 && <span className="text-text-light">└ </span>}
                      {loc.name}
                    </span>
                  </td>
                  <td className="py-2.5 pr-3">
                    <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-primary/8 text-primary">
                      {loc.location_type}
                    </span>
                  </td>
                  <td className="py-2.5 pr-3 text-text-muted">{loc.description || "—"}</td>
                  <td className="py-2.5 pr-3">
                    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                      loc.is_active ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
                    }`}>
                      {loc.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="py-2.5 text-right whitespace-nowrap">
                    {loc.is_active ? (
                      <button onClick={() => deactivate(loc)}
                        className="text-danger font-bold text-xs px-2 py-1 hover:underline">
                        Deactivate
                      </button>
                    ) : (
                      <button onClick={() => reactivate(loc)}
                        className="text-success font-bold text-xs px-2 py-1 hover:underline">
                        Reactivate
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {tree.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-sm text-text-muted">
                    No locations yet — add the first one above.
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
