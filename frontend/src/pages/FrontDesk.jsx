/**
 * FrontDesk — /frontdesk
 *
 * Hospital OPD front-desk workflow:
 *   1. Scan patient via webcam → /frontdesk/scan
 *   2. Prefill OPD form (visit type, department, doctor, note)
 *   3. Generate token → /frontdesk/visits
 *   4. Print slip (window.print + dedicated print CSS)
 *   5. Live queue of today's tokens (refreshed via WS + polling)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "react-toastify";
import { useWebcams } from "../hooks/useWebcams";
import CameraSelect from "../components/CameraSelect";
import { useSocketMessages } from "../hooks/useSocket";
import { Link } from "react-router-dom";

const TYPE_BADGE = {
  PATIENT:  { bg: "bg-blue-100 dark:bg-blue-500/15",       text: "text-blue-700 dark:text-blue-300",       label: "Patient" },
  DOCTOR:   { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300", label: "Doctor" },
  EMPLOYEE: { bg: "bg-amber-100 dark:bg-amber-500/15",     text: "text-amber-700 dark:text-amber-300",     label: "Employee" },
  VISITOR:  { bg: "bg-purple-100 dark:bg-purple-500/15",   text: "text-purple-700 dark:text-purple-300",   label: "Visitor" },
  RELATIVE: { bg: "bg-pink-100 dark:bg-pink-500/15",       text: "text-pink-700 dark:text-pink-300",       label: "Relative" },
};
const TYPE_LABEL = (t) => TYPE_BADGE[t]?.label || "Patient";
const isPatient = (u) => !u?.user_type || u.user_type === "PATIENT";
import QRCode from "qrcode";
import CustomSelect from "../components/CustomSelect";
import { API_URL } from "../config";
import {
  scanFrame,
  createVisit,
  listTodayVisits,
  listDepartments,
  listDoctors,
  reprintVisit,
  updateVisitStatus,
  searchPatients,
} from "../api/frontdesk";

// ── Scan panel ───────────────────────────────────────────────────────────
// Two capture modes:
//   "webcam"  — browser USB webcam via getUserMedia (existing behavior).
//   "system"  — backend-configured IP/RTSP camera; we render its MJPEG
//               stream and capture a snapshot via GET /stream/{id}/snapshot.
function ScanPanel({ onScanned, onReset }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [mode, setMode] = useState("system"); // "webcam" | "system" — default to system cameras
  const [isCameraOn, setIsCameraOn] = useState(false);
  const [scanning, setScanning] = useState(false);
  const { devices, selectedDeviceId, setSelectedDeviceId, refreshDevices } =
    useWebcams();

  // System cameras (config from backend).
  const [systemCameras, setSystemCameras] = useState([]);
  const [selectedSystemCam, setSelectedSystemCam] = useState("");
  const [streamNonce, setStreamNonce] = useState(0); // force remount on retry

  // Load system camera list once.
  useEffect(() => {
    fetch(`${API_URL}/tracking/cameras`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    })
      .then((r) => r.ok ? r.json() : { cameras: [] })
      .then((d) => {
        const cams = d.cameras || [];
        setSystemCameras(cams);
        if (cams.length && !selectedSystemCam) {
          setSelectedSystemCam(cams[0].camera_id);
        }
      })
      .catch(() => setSystemCameras([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Webcam (browser) lifecycle ────────────────────────────────────────
  const stopWebcam = () => {
    const stream = videoRef.current?.srcObject;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.srcObject = null;
    }
    setIsCameraOn(false);
  };

  const startWebcam = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: selectedDeviceId
          ? { deviceId: { exact: selectedDeviceId } }
          : true,
      });
      await new Promise((r) => setTimeout(r, 50));
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      await new Promise((resolve) => {
        video.onloadedmetadata = () => resolve();
      });
      await video.play();
      setIsCameraOn(true);
      refreshDevices();
    } catch (err) {
      console.error(err);
      toast.error("Unable to access webcam.");
    }
  };

  // Auto-start webcam when in webcam mode; stop it when leaving.
  useEffect(() => {
    if (mode === "webcam") {
      startWebcam();
      return () => stopWebcam();
    } else {
      stopWebcam();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    if (mode === "webcam" && isCameraOn && selectedDeviceId) startWebcam();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDeviceId]);

  // ── Capture + scan ────────────────────────────────────────────────────
  const captureWebcamBlob = async () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !isCameraOn) {
      throw new Error("Webcam not ready");
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    return await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
  };

  const captureSystemBlob = async () => {
    if (!selectedSystemCam) throw new Error("Pick a system camera first");
    const res = await fetch(
      `${API_URL}/stream/${encodeURIComponent(selectedSystemCam)}/snapshot`,
      { headers: { "ngrok-skip-browser-warning": "true" } },
    );
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch { /* ignore */ }
      throw new Error(detail || `Snapshot failed (${res.status})`);
    }
    return await res.blob();
  };

  const runScan = async () => {
    setScanning(true);
    try {
      const blob =
        mode === "webcam" ? await captureWebcamBlob() : await captureSystemBlob();
      if (!blob) throw new Error("Failed to capture frame");
      const result = await scanFrame(blob);
      if (result.matched) {
        const typeLabel = TYPE_LABEL(result.patient.user_type);
        toast.success(`Recognized ${typeLabel.toLowerCase()}: ${result.patient.name}`);
      } else {
        toast.warn("Face not recognized — try manual search.");
      }
      onScanned(result);
    } catch (err) {
      console.error(err);
      toast.error(`Scan failed: ${err.message}`);
    } finally {
      setScanning(false);
    }
  };

  const isSystemReady = mode === "system" && !!selectedSystemCam;
  const canScan =
    (mode === "webcam" && isCameraOn) || (isSystemReady && !scanning);

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex flex-wrap justify-between items-center gap-2">
        <h3 className="text-sm font-bold text-text-main whitespace-nowrap">Patient Scan</h3>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Mode toggle */}
          <div className="flex bg-background border border-primary/15 rounded-lg overflow-hidden text-[11px] font-semibold">
            <button
              onClick={() => setMode("webcam")}
              className={`px-2.5 py-1 ${mode === "webcam" ? "bg-primary text-white" : "text-text-muted hover:bg-primary/5"}`}
            >
              Webcam
            </button>
            <button
              onClick={() => setMode("system")}
              className={`px-2.5 py-1 ${mode === "system" ? "bg-primary text-white" : "text-text-muted hover:bg-primary/5"}`}
            >
              System
            </button>
          </div>

          {/* Camera selector — depends on mode */}
          {mode === "webcam" && devices.length > 0 && (
            <CameraSelect
              devices={devices}
              selectedDeviceId={selectedDeviceId}
              onSelect={setSelectedDeviceId}
              compact
            />
          )}
          {mode === "system" && (
            <CustomSelect
              value={selectedSystemCam}
              onChange={(e) => {
                setSelectedSystemCam(e.target.value);
                setStreamNonce((n) => n + 1);
              }}
              disabled={systemCameras.length === 0}
              size="sm"
              className="min-w-36"
              placeholder={systemCameras.length === 0 ? "No cameras configured" : "Select camera"}
              leadingIcon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              }
              options={systemCameras.map((c) => ({
                value: c.camera_id,
                label: c.name || c.camera_id,
              }))}
            />
          )}

          {/* Webcam start/stop only relevant in webcam mode */}
          {mode === "webcam" && (
            isCameraOn ? (
              <button
                onClick={stopWebcam}
                className="text-xs px-3 py-1.5 bg-danger/10 text-danger rounded-lg font-semibold border border-danger/20"
              >
                Stop
              </button>
            ) : (
              <button
                onClick={startWebcam}
                className="text-xs px-3 py-1.5 bg-success/10 text-green-700 dark:text-green-300 rounded-lg font-semibold border border-success/20"
              >
                Start
              </button>
            )
          )}
        </div>
      </div>

      <div className="p-5 flex flex-col gap-4">
        <div className="w-full aspect-video bg-gray-900 rounded-xl overflow-hidden flex items-center justify-center relative">
          {mode === "webcam" ? (
            <>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={`w-full h-full object-cover ${!isCameraOn ? "hidden" : ""}`}
              />
              <canvas ref={canvasRef} className="hidden" />
              {!isCameraOn && (
                <p className="text-white/50 text-sm font-medium">Camera inactive</p>
              )}
            </>
          ) : selectedSystemCam ? (
            <img
              key={`${selectedSystemCam}-${streamNonce}`}
              src={`${API_URL}/stream/${encodeURIComponent(selectedSystemCam)}?n=${streamNonce}`}
              alt={`Stream ${selectedSystemCam}`}
              className="w-full h-full object-cover"
              onError={() => {
                /* MJPEG can flake under reconnects — retry once on error. */
                setTimeout(() => setStreamNonce((n) => n + 1), 1000);
              }}
            />
          ) : (
            <p className="text-white/50 text-sm font-medium">No camera selected</p>
          )}
        </div>

        <div className="flex gap-3">
          <button
            onClick={runScan}
            disabled={!canScan || scanning}
            className={`flex-1 px-4 py-3 rounded-xl font-bold transition-all duration-200 ${
              !canScan || scanning
                ? "bg-primary/8 text-text-light cursor-not-allowed"
                : "gradient-primary text-white hover:opacity-90 shadow-lg shadow-primary/20"
            }`}
          >
            {scanning ? "Scanning…" : "Scan Patient"}
          </button>
          <button
            onClick={onReset}
            className="px-4 py-3 rounded-xl font-semibold bg-card border border-primary/15 text-text-main hover:bg-primary/5"
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Manual search (MRN / phone / name fallback) ──────────────────────────
function ManualSearch({ onPicked }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    if (!q.trim()) return;
    setLoading(true);
    try {
      const res = await searchPatients(q.trim());
      setResults(res.candidates || []);
      if (!res.matched) toast.info("No matches.");
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const pick = async (patientId) => {
    // Re-fetch through /search to get full patient object + recent visits.
    try {
      const res = await searchPatients(String(patientId));
      if (res.matched) {
        onPicked(res);
        setOpen(false);
        setQ("");
        setResults([]);
      }
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full px-5 py-3 flex items-center justify-between text-sm font-bold text-text-main hover:bg-primary/5"
      >
        <span>Manual Search (MRN / Phone / Name)</span>
        <span className="text-text-light text-xs">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="px-5 pb-5 flex flex-col gap-3">
          <div className="flex gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="MRN, phone or name…"
              className="flex-1 bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-lg px-3 py-2 text-sm font-semibold outline-none"
            />
            <button
              onClick={run}
              disabled={loading || !q.trim()}
              className={`px-4 py-2 rounded-lg text-sm font-bold ${
                loading || !q.trim()
                  ? "bg-primary/8 text-text-light cursor-not-allowed"
                  : "bg-primary text-white hover:opacity-90"
              }`}
            >
              {loading ? "…" : "Search"}
            </button>
          </div>
          {results.length > 0 && (
            <div className="border border-primary/10 rounded-lg overflow-hidden divide-y divide-primary/8">
              {results.map((r) => {
                const b = TYPE_BADGE[r.user_type] || TYPE_BADGE.PATIENT;
                return (
                  <button
                    key={r.patient_id}
                    onClick={() => pick(r.patient_id)}
                    className="w-full px-3 py-2 text-left hover:bg-primary/5 flex items-center justify-between gap-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 ${b.bg} ${b.text}`}>
                        {b.label}
                      </span>
                      <span className="text-sm font-semibold text-text-main truncate">
                        {r.name}
                      </span>
                    </div>
                    <span className="text-[11px] text-text-muted whitespace-nowrap">
                      {r.mrn ?? "—"} · {r.contact_number ?? "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── OPD form ─────────────────────────────────────────────────────────────
const VISIT_TYPES = [
  { value: "GENERAL", label: "General" },
  { value: "SPECIALIST", label: "Specialist" },
  { value: "DOCTOR", label: "Specific Doctor" },
];

function OPDForm({ scanResult, departments, doctors, onGenerated, onReprint }) {
  const patient = scanResult?.patient;
  const [visitType, setVisitType] = useState("GENERAL");
  const [departmentId, setDepartmentId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Reset form when scan changes.
  useEffect(() => {
    setVisitType("GENERAL");
    setDepartmentId("");
    setDoctorId("");
    setNote("");
  }, [scanResult?.patient?.id]);

  // Doctors filtered by selected department.
  const filteredDoctors = useMemo(
    () =>
      doctors.filter(
        (d) =>
          d.is_active !== false &&
          (!departmentId || d.department_id === Number(departmentId)),
      ),
    [doctors, departmentId],
  );

  if (!patient) {
    return (
      <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-8 text-center">
        <p className="text-sm text-text-muted">
          Scan a user to prefill the OPD form.
        </p>
      </div>
    );
  }

  // Non-PATIENT users (doctors / employees / visitors / relatives) are
  // surfaced for awareness but do not get an OPD token. Show an
  // info card instead of the form.
  if (!isPatient(patient)) {
    return <NonPatientCard user={patient} confidence={scanResult?.confidence} />;
  }

  const submit = async () => {
    if (!departmentId) {
      toast.warn("Choose a department.");
      return;
    }
    if (visitType === "DOCTOR" && !doctorId) {
      toast.warn("Choose a doctor for a Doctor-specific visit.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await createVisit({
        patient_id: patient.id,
        visit_type: visitType,
        department_id: Number(departmentId),
        doctor_id: doctorId ? Number(doctorId) : null,
        note: note.trim() || null,
        source: "FACE_SCAN",
      });
      toast.success(`Token ${result.visit.token_number} generated.`);
      onGenerated(result);
    } catch (err) {
      console.error(err);
      toast.error(`Generate failed: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const fieldClass =
    "w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-sm font-semibold outline-none";

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex justify-between items-center">
        <h3 className="text-sm font-bold text-text-main">OPD Form</h3>
        {scanResult?.confidence != null && (
          <span className="text-[11px] text-text-muted font-semibold">
            Match {(scanResult.confidence * 100).toFixed(1)}%
          </span>
        )}
      </div>

      <div className="p-5 flex flex-col gap-4">
        {/* Patient summary chips */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Chip label="MRN" value={patient.mrn} />
          <Chip label="Name" value={patient.name} />
          <Chip label="Age" value={patient.age ?? "—"} />
          <Chip label="Gender" value={patient.gender ?? "—"} />
          <Chip label="Contact" value={patient.contact_number ?? "—"} />
          <Chip label="ID" value={`#${patient.id}`} />
        </div>

        {/* Visit type */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
            Visit Type
          </label>
          <div className="flex gap-2">
            {VISIT_TYPES.map((vt) => (
              <button
                key={vt.value}
                onClick={() => setVisitType(vt.value)}
                className={`flex-1 px-3 py-2 rounded-xl text-xs font-semibold border transition-all ${
                  visitType === vt.value
                    ? "bg-primary text-white border-primary"
                    : "bg-card text-text-main border-primary/15 hover:bg-primary/5"
                }`}
              >
                {vt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Department */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
            Department <span className="text-danger">*</span>
          </label>
          <CustomSelect
            value={departmentId}
            onChange={(e) => {
              setDepartmentId(e.target.value);
              setDoctorId("");
            }}
            placeholder="Select…"
            options={departments
              .filter((d) => d.is_active !== false)
              .map((d) => ({
                value: d.id,
                label: `${d.name} (${d.token_prefix})`,
              }))}
          />
        </div>

        {/* Doctor (shown for SPECIALIST/DOCTOR, required for DOCTOR) */}
        {visitType !== "GENERAL" && (
          <div className="flex flex-col gap-1.5">
            <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
              Doctor {visitType === "DOCTOR" && <span className="text-danger">*</span>}
            </label>
            <CustomSelect
              value={doctorId}
              onChange={(e) => setDoctorId(e.target.value)}
              disabled={!departmentId}
              placeholder={departmentId ? "Select…" : "Pick a department first"}
              options={filteredDoctors.map((d) => ({
                value: d.id,
                label: d.specialty ? `${d.name} — ${d.specialty}` : d.name,
              }))}
            />
          </div>
        )}

        {/* Note */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
            Note
          </label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Symptoms, priority, anything else…"
            rows={3}
            className={`${fieldClass} resize-none`}
          />
        </div>

        <button
          onClick={submit}
          disabled={submitting}
          className={`mt-1 py-3 rounded-xl font-bold w-full shadow-md transition-all ${
            submitting
              ? "bg-primary/8 text-text-light cursor-not-allowed"
              : "gradient-primary text-white hover:opacity-90 shadow-lg shadow-primary/25"
          }`}
        >
          {submitting ? "Generating…" : "Generate Token"}
        </button>
      </div>
    </div>
  );
}

function Chip({ label, value }) {
  return (
    <div className="bg-background border border-primary/10 rounded-lg px-2.5 py-1.5">
      <div className="text-[9px] font-bold text-text-light uppercase tracking-wider">
        {label}
      </div>
      <div className="text-xs font-semibold text-text-main truncate">{value}</div>
    </div>
  );
}

// "Others in frame" strip — surfaces every other recognised face from the
// last scan so the operator can one-click swap to a different person
// when the queue stacks people side-by-side. Rendered below the patient
// summary in OPDForm (and inside NonPatientCard) when the scan saw
// more than one identified face. Clicking a row calls
// onPickCandidate(candidate) which the parent uses to swap
// scanResult.patient + confidence in place.
function OthersInFrame({ candidates, currentPatientId, unrecognisedCount, onPick }) {
  const others = (candidates || []).filter(
    (c) => c && c.patient_id !== currentPatientId,
  );
  if (others.length === 0 && !unrecognisedCount) return null;
  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex items-center justify-between">
        <span className="text-sm font-bold text-text-main">
          Others in frame ({others.length}
          {unrecognisedCount ? ` + ${unrecognisedCount} unknown` : ""})
        </span>
        <span className="text-[10px] text-text-light">click to swap</span>
      </div>
      {others.length > 0 && (
        <div className="divide-y divide-primary/10 max-h-80 overflow-y-auto">
          {others.map((c) => {
            const t = c.patient?.user_type || "PATIENT";
            const badge = TYPE_BADGE[t] || TYPE_BADGE.PATIENT;
            const subtitle =
              t === "PATIENT"
                ? c.patient?.mrn
                  ? `MRN ${c.patient.mrn}`
                  : "Patient"
                : t === "DOCTOR"
                  ? c.patient?.specialty || "Doctor"
                  : t === "EMPLOYEE"
                    ? c.patient?.role || "Employee"
                    : t === "VISITOR"
                      ? c.patient?.purpose || "Visitor"
                      : "Relative";
            return (
              <button
                key={c.patient_id}
                onClick={() => onPick(c)}
                className="w-full px-3 py-2 flex items-center gap-2 hover:bg-primary/5 transition-colors text-left cursor-pointer"
              >
                <span
                  className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 ${badge.bg} ${badge.text}`}
                >
                  {badge.label}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-text-main truncate">
                    {c.name}
                  </div>
                  <div className="text-[10px] text-text-light truncate">
                    {subtitle}
                  </div>
                </div>
                <span className="text-[10px] font-mono text-text-muted shrink-0">
                  {Math.round((c.confidence || 0) * 100)}%
                </span>
              </button>
            );
          })}
        </div>
      )}
      {unrecognisedCount > 0 && others.length === 0 && (
        <div className="px-3 py-2 text-[10px] text-text-light">
          {unrecognisedCount} unrecognised face{unrecognisedCount === 1 ? "" : "s"} in the frame.
        </div>
      )}
    </div>
  );
}

// Non-PATIENT result card — OPD form is patient-only; for doctors,
// employees, visitors and relatives we just confirm identity and link
// out to the profile.
function NonPatientCard({ user, confidence }) {
  const badge = TYPE_BADGE[user.user_type] || TYPE_BADGE.PATIENT;
  const t = user.user_type;
  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-bold text-text-main">Identified</h3>
          <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${badge.bg} ${badge.text}`}>
            {badge.label}
          </span>
        </div>
        {confidence != null && (
          <span className="text-[11px] text-text-muted font-semibold">
            Match {(confidence * 100).toFixed(1)}%
          </span>
        )}
      </div>

      <div className="p-5 flex flex-col gap-4">
        <div>
          <div className="text-lg font-extrabold text-text-main">{user.name}</div>
          <div className="text-[11px] text-text-light font-mono mt-0.5">
            ID: #{user.id}
            {user.contact_number ? ` · ${user.contact_number}` : ""}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          {t === "DOCTOR" && (
            <>
              {user.specialty && <Chip label="Specialty" value={user.specialty} />}
              {user.opd_department_id != null && <Chip label="OPD Dept" value={`#${user.opd_department_id}`} />}
              {user.opd_room_id != null && <Chip label="OPD Room" value={`#${user.opd_room_id}`} />}
              {user.department && <Chip label="Department" value={user.department} />}
            </>
          )}
          {t === "EMPLOYEE" && (
            <>
              {user.role && <Chip label="Role" value={user.role} />}
              {user.staff_department && <Chip label="Department" value={user.staff_department} />}
            </>
          )}
          {t === "VISITOR" && user.purpose && (
            <div className="col-span-2">
              <Chip label="Purpose" value={user.purpose} />
            </div>
          )}
          {t === "RELATIVE" && (
            <div className="col-span-2 text-xs text-text-muted">
              Relative — see profile for linked patient.
            </div>
          )}
        </div>

        {user.note && (
          <div>
            <div className="text-[10px] font-bold text-text-light uppercase tracking-wider mb-1">
              Note
            </div>
            <div className="text-xs text-text-main bg-background rounded-lg px-3 py-2 border border-primary/10">
              {user.note}
            </div>
          </div>
        )}

        <div className="rounded-xl bg-amber-50 border border-amber-200 text-amber-800 dark:bg-amber-500/10 dark:border-amber-500/30 dark:text-amber-300 text-xs font-semibold px-3 py-2">
          OPD tokens can only be issued to patients. This is a {badge.label.toLowerCase()}.
        </div>

        <Link
          to={`/patients/${user.id}`}
          className="block text-center py-2.5 rounded-xl font-bold bg-primary text-white hover:opacity-90 shadow-sm"
        >
          View profile
        </Link>
      </div>
    </div>
  );
}

// ── Token slip (printable) ───────────────────────────────────────────────
function TokenSlip({ slip, onPrint }) {
  const [qrDataUrl, setQrDataUrl] = useState(null);

  // Generate the QR after we have a visit. We encode the API URL for the
  // visit so anyone scanning it can fetch the full record.
  useEffect(() => {
    if (!slip?.visit?.id) {
      setQrDataUrl(null);
      return;
    }
    const payload = `${API_URL}/frontdesk/visits/${slip.visit.id}`;
    QRCode.toDataURL(payload, { margin: 1, width: 240 })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(null));
  }, [slip?.visit?.id]);

  if (!slip) return null;
  const v = slip.visit;
  const p = slip.patient;
  const issued = v.created_at ? new Date(v.created_at) : new Date();

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex justify-between items-center">
        <h3 className="text-sm font-bold text-text-main">Token Slip</h3>
        <button
          onClick={onPrint}
          className="text-xs px-3 py-1.5 bg-primary text-white rounded-lg font-semibold hover:opacity-90"
        >
          Print
        </button>
      </div>

      {/* On-screen preview */}
      <div className="p-5">
        <div className="border-2 border-dashed border-primary/20 rounded-xl p-5 text-center">
          <div className="text-[10px] font-bold uppercase tracking-widest text-text-muted">
            OPD Token
          </div>
          <div className="text-5xl font-extrabold text-primary my-2 tracking-tight">
            {v.token_number}
          </div>
          <div className="text-sm font-bold text-text-main">{p.name}</div>
          <div className="text-xs text-text-muted mt-0.5">
            MRN {p.mrn} · {p.age ?? "—"} / {p.gender ?? "—"}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-left">
            <Chip label="Department" value={v.department_name ?? "—"} />
            <Chip label="Room" value={v.room_name ?? "—"} />
            <Chip label="Doctor" value={v.doctor_name ?? "—"} />
            <Chip label="Visit" value={v.visit_type} />
          </div>
          {v.note && (
            <div className="mt-3 text-left">
              <div className="text-[9px] font-bold text-text-light uppercase tracking-wider">
                Note
              </div>
              <div className="text-xs text-text-main">{v.note}</div>
            </div>
          )}
          {qrDataUrl && (
            <div className="mt-3 flex flex-col items-center">
              <img
                src={qrDataUrl}
                alt={`QR for visit ${v.id}`}
                className="w-24 h-24"
              />
              <div className="text-[9px] text-text-light mt-1">
                Scan for visit #{v.id}
              </div>
            </div>
          )}
          <div className="mt-3 text-[10px] text-text-muted">
            Issued {issued.toLocaleString()}
          </div>
        </div>
      </div>

      {/* Hidden print-only block (consumed by @media print rules). */}
      <div className="print-slip" aria-hidden="true">
        <div className="ps-title">OPD TOKEN</div>
        <div className="ps-token">{v.token_number}</div>
        <div className="ps-name">{p.name}</div>
        <div className="ps-sub">
          MRN {p.mrn} · {p.age ?? "—"} / {p.gender ?? "—"}
        </div>
        <div className="ps-row"><b>Dept:</b> {v.department_name ?? "—"}</div>
        <div className="ps-row"><b>Room:</b> {v.room_name ?? "—"}</div>
        <div className="ps-row"><b>Doctor:</b> {v.doctor_name ?? "—"}</div>
        <div className="ps-row"><b>Visit:</b> {v.visit_type}</div>
        {v.note && <div className="ps-row"><b>Note:</b> {v.note}</div>}
        {qrDataUrl && (
          <div className="ps-qr">
            <img src={qrDataUrl} alt="" />
          </div>
        )}
        <div className="ps-foot">{issued.toLocaleString()} · #{v.id}</div>
      </div>
    </div>
  );
}

// ── Today's queue ────────────────────────────────────────────────────────
function TodayQueue({ visits, onStatusChange, onReprint }) {
  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex justify-between items-center">
        <h3 className="text-sm font-bold text-text-main">Today's Queue</h3>
        <span className="text-[11px] text-text-muted font-semibold">
          {visits.length} token{visits.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="max-h-96 overflow-y-auto overflow-x-auto">
        {visits.length === 0 ? (
          <p className="text-xs text-text-muted text-center py-8">
            No tokens issued today.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-background sticky top-0">
              <tr className="text-left text-[10px] font-bold text-text-muted uppercase tracking-wider">
                <th className="px-3 py-2 whitespace-nowrap">Token</th>
                <th className="px-3 py-2 whitespace-nowrap">Patient</th>
                <th className="px-3 py-2 whitespace-nowrap">Dept</th>
                <th className="px-3 py-2 whitespace-nowrap">Room</th>
                <th className="px-3 py-2 whitespace-nowrap">Status</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {visits.map((v) => (
                <tr key={v.id} className="border-t border-primary/8">
                  <td className="px-3 py-2 font-bold text-primary whitespace-nowrap">
                    {v.token_number}
                  </td>
                  <td className="px-3 py-2 font-semibold text-text-main whitespace-nowrap">
                    #{v.patient_id}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{v.department_name ?? "—"}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{v.room_name ?? "—"}</td>
                  <td className="px-3 py-2">
                    <CustomSelect
                      value={v.status}
                      onChange={(e) => onStatusChange(v.id, e.target.value)}
                      size="sm"
                      className="min-w-28"
                      options={[
                        { value: "WAITING", label: "Waiting" },
                        { value: "IN_CONSULT", label: "In Consult" },
                        { value: "DONE", label: "Done" },
                        { value: "CANCELLED", label: "Cancelled" },
                      ]}
                    />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => onReprint(v.id)}
                      className="text-[10px] px-2 py-1 rounded-md bg-primary/8 text-primary font-semibold hover:bg-primary/15"
                    >
                      Reprint
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function FrontDesk() {
  const [scanResult, setScanResult] = useState(null);
  const [slip, setSlip] = useState(null);
  const [departments, setDepartments] = useState([]);
  const [doctors, setDoctors] = useState([]);
  const [todayVisits, setTodayVisits] = useState([]);

  const reloadConfig = async () => {
    try {
      const [deps, docs] = await Promise.all([
        listDepartments(),
        listDoctors(),
      ]);
      setDepartments(deps.departments || []);
      setDoctors(docs.doctors || []);
    } catch (err) {
      console.error(err);
      toast.error(`Failed to load departments/doctors: ${err.message}`);
    }
  };

  const reloadQueue = async () => {
    try {
      const res = await listTodayVisits();
      setTodayVisits(res.visits || []);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    reloadConfig();
    reloadQueue();
  }, []);

  // WS live updates.
  useSocketMessages((data) => {
    if (
      data?.type === "frontdesk.visit.created" ||
      data?.type === "frontdesk.visit.status_changed"
    ) {
      reloadQueue();
    }
  });

  // Lightweight poll as a fallback if WS is offline.
  useEffect(() => {
    const id = setInterval(reloadQueue, 15000);
    return () => clearInterval(id);
  }, []);

  const handleGenerated = (result) => {
    setSlip(result);
    reloadQueue();
  };

  // Operator picks a non-primary face from the OthersInFrame strip.
  // Swap patient + confidence in-place; keep candidates intact so the
  // strip stays visible (with the picked one now hidden as "current").
  // Toasts so the operator gets confirmation the swap happened.
  const handlePickCandidate = (candidate) => {
    if (!candidate?.patient) return;
    setScanResult((prev) =>
      prev
        ? {
            ...prev,
            patient: candidate.patient,
            confidence: candidate.confidence ?? prev.confidence,
          }
        : prev,
    );
    toast.info(`Switched to ${candidate.patient.name}`);
  };

  const handleReset = () => {
    setScanResult(null);
    setSlip(null);
  };

  const handlePrint = async () => {
    if (slip?.visit?.id) {
      try {
        await reprintVisit(slip.visit.id);
      } catch {
        // best effort — still print
      }
    }
    window.print();
  };

  const handleQueueStatus = async (visitId, status) => {
    try {
      await updateVisitStatus(visitId, status);
      reloadQueue();
    } catch (err) {
      toast.error(`Status update failed: ${err.message}`);
    }
  };

  const handleQueueReprint = async (visitId) => {
    try {
      const result = await reprintVisit(visitId);
      setSlip(result);
      window.print();
    } catch (err) {
      toast.error(`Reprint failed: ${err.message}`);
    }
  };

  const noDepartments = departments.length === 0;

  return (
    <div className="w-full pt-2 px-4 lg:px-6 frontdesk-root">
      {/* Top bar: title + link to admin config */}
      <div className="flex items-center justify-between mb-4 px-1">
        <h1 className="text-xl font-extrabold text-text-main">Front Desk</h1>
        <Link
          to="/settings?section=frontdesk"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-primary/10 text-primary hover:bg-primary/15 border border-primary/15 transition-all"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          Manage Departments / Doctors / Rooms
        </Link>
      </div>

      {noDepartments && (
        <div className="mb-4 rounded-xl border border-amber-300/40 bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300 px-4 py-3 flex items-center justify-between">
          <div className="text-sm font-semibold">
            No departments configured yet. Add one before generating tokens.
          </div>
          <Link
            to="/settings?section=frontdesk"
            className="text-xs font-bold px-3 py-1.5 rounded-lg bg-amber-600 text-white hover:bg-amber-700"
          >
            Open Admin
          </Link>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1.5fr] gap-5">
        <div className="flex flex-col gap-5">
          <ScanPanel onScanned={setScanResult} onReset={handleReset} />
          <ManualSearch onPicked={setScanResult} />
          {scanResult?.candidates && scanResult.candidates.length > 1 && (
            <OthersInFrame
              candidates={scanResult.candidates}
              currentPatientId={scanResult.patient?.id}
              unrecognisedCount={scanResult.unrecognised_count}
              onPick={handlePickCandidate}
            />
          )}
        </div>
        <div className="flex flex-col gap-5">
          <OPDForm
            scanResult={scanResult}
            departments={departments}
            doctors={doctors}
            onGenerated={handleGenerated}
          />
        </div>
        <div className="flex flex-col gap-5">
          {slip ? (
            <TokenSlip slip={slip} onPrint={handlePrint} />
          ) : (
            <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-8 text-center">
              <p className="text-sm text-text-muted">
                The token slip will appear here after generation.
              </p>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-5">
          <TodayQueue
            visits={todayVisits}
            onStatusChange={handleQueueStatus}
            onReprint={handleQueueReprint}
          />
        </div>
      </div>
    </div>
  );
}
