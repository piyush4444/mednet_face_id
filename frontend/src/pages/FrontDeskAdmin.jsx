/**
 * FrontDeskConfig — embeddable panel for managing departments, doctors
 * and rooms. Hosted inside the Settings page (/settings).
 *
 * Deletes are soft (is_active=false) so historic visits stay intact.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "react-toastify";
import {
  listDepartments, createDepartment, patchDepartment, deleteDepartment,
  listRooms, createRoom, patchRoom, deleteRoom,
  listDoctors, createDoctor, patchDoctor, deleteDoctor,
} from "../api/frontdesk";
import CustomSelect from "../components/CustomSelect";

const ROOM_OPTIONS = (rooms) => [
  { value: "", label: "—" },
  ...rooms.map((r) => ({ value: r.id, label: r.name })),
];
const DEPT_OPTIONS = (departments) =>
  departments.map((d) => ({ value: d.id, label: d.name }));

const TABS = [
  { key: "rooms", label: "Rooms" },
  { key: "departments", label: "Departments" },
  { key: "doctors", label: "Doctors" },
];

export default function FrontDeskConfig() {
  const [tab, setTab] = useState("rooms");
  const [departments, setDepartments] = useState([]);
  const [doctors, setDoctors] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [showInactive, setShowInactive] = useState(false);

  const reload = async () => {
    try {
      const [deps, docs, rms] = await Promise.all([
        listDepartments({ includeInactive: showInactive }),
        listDoctors({ includeInactive: showInactive }),
        listRooms({ includeInactive: showInactive }),
      ]);
      setDepartments(deps.departments || []);
      setDoctors(docs.doctors || []);
      setRooms(rms.rooms || []);
    } catch (err) {
      toast.error(err.message);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-4 border-b border-primary/8 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-text-main">Front Desk Configuration</h2>
          <p className="text-xs text-text-muted mt-0.5">
            Departments, doctors and rooms used to generate OPD tokens.
          </p>
        </div>
        <Checkbox
          checked={showInactive}
          onChange={(e) => setShowInactive(e.target.checked)}
          label="Show inactive"
        />
      </div>
      <div className="px-5 pt-3 flex gap-1 border-b border-primary/8">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-semibold rounded-t-lg transition-all ${
              tab === t.key
                ? "text-primary border-b-2 border-primary"
                : "text-text-muted hover:text-text-main"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="p-5">
        {tab === "departments" && (
          <DepartmentsTab
            items={departments}
            rooms={rooms}
            onChange={reload}
          />
        )}
        {tab === "doctors" && (
          <DoctorsTab
            items={doctors}
            departments={departments}
            rooms={rooms}
            onChange={reload}
          />
        )}
        {tab === "rooms" && <RoomsTab items={rooms} onChange={reload} />}
      </div>
    </div>
  );
}

const fieldClass =
  "bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-lg px-3 py-2 text-sm font-semibold outline-none";

// Styled checkbox that matches the rest of the UI.
function Checkbox({ checked, onChange, disabled = false, label, className = "" }) {
  return (
    <label className={`inline-flex items-center gap-2 cursor-pointer select-none ${disabled ? "opacity-60 cursor-not-allowed" : ""} ${className}`}>
      <span className="relative inline-flex">
        <input
          type="checkbox"
          checked={!!checked}
          onChange={onChange}
          disabled={disabled}
          className="peer sr-only"
        />
        <span className={`w-5 h-5 rounded-md border-2 flex items-center justify-center transition-all
          ${checked
            ? "bg-primary border-primary"
            : "bg-background border-primary/25 hover:border-primary/50"}
          peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30`}
        >
          {checked && (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3.5} d="M5 13l4 4L19 7" />
            </svg>
          )}
        </span>
      </span>
      {label && <span className="text-xs font-semibold text-text-muted">{label}</span>}
    </label>
  );
}

// ── Departments ──────────────────────────────────────────────────────────
function DepartmentsTab({ items, rooms, onChange }) {
  const [form, setForm] = useState({
    code: "", name: "", token_prefix: "", default_room_id: "",
  });

  const submit = async () => {
    try {
      await createDepartment({
        code: form.code,
        name: form.name,
        token_prefix: form.token_prefix,
        default_room_id: form.default_room_id ? Number(form.default_room_id) : null,
      });
      toast.success("Department created");
      setForm({ code: "", name: "", token_prefix: "", default_room_id: "" });
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <input className={fieldClass} placeholder="Code (e.g. CARD)"
          value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
        <input className={fieldClass} placeholder="Name (Cardiology)"
          value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input className={fieldClass} placeholder="Token prefix"
          value={form.token_prefix}
          onChange={(e) => setForm({ ...form, token_prefix: e.target.value })} />
        <CustomSelect
          value={form.default_room_id}
          onChange={(e) => setForm({ ...form, default_room_id: e.target.value })}
          placeholder="Default room…"
          options={[{ value: "", label: "Default room…" }, ...rooms.map((r) => ({ value: r.id, label: r.name }))]}
        />
        <button onClick={submit}
          className="bg-primary text-white rounded-lg px-3 py-2 text-sm font-bold hover:opacity-90">
          Add
        </button>
      </div>

      <table className="w-full text-xs">
        <thead className="bg-background">
          <tr className="text-left text-[10px] font-bold text-text-muted uppercase tracking-wider">
            <th className="px-3 py-2">Code</th>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Prefix</th>
            <th className="px-3 py-2">Default Room</th>
            <th className="px-3 py-2">Active</th>
            <th className="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <DepartmentRow key={d.id} dept={d} rooms={rooms} onChange={onChange} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DepartmentRow({ dept, rooms, onChange }) {
  const [edit, setEdit] = useState({
    name: dept.name,
    token_prefix: dept.token_prefix,
    default_room_id: dept.default_room_id ?? "",
    is_active: dept.is_active,
  });
  const dirty =
    edit.name !== dept.name ||
    edit.token_prefix !== dept.token_prefix ||
    (edit.default_room_id || null) !== (dept.default_room_id ?? null) ||
    edit.is_active !== dept.is_active;

  const save = async () => {
    try {
      await patchDepartment(dept.id, {
        name: edit.name,
        token_prefix: edit.token_prefix,
        default_room_id: edit.default_room_id ? Number(edit.default_room_id) : null,
        is_active: edit.is_active,
      });
      toast.success("Saved");
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const remove = async () => {
    if (!confirm(`Deactivate department ${dept.name}?`)) return;
    try {
      await deleteDepartment(dept.id);
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <tr className="border-t border-primary/8">
      <td className="px-3 py-2 font-bold">{dept.code}</td>
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.name}
          onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-20`} value={edit.token_prefix}
          onChange={(e) => setEdit({ ...edit, token_prefix: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <CustomSelect
          value={edit.default_room_id ?? ""}
          onChange={(e) => setEdit({ ...edit, default_room_id: e.target.value })}
          placeholder="—"
          options={ROOM_OPTIONS(rooms)}
        />
      </td>
      <td className="px-3 py-2">
        <Checkbox
          checked={!!edit.is_active}
          onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })}
        />
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap">
        <button onClick={save} disabled={!dirty}
          className={`text-xs px-3 py-1.5 rounded-lg font-semibold mr-2 ${
            dirty ? "bg-primary text-white" : "bg-primary/8 text-text-light cursor-not-allowed"}`}>
          Save
        </button>
        <button onClick={remove}
          className="text-xs px-3 py-1.5 rounded-lg bg-danger/10 text-danger font-semibold">
          Deactivate
        </button>
      </td>
    </tr>
  );
}

// ── Doctors ──────────────────────────────────────────────────────────────
function DoctorsTab({ items, departments, rooms, onChange }) {
  const [form, setForm] = useState({
    name: "", department_id: "", room_id: "", specialty: "",
  });

  const submit = async () => {
    if (!form.name || !form.department_id) {
      toast.warn("Name and department are required.");
      return;
    }
    try {
      await createDoctor({
        name: form.name,
        department_id: Number(form.department_id),
        room_id: form.room_id ? Number(form.room_id) : null,
        specialty: form.specialty || null,
      });
      toast.success("Doctor added");
      setForm({ name: "", department_id: "", room_id: "", specialty: "" });
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const deptById = useMemo(
    () => Object.fromEntries(departments.map((d) => [d.id, d])),
    [departments],
  );
  const roomById = useMemo(
    () => Object.fromEntries(rooms.map((r) => [r.id, r])),
    [rooms],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <input className={fieldClass} placeholder="Name"
          value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <CustomSelect
          value={form.department_id}
          onChange={(e) => setForm({ ...form, department_id: e.target.value })}
          placeholder="Department…"
          options={[{ value: "", label: "Department…" }, ...DEPT_OPTIONS(departments)]}
        />
        <CustomSelect
          value={form.room_id}
          onChange={(e) => setForm({ ...form, room_id: e.target.value })}
          placeholder="Room…"
          options={[{ value: "", label: "Room…" }, ...rooms.map((r) => ({ value: r.id, label: r.name }))]}
        />
        <input className={fieldClass} placeholder="Specialty (optional)"
          value={form.specialty}
          onChange={(e) => setForm({ ...form, specialty: e.target.value })} />
        <button onClick={submit}
          className="bg-primary text-white rounded-lg px-3 py-2 text-sm font-bold hover:opacity-90">
          Add
        </button>
      </div>

      <table className="w-full text-xs">
        <thead className="bg-background">
          <tr className="text-left text-[10px] font-bold text-text-muted uppercase tracking-wider">
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Department</th>
            <th className="px-3 py-2">Room</th>
            <th className="px-3 py-2">Specialty</th>
            <th className="px-3 py-2">Active</th>
            <th className="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <DoctorRow key={d.id} doc={d} departments={departments}
              rooms={rooms} deptById={deptById} roomById={roomById} onChange={onChange} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DoctorRow({ doc, departments, rooms, deptById, roomById, onChange }) {
  const [edit, setEdit] = useState({
    name: doc.name,
    department_id: doc.department_id,
    room_id: doc.room_id ?? "",
    specialty: doc.specialty ?? "",
    is_active: doc.is_active,
  });
  const save = async () => {
    try {
      await patchDoctor(doc.id, {
        name: edit.name,
        department_id: Number(edit.department_id),
        room_id: edit.room_id ? Number(edit.room_id) : null,
        specialty: edit.specialty || null,
        is_active: edit.is_active,
      });
      toast.success("Saved");
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };
  const remove = async () => {
    if (!confirm(`Deactivate ${doc.name}?`)) return;
    try {
      await deleteDoctor(doc.id);
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };
  return (
    <tr className="border-t border-primary/8">
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.name}
          onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <CustomSelect
          value={edit.department_id}
          onChange={(e) => setEdit({ ...edit, department_id: e.target.value })}
          options={DEPT_OPTIONS(departments)}
        />
      </td>
      <td className="px-3 py-2">
        <CustomSelect
          value={edit.room_id ?? ""}
          onChange={(e) => setEdit({ ...edit, room_id: e.target.value })}
          placeholder="—"
          options={ROOM_OPTIONS(rooms)}
        />
      </td>
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.specialty}
          onChange={(e) => setEdit({ ...edit, specialty: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <Checkbox
          checked={!!edit.is_active}
          onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })}
        />
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap">
        <button onClick={save}
          className="text-xs px-3 py-1.5 rounded-lg bg-primary text-white font-semibold mr-2">
          Save
        </button>
        <button onClick={remove}
          className="text-xs px-3 py-1.5 rounded-lg bg-danger/10 text-danger font-semibold">
          Deactivate
        </button>
      </td>
    </tr>
  );
}

// ── Rooms ────────────────────────────────────────────────────────────────
function RoomsTab({ items, onChange }) {
  const [form, setForm] = useState({ name: "", floor: "", description: "" });

  const submit = async () => {
    if (!form.name) {
      toast.warn("Name required.");
      return;
    }
    try {
      await createRoom({
        name: form.name,
        floor: form.floor || null,
        description: form.description || null,
      });
      toast.success("Room added");
      setForm({ name: "", floor: "", description: "" });
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <input className={fieldClass} placeholder="Name (Room 12)"
          value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input className={fieldClass} placeholder="Floor"
          value={form.floor} onChange={(e) => setForm({ ...form, floor: e.target.value })} />
        <input className={fieldClass} placeholder="Description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })} />
        <button onClick={submit}
          className="bg-primary text-white rounded-lg px-3 py-2 text-sm font-bold hover:opacity-90">
          Add
        </button>
      </div>

      <table className="w-full text-xs">
        <thead className="bg-background">
          <tr className="text-left text-[10px] font-bold text-text-muted uppercase tracking-wider">
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Floor</th>
            <th className="px-3 py-2">Description</th>
            <th className="px-3 py-2">Active</th>
            <th className="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => <RoomRow key={r.id} room={r} onChange={onChange} />)}
        </tbody>
      </table>
    </div>
  );
}

function RoomRow({ room, onChange }) {
  const [edit, setEdit] = useState({
    name: room.name,
    floor: room.floor ?? "",
    description: room.description ?? "",
    is_active: room.is_active,
  });
  const save = async () => {
    try {
      await patchRoom(room.id, {
        name: edit.name,
        floor: edit.floor || null,
        description: edit.description || null,
        is_active: edit.is_active,
      });
      toast.success("Saved");
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };
  const remove = async () => {
    if (!confirm(`Deactivate ${room.name}?`)) return;
    try {
      await deleteRoom(room.id);
      onChange();
    } catch (err) {
      toast.error(err.message);
    }
  };
  return (
    <tr className="border-t border-primary/8">
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.name}
          onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.floor}
          onChange={(e) => setEdit({ ...edit, floor: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <input className={`${fieldClass} w-full`} value={edit.description}
          onChange={(e) => setEdit({ ...edit, description: e.target.value })} />
      </td>
      <td className="px-3 py-2">
        <Checkbox
          checked={!!edit.is_active}
          onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })}
        />
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap">
        <button onClick={save}
          className="text-xs px-3 py-1.5 rounded-lg bg-primary text-white font-semibold mr-2">
          Save
        </button>
        <button onClick={remove}
          className="text-xs px-3 py-1.5 rounded-lg bg-danger/10 text-danger font-semibold">
          Deactivate
        </button>
      </td>
    </tr>
  );
}
