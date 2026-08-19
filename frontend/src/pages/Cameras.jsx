import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "react-toastify";
import { API_URL as API } from "../config";
import { useSocketMessages } from "../hooks/useSocket";
import { listFacilities, listLocations } from "../api/facilities";

/**
 * Cameras admin page.
 *
 * - Lists every camera in the persistent store with its runtime status.
 * - Add / edit / delete network (IP) cameras without restarting the backend.
 * - "Test connection" probes the URL via the backend before saving.
 *
 * USB cameras can be displayed and edited but not created — the backend
 * rejects POST with type=usb. Existing USB entries (seeded from the
 * legacy CAMERAS list) keep working.
 */

const ROLE_OPTIONS = [
  { value: "entry", label: "Entry" },
  { value: "exit", label: "Exit" },
  { value: "inside", label: "Inside" },
];

const STATUS_STYLES = {
  running: {
    bg: "bg-emerald-500/10",
    text: "text-emerald-600 dark:text-emerald-400",
    dot: "bg-emerald-500",
    label: "Running",
  },
  stopped: {
    bg: "bg-gray-500/10",
    text: "text-gray-500 dark:text-gray-400",
    dot: "bg-gray-400",
    label: "Stopped",
  },
  missing: {
    bg: "bg-red-500/10",
    text: "text-red-600 dark:text-red-400",
    dot: "bg-red-500",
    label: "Not running",
  },
};

const EMPTY_FORM = {
  name: "",
  source: "",
  floor: "floor_1",
  role: "inside",
  active: true,
  facility_id: "",
  location_id: "",
};

export default function Cameras() {
  const [cameras, setCameras] = useState([]);
  const [facilities, setFacilities] = useState([]);
  const [locations, setLocations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Modal state — `mode` is "create" | "edit" | null. When editing we
  // also keep the original camera_id so PATCH targets the right row.
  const [modal, setModal] = useState({
    mode: null,
    cameraId: null,
    form: EMPTY_FORM,
  });
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [busyRow, setBusyRow] = useState(null);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await fetch(`${API}/cameras`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setCameras(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load cameras");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + slow safety-net poll. Real-time updates come from
  // the WS handler below — the 30s poll is only there to recover if a
  // WS event is dropped or the page is opened mid-reconnect.
  useEffect(() => {
    fetchCameras();
    const t = setInterval(fetchCameras, 30_000);
    return () => clearInterval(t);
  }, [fetchCameras]);

  // Coalesce bursts: many WS events in quick succession (e.g. several
  // cameras reconnecting after a switch reboot) collapse into a single
  // refetch. Without this we'd hammer /cameras N times for N events.
  const refetchTimerRef = useRef(null);
  const scheduleRefetch = useCallback(() => {
    if (refetchTimerRef.current) return;
    refetchTimerRef.current = setTimeout(() => {
      refetchTimerRef.current = null;
      fetchCameras();
    }, 250);
  }, [fetchCameras]);

  useEffect(
    () => () => {
      if (refetchTimerRef.current) clearTimeout(refetchTimerRef.current);
    },
    [],
  );

  // The backend already publishes CAMERA_DISCONNECTED / CAMERA_RECONNECTED
  // for every health transition. Any of these events is a reason to
  // re-pull the list — status pills depend on the same signal.
  const handleWS = useCallback(
    (msg) => {
      if (!msg || typeof msg !== "object") return;
      const t = msg.type;
      if (t === "CAMERA_DISCONNECTED" || t === "CAMERA_RECONNECTED") {
        scheduleRefetch();
      }
    },
    [scheduleRefetch],
  );
  useSocketMessages(handleWS);

  const sortedCameras = useMemo(
    () => [...cameras].sort((a, b) => a.name.localeCompare(b.name)),
    [cameras],
  );

  // Facility + location lists for the placement dropdowns. Best-effort:
  // if the account can't read them the selects just stay empty.
  useEffect(() => {
    (async () => {
      try {
        const [f, l] = await Promise.all([listFacilities(), listLocations()]);
        setFacilities(f.facilities || []);
        setLocations(l.locations || []);
      } catch {
        /* not permitted / none — placement selects hide their options */
      }
    })();
  }, []);

  const facilityName = useCallback(
    (id) => facilities.find((f) => f.id === id)?.display_name || null,
    [facilities],
  );
  const locationName = useCallback(
    (id) => locations.find((l) => l.id === id)?.name || null,
    [locations],
  );

  const openCreate = () => {
    setTestResult(null);
    setModal({ mode: "create", cameraId: null, form: EMPTY_FORM });
  };

  const openEdit = (cam) => {
    setTestResult(null);
    setModal({
      mode: "edit",
      cameraId: cam.camera_id,
      form: {
        name: cam.name || "",
        source: cam.source || "",
        floor: cam.floor || "floor_1",
        role: cam.role || "inside",
        active: cam.active !== false,
        facility_id: cam.facility_id ?? "",
        location_id: cam.location_id ?? "",
      },
    });
  };

  const closeModal = () => {
    if (submitting) return;
    setModal({ mode: null, cameraId: null, form: EMPTY_FORM });
    setTestResult(null);
  };

  const updateForm = (patch) =>
    setModal((m) => ({ ...m, form: { ...m.form, ...patch } }));

  const handleTest = async () => {
    const source = modal.form.source.trim();
    if (!source) {
      toast.warn("Enter a stream URL first");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${API}/cameras/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      const data = await res.json();
      setTestResult(data);
      if (data.ok) {
        toast.success(
          `Connected — ${data.width}×${data.height} in ${data.elapsed_ms} ms`,
        );
      } else {
        toast.error(data.error || "Connection test failed");
      }
    } catch (err) {
      setTestResult({ ok: false, error: err.message });
      toast.error("Test request failed");
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const isCreate = modal.mode === "create";
      const url = isCreate
        ? `${API}/cameras`
        : `${API}/cameras/${modal.cameraId}`;
      const method = isCreate ? "POST" : "PATCH";

      // Coerce the placement selects: "" → null, else a number.
      const placement = {
        facility_id: modal.form.facility_id === "" ? null : Number(modal.form.facility_id),
        location_id: modal.form.location_id === "" ? null : Number(modal.form.location_id),
      };
      const body = isCreate
        ? { ...modal.form, ...placement, type: "rtsp" }
        : { ...modal.form, ...placement };

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const text = await res.text();
        let msg = text;
        try {
          msg = JSON.parse(text).detail || text;
        } catch {
          /* keep raw text */
        }
        throw new Error(msg);
      }

      toast.success(isCreate ? "Camera added" : "Camera updated");
      closeModal();
      await fetchCameras();
    } catch (err) {
      toast.error(err.message || "Save failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (cam) => {
    if (
      !confirm(
        `Delete camera "${cam.name}"? This will stop its worker and forget the URL.`,
      )
    ) {
      return;
    }
    setBusyRow(cam.camera_id);
    try {
      const res = await fetch(`${API}/cameras/${cam.camera_id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      toast.success("Camera removed");
      await fetchCameras();
    } catch (err) {
      toast.error(err.message || "Delete failed");
    } finally {
      setBusyRow(null);
    }
  };

  const handleRestart = async (cam) => {
    setBusyRow(cam.camera_id);
    try {
      const res = await fetch(`${API}/cameras/${cam.camera_id}/restart`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success("Camera restarting…");
      await fetchCameras();
    } catch (err) {
      toast.error(err.message || "Restart failed");
    } finally {
      setBusyRow(null);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* ── Header ── */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-text-main">Cameras</h1>
          <p className="text-sm text-text-muted mt-1">
            Add or edit network (IP) camera feeds.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="bg-primary hover:bg-primary/90 text-white text-sm font-semibold px-4 py-2.5 rounded-xl shadow-sm shadow-primary/20 transition-colors flex items-center gap-2"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2.5}
              d="M12 4v16m8-8H4"
            />
          </svg>
          Add IP Camera
        </button>
      </div>

      {/* ── List ── */}
      <div className="bg-card rounded-2xl shadow-sm ring-1 ring-primary/5 overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-text-muted text-sm">
            Loading…
          </div>
        ) : error ? (
          <div className="p-10 text-center text-red-600 dark:text-red-400 text-sm">
            <p className="font-semibold">Couldn't load cameras</p>
            <p className="text-xs mt-1 text-text-muted">{error}</p>
            <button
              onClick={fetchCameras}
              className="mt-3 px-3 py-1.5 text-xs rounded-lg bg-primary/10 hover:bg-primary/20 text-primary"
            >
              Retry
            </button>
          </div>
        ) : sortedCameras.length === 0 ? (
          <div className="p-10 text-center text-text-muted">
            <p className="text-sm font-medium">No cameras configured yet.</p>
            <p className="text-xs mt-1">
              Click "Add IP Camera" to register your first feed.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-primary/5">
            {sortedCameras.map((cam) => {
              const status = STATUS_STYLES[cam.status] || STATUS_STYLES.missing;
              const isBusy = busyRow === cam.camera_id;
              const isUsb = cam.type === "usb";
              return (
                <li
                  key={cam.camera_id}
                  className="p-4 sm:px-6 flex items-center gap-4"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-bold text-text-main truncate">
                        {cam.name}
                      </span>
                      <span
                        className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${status.bg} ${status.text}`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${status.dot}`}
                        ></span>
                        {status.label}
                      </span>
                      <span className="text-[10px] uppercase font-semibold text-text-light bg-primary/5 px-2 py-0.5 rounded-full">
                        {cam.type}
                      </span>
                      <span className="text-[10px] uppercase font-semibold text-text-light bg-primary/5 px-2 py-0.5 rounded-full">
                        {cam.role}
                      </span>
                      <span className="text-[10px] uppercase font-semibold text-text-light bg-primary/5 px-2 py-0.5 rounded-full">
                        {cam.floor}
                      </span>
                      {locationName(cam.location_id) && (
                        <span className="text-[10px] font-semibold text-primary bg-primary/10 px-2 py-0.5 rounded-full">
                          📍 {locationName(cam.location_id)}
                          {facilityName(cam.facility_id) ? ` · ${facilityName(cam.facility_id)}` : ""}
                        </span>
                      )}
                    </div>
                    <p
                      className="text-xs text-text-muted mt-1 font-mono truncate"
                      title={cam.source}
                    >
                      {cam.source}
                    </p>
                    <p className="text-[10px] text-text-light mt-0.5">
                      ID: {cam.camera_id}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      disabled={isBusy}
                      onClick={() => handleRestart(cam)}
                      className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary/5 hover:bg-primary/10 text-primary disabled:opacity-50"
                      title="Restart worker"
                    >
                      Restart
                    </button>
                    <button
                      disabled={isBusy}
                      onClick={() => openEdit(cam)}
                      className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary/5 hover:bg-primary/10 text-primary disabled:opacity-50"
                    >
                      Edit
                    </button>
                    <button
                      disabled={isBusy || isUsb}
                      onClick={() => handleDelete(cam)}
                      title={
                        isUsb
                          ? "USB cameras can't be deleted from the UI"
                          : "Delete"
                      }
                      className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-500/5 hover:bg-red-500/10 text-red-600 dark:text-red-400 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ── Modal ── */}
      {modal.mode && (
        <CameraFormModal
          mode={modal.mode}
          form={modal.form}
          updateForm={updateForm}
          onSubmit={handleSubmit}
          onClose={closeModal}
          onTest={handleTest}
          testing={testing}
          testResult={testResult}
          submitting={submitting}
          facilities={facilities}
          locations={locations}
        />
      )}
    </div>
  );
}

function CameraFormModal({
  mode,
  form,
  updateForm,
  onSubmit,
  onClose,
  onTest,
  testing,
  testResult,
  submitting,
  facilities,
  locations,
}) {
  const isEdit = mode === "edit";
  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={onSubmit}
        className="bg-card w-full max-w-lg rounded-2xl shadow-xl ring-1 ring-primary/10 overflow-hidden"
      >
        <header className="px-6 py-4 border-b border-primary/5">
          <h2 className="text-lg font-extrabold text-text-main">
            {isEdit ? "Edit camera" : "Add IP camera"}
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            {isEdit
              ? "Source / type / active changes will restart the worker."
              : "URL will be tested before saving (recommended)."}
          </p>
        </header>

        <div className="px-6 py-5 space-y-4">
          <Field label="Name">
            <input
              required
              maxLength={80}
              value={form.name}
              onChange={(e) => updateForm({ name: e.target.value })}
              placeholder="e.g. Lobby Entrance"
              className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm"
            />
          </Field>

          <Field label="Stream URL">
            <input
              required
              maxLength={500}
              value={form.source}
              onChange={(e) => updateForm({ source: e.target.value })}
              placeholder="rtsp://user:pass@10.0.0.42:554/stream1"
              className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm font-mono"
            />
            <div className="flex items-center justify-between mt-1.5">
              <p className="text-[11px] text-text-light">
                Network cameras stream over RTSP — URL must start with{" "}
                <span className="font-mono">rtsp://</span> or{" "}
                <span className="font-mono">rtsps://</span>
              </p>
              <button
                type="button"
                disabled={testing}
                onClick={onTest}
                className="text-xs font-semibold text-primary hover:underline disabled:opacity-50"
              >
                {testing ? "Testing…" : "Test connection"}
              </button>
            </div>
            {testResult && (
              <div
                className={`mt-2 px-3 py-2 rounded-lg text-xs ${
                  testResult.ok
                    ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30"
                    : "bg-red-50 text-red-700 ring-1 ring-red-200 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-500/30"
                }`}
              >
                {testResult.ok
                  ? `OK — ${testResult.width}×${testResult.height} (${testResult.elapsed_ms} ms)`
                  : `Failed — ${testResult.error || "unknown"}`}
              </div>
            )}
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Floor">
              <input
                required
                maxLength={40}
                value={form.floor}
                onChange={(e) => updateForm({ floor: e.target.value })}
                placeholder="floor_1"
                className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm"
              />
            </Field>
            <Field label="Role">
              <select
                value={form.role}
                onChange={(e) => updateForm({ role: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm bg-card"
              >
                {ROLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Facility (optional)">
              <select
                value={form.facility_id}
                onChange={(e) =>
                  updateForm({ facility_id: e.target.value, location_id: "" })}
                className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm bg-card"
              >
                <option value="">—</option>
                {facilities.map((f) => (
                  <option key={f.id} value={f.id}>{f.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="Location (optional)">
              <select
                value={form.location_id}
                onChange={(e) => updateForm({ location_id: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-primary/10 focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none text-sm bg-card disabled:opacity-50"
                disabled={!form.facility_id}
              >
                <option value="">—</option>
                {locations
                  .filter((l) =>
                    !form.facility_id || String(l.facility_id) === String(form.facility_id))
                  .map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name} ({l.location_type})
                    </option>
                  ))}
              </select>
            </Field>
          </div>

          <label className="flex items-center gap-2 text-sm text-text-main cursor-pointer">
            <input
              type="checkbox"
              checked={form.active}
              onChange={(e) => updateForm({ active: e.target.checked })}
              className="w-4 h-4 rounded text-primary"
            />
            Active (worker runs and produces frames)
          </label>
        </div>

        <footer className="px-6 py-4 bg-primary/5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 text-sm font-semibold rounded-lg text-text-muted hover:bg-primary/10 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 shadow-sm shadow-primary/20"
          >
            {submitting ? "Saving…" : isEdit ? "Save changes" : "Add camera"}
          </button>
        </footer>
      </form>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="block text-xs font-semibold text-text-muted mb-1.5 uppercase tracking-wide">
        {label}
      </span>
      {children}
    </label>
  );
}
