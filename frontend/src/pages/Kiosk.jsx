/**
 * Kiosk — the one complete admin section for kiosks: central device config
 * and all kiosk logs (activity / pre-registrations / attendance exports).
 * Gated by kiosk.manage.
 */
/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useState } from "react";
import { toast } from "react-toastify";
import { API_URL } from "../config";
import {
  listDevices, createDevice, patchDevice, deleteDevice, provisionAccount,
  listActivity, listPreregs, listExports, retryPrereg, retryExport,
} from "../api/kiosk";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "devices", label: "Devices" },
  { key: "activity", label: "Activity" },
  { key: "preregs", label: "Pre-registrations" },
  { key: "exports", label: "Attendance exports" },
];

const field =
  "bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-2 text-sm font-semibold outline-none";
const fmt = (iso) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }); }
  catch { return iso; }
};
const StatusPill = ({ s }) => {
  const map = {
    SENT: "bg-success/10 text-success", PENDING: "bg-warning/10 text-warning",
    SENDING: "bg-primary/10 text-primary", FAILED: "bg-danger/10 text-danger",
  };
  return <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${map[s] || "bg-primary/5 text-text-muted"}`}>{s}</span>;
};

export default function Kiosk() {
  const [tab, setTab] = useState("overview");
  return (
    <div className="max-w-7xl mx-auto w-full flex flex-col gap-4">
      <div className="flex items-center justify-between px-1">
        <h1 className="text-xl font-extrabold text-text-main">Kiosk</h1>
      </div>
      <div className="flex gap-1 border-b border-primary/8">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-semibold rounded-t-lg transition-all ${
              tab === t.key ? "text-primary border-b-2 border-primary" : "text-text-muted hover:text-text-main"
            }`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview />}
      {tab === "devices" && <Devices />}
      {tab === "activity" && <Activity />}
      {tab === "preregs" && <Preregs />}
      {tab === "exports" && <Exports />}
    </div>
  );
}

// ── Overview ─────────────────────────────────────────────────────────────
function Overview() {
  const [stats, setStats] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const [d, a, p, e] = await Promise.all([
          listDevices(), listActivity({ limit: 500 }),
          listPreregs({ limit: 500 }), listExports({ limit: 500 }),
        ]);
        const today = new Date().toDateString();
        const isToday = (r) => r.event_time && new Date(r.event_time).toDateString() === today;
        setStats({
          activeDevices: (d.devices || []).filter((x) => x.is_active).length,
          punchesToday: (a.activity || []).filter(isToday).length,
          preregsPending: (p.preregs || []).filter((x) => x.status !== "SENT").length,
          exportsFailed: (e.exports || []).filter((x) => x.status === "FAILED").length,
        });
      } catch (err) { toast.error(err.message); }
    })();
  }, []);
  const cards = [
    { label: "Active kiosks", value: stats?.activeDevices },
    { label: "Punches today", value: stats?.punchesToday },
    { label: "Pre-regs pending", value: stats?.preregsPending },
    { label: "Exports failed", value: stats?.exportsFailed, danger: true },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-card border border-primary/8 rounded-2xl p-5">
          <p className="text-xs font-semibold text-text-muted">{c.label}</p>
          <p className={`text-3xl font-extrabold mt-1 ${c.danger && c.value ? "text-danger" : "text-text-main"}`}>
            {c.value ?? "—"}
          </p>
        </div>
      ))}
    </div>
  );
}

// ── Devices ──────────────────────────────────────────────────────────────
const EMPTY_DEVICE = { serial: "", name: "", mode: "AUTO", source_type: "webcam", camera_id: "", dup_window: "" };

function Devices() {
  const [devices, setDevices] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [form, setForm] = useState(EMPTY_DEVICE);
  const [acctFor, setAcctFor] = useState(null); // device id being provisioned

  const reload = useCallback(async () => {
    try { setDevices((await listDevices()).devices || []); }
    catch (err) { toast.error(err.message); }
  }, []);

  useEffect(() => {
    reload();
    (async () => {
      try {
        const res = await fetch(`${API_URL}/cameras`, { credentials: "include", headers: { "ngrok-skip-browser-warning": "true" } });
        if (res.ok) setCameras(await res.json());
      } catch { /* */ }
    })();
  }, [reload]);

  const submit = async () => {
    if (!form.serial.trim() || !form.name.trim()) return toast.error("Serial and name are required");
    try {
      await createDevice({
        serial: form.serial.trim(), name: form.name.trim(),
        mode: form.mode, source_type: form.source_type,
        camera_id: form.source_type === "system" ? (form.camera_id || null) : null,
        dup_window: form.dup_window ? Number(form.dup_window) : null,
      });
      toast.success("Kiosk registered");
      setForm(EMPTY_DEVICE);
      reload();
    } catch (err) { toast.error(err.message); }
  };

  const toggle = async (d) => {
    try { await patchDevice(d.id, { is_active: !d.is_active }); reload(); }
    catch (err) { toast.error(err.message); }
  };
  const remove = async (d) => {
    try { await deleteDevice(d.id); toast.success("Deactivated"); reload(); }
    catch (err) { toast.error(err.message); }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Register form */}
      <div className="bg-card border border-primary/8 rounded-2xl p-5 flex flex-col gap-3">
        <h2 className="text-sm font-bold text-text-main">Register a kiosk</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <input className={field} placeholder="Serial (e.g. SR4536)" value={form.serial}
            onChange={(e) => setForm({ ...form, serial: e.target.value })} />
          <input className={field} placeholder="Name (Main Gate)" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className={field} value={form.mode}
            onChange={(e) => setForm({ ...form, mode: e.target.value })}>
            <option value="AUTO">Mode: Automatic</option>
            <option value="IN">Mode: IN only</option>
            <option value="OUT">Mode: OUT only</option>
          </select>
          <select className={field} value={form.source_type}
            onChange={(e) => setForm({ ...form, source_type: e.target.value, camera_id: "" })}>
            <option value="webcam">Source: device webcam</option>
            <option value="system">Source: IP / system camera</option>
          </select>
          {form.source_type === "system" && (
            <select className={field} value={form.camera_id}
              onChange={(e) => setForm({ ...form, camera_id: e.target.value })}>
              <option value="">Camera…</option>
              {cameras.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name} · {c.role}</option>)}
            </select>
          )}
          <input className={field} type="number" placeholder="Dup window (s, optional)" value={form.dup_window}
            onChange={(e) => setForm({ ...form, dup_window: e.target.value })} />
          <button onClick={submit} className="bg-primary text-white rounded-lg px-3 py-2 text-sm font-bold hover:opacity-90">
            Register
          </button>
        </div>
      </div>

      {/* Device list */}
      <div className="bg-card border border-primary/8 rounded-2xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-text-muted border-b border-primary/8">
              <th className="py-2.5 px-4 font-semibold">Kiosk</th>
              <th className="py-2.5 px-3 font-semibold">Mode</th>
              <th className="py-2.5 px-3 font-semibold">Source</th>
              <th className="py-2.5 px-3 font-semibold">Login</th>
              <th className="py-2.5 px-3 font-semibold">Last seen</th>
              <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <tr key={d.id} className="border-b border-primary/5">
                <td className="py-2.5 px-4">
                  <div className="font-bold text-text-main">{d.name}</div>
                  <div className="text-[11px] text-text-muted font-mono">{d.serial}{d.is_active ? "" : " · inactive"}</div>
                </td>
                <td className="py-2.5 px-3 text-text-muted">{d.mode}</td>
                <td className="py-2.5 px-3 text-text-muted">{d.source_type === "system" ? `cam:${d.camera_id || "?"}` : "webcam"}</td>
                <td className="py-2.5 px-3">
                  {d.account_id
                    ? <span className="text-[11px] font-bold text-success">linked</span>
                    : <button onClick={() => setAcctFor(d.id)} className="text-[11px] font-bold text-primary hover:underline">provision</button>}
                </td>
                <td className="py-2.5 px-3 text-text-muted">{fmt(d.last_seen_at)}</td>
                <td className="py-2.5 px-3 text-right whitespace-nowrap">
                  <button onClick={() => toggle(d)} className="text-primary font-bold text-xs px-2 py-1 hover:underline">
                    {d.is_active ? "Disable" : "Enable"}
                  </button>
                  <button onClick={() => remove(d)} className="text-danger font-bold text-xs px-2 py-1 hover:underline">Remove</button>
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-text-muted">No kiosks registered yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {acctFor && (
        <ProvisionModal deviceId={acctFor} onClose={() => setAcctFor(null)}
          onDone={() => { setAcctFor(null); reload(); }} />
      )}
    </div>
  );
}

function ProvisionModal({ deviceId, onClose, onDone }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (username.trim().length < 1 || password.length < 8) return toast.error("Username required; password ≥ 8 chars");
    setBusy(true);
    try { await provisionAccount(deviceId, { username: username.trim(), password }); toast.success("Device login created"); onDone(); }
    catch (err) { toast.error(err.message); setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-card rounded-2xl border border-primary/10 p-6 w-full max-w-sm flex flex-col gap-3" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-text-main">Provision device login</h3>
        <p className="text-xs text-text-muted">A minimal kiosk account (kiosk.operate + streams.view). The kiosk signs in with these.</p>
        <input className={field} placeholder="Username (e.g. kiosk-gate-1)" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={field} type="password" placeholder="Password (≥ 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div className="flex gap-2 mt-1">
          <button onClick={submit} disabled={busy} className="flex-1 bg-primary text-white rounded-lg px-3 py-2.5 text-sm font-bold hover:opacity-90 disabled:opacity-50">
            {busy ? "Creating…" : "Create login"}
          </button>
          <button onClick={onClose} className="bg-background border border-primary/20 text-text-muted rounded-lg px-3 py-2.5 text-sm font-bold">Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── Log tables ───────────────────────────────────────────────────────────
function LogTable({ cols, rows, empty }) {
  return (
    <div className="bg-card border border-primary/8 rounded-2xl overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-text-muted border-b border-primary/8">
            {cols.map((c) => <th key={c.key} className="py-2.5 px-4 font-semibold">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i} className="border-b border-primary/5">
              {cols.map((c) => <td key={c.key} className="py-2.5 px-4 text-text-muted">{c.render ? c.render(r) : (r[c.key] ?? "—")}</td>)}
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={cols.length} className="py-8 text-center text-text-muted">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function Activity() {
  const [rows, setRows] = useState([]);
  const [dir, setDir] = useState("");
  useEffect(() => {
    (async () => {
      try { setRows((await listActivity({ visitType: dir || undefined, limit: 200 })).activity || []); }
      catch (err) { toast.error(err.message); }
    })();
  }, [dir]);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-text-muted">Direction</span>
        <select className={field} value={dir} onChange={(e) => setDir(e.target.value)}>
          <option value="">All</option><option value="IN">IN</option><option value="OUT">OUT</option>
          <option value="TRACKER_IN">Tracker in</option><option value="TRACKER_OUT">Tracker out</option>
        </select>
      </div>
      <LogTable empty="No kiosk activity yet." rows={rows} cols={[
        { key: "person_name", label: "Person", render: (r) => <span className="font-bold text-text-main">{r.person_name}</span> },
        { key: "person_type", label: "Type" },
        { key: "visit_type", label: "Direction", render: (r) => <StatusPill s={r.visit_type} /> },
        { key: "facility_name", label: "Facility" },
        { key: "camera_id", label: "Camera" },
        { key: "visit_number", label: "Visit #" },
        { key: "event_time", label: "Time", render: (r) => fmt(r.event_time) },
      ]} />
    </div>
  );
}

function Preregs() {
  const [rows, setRows] = useState([]);
  const reload = useCallback(async () => {
    try { setRows((await listPreregs({ limit: 200 })).preregs || []); } catch (err) { toast.error(err.message); }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const retry = async (id) => { try { await retryPrereg(id); toast.success("Re-queued"); reload(); } catch (err) { toast.error(err.message); } };
  return (
    <LogTable empty="No pre-registrations yet." rows={rows} cols={[
      { key: "person_name", label: "Person", render: (r) => <span className="font-bold text-text-main">{r.person_name || "—"}</span> },
      { key: "token_no", label: "Token", render: (r) => r.token_no ? <span className="font-bold text-primary">{r.token_no}</span> : "—" },
      { key: "status", label: "Status", render: (r) => <StatusPill s={r.status} /> },
      { key: "facility_name", label: "Facility" },
      { key: "created_at", label: "Time", render: (r) => fmt(r.created_at) },
      { key: "act", label: "", render: (r) => r.status === "FAILED" ? <button onClick={() => retry(r.id)} className="text-primary font-bold text-xs hover:underline">Retry</button> : null },
    ]} />
  );
}

function Exports() {
  const [rows, setRows] = useState([]);
  const reload = useCallback(async () => {
    try { setRows((await listExports({ limit: 200 })).exports || []); } catch (err) { toast.error(err.message); }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const retry = async (id) => { try { await retryExport(id); toast.success("Re-queued"); reload(); } catch (err) { toast.error(err.message); } };
  return (
    <LogTable empty="No attendance exports yet." rows={rows} cols={[
      { key: "person_name", label: "Person", render: (r) => <span className="font-bold text-text-main">{r.person_name || "—"}</span> },
      { key: "biometric_idx", label: "Txn id", render: (r) => <span className="font-mono text-[11px]">{r.biometric_idx}</span> },
      { key: "status", label: "Status", render: (r) => <StatusPill s={r.status} /> },
      { key: "attempts", label: "Tries" },
      { key: "sent_at", label: "Sent", render: (r) => fmt(r.sent_at) },
      { key: "act", label: "", render: (r) => r.status === "FAILED" ? <button onClick={() => retry(r.id)} className="text-primary font-bold text-xs hover:underline">Retry</button> : null },
    ]} />
  );
}
