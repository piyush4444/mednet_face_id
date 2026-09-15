import { useCallback, useEffect, useState } from "react";
import { toast } from "react-toastify";
import {
  getAttendanceConfig,
  getAttendanceDashboard,
  getAttendanceObservations,
  getPunchQueue,
  retryPunch,
  saveAttendanceConfig,
} from "../api/attendance";
import { useAuth } from "../hooks/useAuth";

const fieldClass = "bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-2 text-sm font-semibold outline-none";
const cardClass = "bg-card rounded-2xl border border-primary/10 shadow-md";
const today = () => new Date().toLocaleDateString("en-CA");
const displayTime = (value) => value ? new Date(value).toLocaleString() : "—";

function Status({ value }) {
  const tone = value === "SENT" || value?.startsWith("QUEUED")
    ? "bg-success/10 text-success"
    : value === "FAILED" ? "bg-danger/10 text-danger" : "bg-primary/10 text-primary";
  return <span className={`text-[11px] font-bold px-2 py-1 rounded-full ${tone}`}>{value || "—"}</span>;
}

export default function Attendance() {
  const { hasPermission } = useAuth();
  const canManage = hasPermission("attendance.manage");
  const canRetry = hasPermission("attendance.retry");
  const [date, setDate] = useState(today);
  const [policy, setPolicy] = useState(null);
  const [cameras, setCameras] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [observations, setObservations] = useState([]);
  const [queue, setQueue] = useState([]);
  const [saving, setSaving] = useState(false);

  const reloadOperational = useCallback(async () => {
    try {
      const [daily, seen, punches] = await Promise.all([
        getAttendanceDashboard(date),
        getAttendanceObservations(date),
        getPunchQueue(),
      ]);
      setDashboard(daily);
      setObservations(seen);
      setQueue(punches);
    } catch (err) {
      toast.error(err.message);
    }
  }, [date]);

  useEffect(() => {
    getAttendanceConfig().then((data) => {
      setPolicy(data.policy);
      setCameras(data.cameras || []);
    }).catch((err) => toast.error(err.message));
  }, []);

  useEffect(() => {
    Promise.all([
      getAttendanceDashboard(date),
      getAttendanceObservations(date),
      getPunchQueue(),
    ]).then(([daily, seen, punches]) => {
      setDashboard(daily);
      setObservations(seen);
      setQueue(punches);
    }).catch((err) => toast.error(err.message));
  }, [date]);

  const change = (key, value) => setPolicy((current) => ({ ...current, [key]: value }));
  const toggleCamera = (cameraId, checked) => {
    const ids = new Set(policy.attendance_camera_ids || []);
    checked ? ids.add(cameraId) : ids.delete(cameraId);
    change("attendance_camera_ids", [...ids]);
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        ...policy,
        eligible_user_types: policy.eligible_user_types || [],
        attendance_camera_ids: policy.attendance_camera_ids || [],
        camera_serial_numbers: policy.camera_serial_numbers || {},
      };
      delete payload.id;
      delete payload.version;
      delete payload.updated_at;
      const saved = await saveAttendanceConfig(payload);
      setPolicy(saved);
      toast.success(saved.shadow_mode
        ? "Attendance policy saved in shadow mode"
        : "Attendance policy saved");
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const retry = async (id) => {
    try {
      await retryPunch(id);
      toast.success(`Punch ${id} rescheduled`);
      reloadOperational();
    } catch (err) {
      toast.error(err.message);
    }
  };

  if (!policy) return <div className="p-8 text-text-muted">Loading attendance configuration…</div>;
  const summary = dashboard?.summary || {};

  return (
    <div className="p-5 md:p-7 flex flex-col gap-5">
      <div>
        <h1 className="text-xl font-extrabold text-text-main">Attendance</h1>
        <p className="text-sm text-text-muted mt-1">Control recognition-based Mednet IN/OUT punches and inspect every decision and delivery.</p>
      </div>

      <section className={`${cardClass} p-5 flex flex-col gap-4`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-text-main">Punch policy</h2>
            <p className="text-xs text-text-muted">Start in shadow mode to audit decisions without contacting Mednet.</p>
          </div>
          <div className="flex gap-4 text-sm font-semibold">
            <label className="flex items-center gap-2"><input type="checkbox" disabled={!canManage} checked={policy.enabled} onChange={(e) => change("enabled", e.target.checked)} /> Enabled</label>
            <label className="flex items-center gap-2"><input type="checkbox" disabled={!canManage} checked={policy.shadow_mode} onChange={(e) => change("shadow_mode", e.target.checked)} /> Shadow mode</label>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <label className="text-xs font-bold text-text-muted">IN FROM<input className={`${fieldClass} w-full mt-1`} type="time" disabled={!canManage} value={policy.in_window_start} onChange={(e) => change("in_window_start", e.target.value)} /></label>
          <label className="text-xs font-bold text-text-muted">IN UNTIL<input className={`${fieldClass} w-full mt-1`} type="time" disabled={!canManage} value={policy.in_window_end} onChange={(e) => change("in_window_end", e.target.value)} /></label>
          <label className="text-xs font-bold text-text-muted">OUT FROM<input className={`${fieldClass} w-full mt-1`} type="time" disabled={!canManage} value={policy.out_window_start} onChange={(e) => change("out_window_start", e.target.value)} /></label>
          <label className="text-xs font-bold text-text-muted">OUT UNTIL<input className={`${fieldClass} w-full mt-1`} type="time" disabled={!canManage} value={policy.out_window_end} onChange={(e) => change("out_window_end", e.target.value)} /></label>
          <label className="text-xs font-bold text-text-muted">TIMEZONE<input className={`${fieldClass} w-full mt-1`} disabled={!canManage} value={policy.timezone} onChange={(e) => change("timezone", e.target.value)} /></label>
          <label className="text-xs font-bold text-text-muted">MINIMUM WORK MINUTES<input className={`${fieldClass} w-full mt-1`} type="number" disabled={!canManage} value={policy.minimum_work_minutes} onChange={(e) => change("minimum_work_minutes", Number(e.target.value))} /></label>
          <label className="text-xs font-bold text-text-muted">LOG COOLDOWN SECONDS<input className={`${fieldClass} w-full mt-1`} type="number" disabled={!canManage} value={policy.observation_cooldown_seconds} onChange={(e) => change("observation_cooldown_seconds", Number(e.target.value))} /></label>
          <label className="text-xs font-bold text-text-muted">BIOMETRIC SERVER IP<input className={`${fieldClass} w-full mt-1`} disabled={!canManage} placeholder="Optional" value={policy.biometric_server_ip} onChange={(e) => change("biometric_server_ip", e.target.value)} /></label>
        </div>

        <div>
          <h3 className="text-sm font-bold text-text-main mb-2">Eligible registered people</h3>
          <div className="flex gap-5">
            {["EMPLOYEE", "DOCTOR"].map((userType) => <label key={userType} className="flex items-center gap-2 text-sm font-semibold text-text-muted">
              <input type="checkbox" disabled={!canManage} checked={policy.eligible_user_types.includes(userType)} onChange={(e) => {
                const types = new Set(policy.eligible_user_types);
                e.target.checked ? types.add(userType) : types.delete(userType);
                change("eligible_user_types", [...types]);
              }} />
              {userType === "EMPLOYEE" ? "Employees" : "Doctors"}
            </label>)}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-bold text-text-main mb-2">Attendance cameras</h3>
          <div className="grid md:grid-cols-2 gap-2">
            {cameras.map((camera) => {
              const selected = policy.attendance_camera_ids.includes(camera.id);
              return <div key={camera.id} className="flex items-center gap-3 bg-background rounded-xl p-3 border border-primary/8">
                <input type="checkbox" disabled={!canManage || !camera.active} checked={selected} onChange={(e) => toggleCamera(camera.id, e.target.checked)} />
                <div className="min-w-0 flex-1"><div className="text-sm font-bold text-text-main">{camera.name}</div><div className="text-xs text-text-muted">{camera.id}{!camera.active && " · inactive"}</div></div>
                <input className={`${fieldClass} w-44`} disabled={!canManage || !selected} placeholder="Mednet serial number" value={policy.camera_serial_numbers[camera.id] || ""} onChange={(e) => change("camera_serial_numbers", { ...policy.camera_serial_numbers, [camera.id]: e.target.value })} />
              </div>;
            })}
            {!cameras.length && <p className="text-sm text-text-muted">No cameras configured.</p>}
          </div>
        </div>

        {canManage && <button disabled={saving} onClick={save} className="self-end bg-primary text-white rounded-lg px-5 py-2 text-sm font-bold disabled:opacity-50">{saving ? "Saving…" : "Save policy"}</button>}
      </section>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[['Recognized', summary.recognized || 0], ['IN decisions', summary.in_decided || 0], ['OUT decisions', summary.out_decided || 0], ['Failed deliveries', summary.queued?.FAILED || 0]].map(([label, value]) => <div key={label} className={`${cardClass} p-4`}><div className="text-2xl font-extrabold text-text-main">{value}</div><div className="text-xs font-semibold text-text-muted">{label}</div></div>)}
      </section>

      <section className={`${cardClass} overflow-hidden`}>
        <div className="p-4 border-b border-primary/8 flex justify-between items-center"><div><h2 className="font-bold text-text-main">Daily attendance</h2><p className="text-xs text-text-muted">One decision state per registered user and day.</p></div><input className={fieldClass} type="date" value={date} onChange={(e) => setDate(e.target.value)} /></div>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs text-text-muted border-b border-primary/8"><th className="p-3">Person</th><th className="p-3">First seen</th><th className="p-3">IN</th><th className="p-3">OUT</th><th className="p-3">Last seen</th></tr></thead><tbody>{(dashboard?.rows || []).map((row) => <tr key={row.id} className="border-b border-primary/5"><td className="p-3 font-bold text-text-main">{row.name}<div className="text-[11px] text-text-muted">{row.user_type}</div></td><td className="p-3">{displayTime(row.first_seen_at)}</td><td className="p-3">{displayTime(row.in_decided_at)}{row.in_shadow && " · shadow"}</td><td className="p-3">{displayTime(row.out_decided_at)}{row.out_shadow && " · shadow"}</td><td className="p-3">{displayTime(row.last_seen_at)}</td></tr>)}{!dashboard?.rows?.length && <tr><td colSpan={5} className="p-8 text-center text-text-muted">No attendance decisions for this date.</td></tr>}</tbody></table></div>
      </section>

      <section className={`${cardClass} overflow-hidden`}>
        <div className="p-4 border-b border-primary/8"><h2 className="font-bold text-text-main">Mednet delivery queue</h2><p className="text-xs text-text-muted">Calls happen in the background with ordered retries and full attempt audit.</p></div>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs text-text-muted border-b border-primary/8"><th className="p-3">ID / person</th><th className="p-3">Punch</th><th className="p-3">Status</th><th className="p-3">Attempts</th><th className="p-3">Latency</th><th className="p-3">Error</th><th className="p-3"></th></tr></thead><tbody>{queue.map((row) => <tr key={row.id} className="border-b border-primary/5"><td className="p-3 font-semibold">#{row.id} · {row.name || `User ${row.user_id}`}</td><td className="p-3">{row.direction} · {row.attendance_date}</td><td className="p-3"><Status value={row.status} /></td><td className="p-3">{row.attempts}</td><td className="p-3">{row.last_latency_ms == null ? "—" : `${row.last_latency_ms} ms`}</td><td className="p-3 text-danger max-w-xs truncate" title={row.last_error || ""}>{row.last_error || "—"}</td><td className="p-3">{canRetry && row.status !== "SENT" && <button className="text-primary font-bold" onClick={() => retry(row.id)}>Retry</button>}</td></tr>)}{!queue.length && <tr><td colSpan={7} className="p-8 text-center text-text-muted">Queue is empty.</td></tr>}</tbody></table></div>
      </section>

      <section className={`${cardClass} overflow-hidden`}>
        <div className="p-4 border-b border-primary/8"><h2 className="font-bold text-text-main">Recognition decision audit</h2><p className="text-xs text-text-muted">Ordinary sightings remain local; only qualified window decisions create punches.</p></div>
        <div className="overflow-x-auto max-h-96"><table className="w-full text-sm"><thead><tr className="text-left text-xs text-text-muted border-b border-primary/8"><th className="p-3">Observed</th><th className="p-3">Person</th><th className="p-3">Camera</th><th className="p-3">Outcome</th><th className="p-3">Reason</th></tr></thead><tbody>{observations.map((row) => <tr key={row.id} className="border-b border-primary/5"><td className="p-3">{displayTime(row.observed_at)}</td><td className="p-3 font-semibold">{row.name}</td><td className="p-3">{row.camera_id}</td><td className="p-3"><Status value={row.outcome} /></td><td className="p-3 text-text-muted">{row.reason.replaceAll("_", " ")}</td></tr>)}{!observations.length && <tr><td colSpan={5} className="p-8 text-center text-text-muted">No observations for this date.</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}
