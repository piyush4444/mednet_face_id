/**
 * KioskApp — the entry-gate kiosk (separate URL / bundle from the admin SPA).
 *
 * One cycle: SCANNING → face matched → punch recorded server-side →
 * GREETING (welcome / goodbye; patients get pre-register buttons) →
 * back to SCANNING. Unknown faces get a short "please visit the front
 * desk" message (front desk registers — the kiosk never self-registers).
 *
 * Device binding: camera, mode (IN/OUT), facility and kiosk serial are
 * chosen once and persisted in localStorage — the kiosk boots straight
 * into scan mode afterwards. The duplicate-punch window is enforced
 * server-side; the client additionally pauses the loop during greetings.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "../config";

const CONFIG_KEY = "iris-kiosk-config";
const SCAN_INTERVAL_MS = 1500;
const GREETING_MS = 4500;
const PATIENT_CHOICE_MS = 15000;
const UNKNOWN_MS = 4000;
const UNKNOWN_STREAK_TO_SHOW = 2;

// ── Config persistence ───────────────────────────────────────────────────
function loadConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    if (!raw) return null;
    const cfg = JSON.parse(raw);
    return cfg && cfg.mode && cfg.facilityId ? cfg : null;
  } catch {
    return null;
  }
}

function saveConfig(cfg) {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(cfg));
}

// ── Auth-aware fetch ───────────────────────────────────────────────────────
// The kiosk runs as a minimal "kiosk device" login (kiosk.operate +
// streams.view). All calls send the session cookie (same-origin default,
// but we set credentials:'include' so the dev proxy works too); mutating
// calls echo the readable iris_csrf cookie in X-CSRF-Token (double-submit
// CSRF). When AUTH_ENABLED is off, guards + CSRF are inert and this all
// still works. A 401 surfaces as an AuthError so the UI can show login.
class AuthError extends Error {}

function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

function baseHeaders(mutating) {
  const h = { "ngrok-skip-browser-warning": "true" };
  if (mutating) {
    const csrf = getCookie("iris_csrf");
    if (csrf) h["X-CSRF-Token"] = csrf;
  }
  return h;
}

async function jsonOrThrow(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty body */
  }
  if (res.status === 401) {
    throw new AuthError((body && (body.detail || body.message)) || "not authenticated");
  }
  if (!res.ok) {
    throw new Error((body && (body.detail || body.message)) || `HTTP ${res.status}`);
  }
  return body;
}

async function apiLogin(username, password) {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { ...baseHeaders(false), "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  // Login failures come back 401/429 with a detail — surface the message
  // rather than the AuthError→login loop.
  let body = null;
  try { body = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    throw new Error((body && (body.detail || body.message)) || `Login failed (${res.status})`);
  }
  return body;
}

async function apiScan(blob, cfg) {
  const form = new FormData();
  form.append("image", blob, "frame.jpg");
  form.append("facility_id", String(cfg.facilityId));
  form.append("mode", cfg.mode);
  if (cfg.serial) form.append("kiosk_serial", cfg.serial);
  if (cfg.cameraLabel) form.append("camera_label", cfg.cameraLabel);
  const res = await fetch(`${API_URL}/kiosk/scan`, {
    method: "POST",
    credentials: "include",
    headers: baseHeaders(true),
    body: form,
  });
  return jsonOrThrow(res);
}

async function apiPrereg(payload) {
  const res = await fetch(`${API_URL}/kiosk/prereg`, {
    method: "POST",
    credentials: "include",
    headers: { ...baseHeaders(true), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return jsonOrThrow(res);
}

async function apiNewMrn() {
  const res = await fetch(`${API_URL}/kiosk/mrn/new`, {
    credentials: "include",
    headers: baseHeaders(false),
  });
  return jsonOrThrow(res);
}

// Central config a registered kiosk pulls by serial. 404 → not registered
// (fall back to manual setup); 401 → AuthError (show login).
async function apiKioskConfig(serial) {
  const res = await fetch(
    `${API_URL}/kiosk/config?serial=${encodeURIComponent(serial)}`,
    { credentials: "include", headers: baseHeaders(false) },
  );
  if (res.status === 404) return null;
  return jsonOrThrow(res);
}

// Minimal facility + camera lists for setup — served under kiosk.operate so
// the device account never needs admin-tier facility or camera APIs.
async function apiSetupOptions() {
  const res = await fetch(`${API_URL}/kiosk/setup-options`, {
    credentials: "include",
    headers: baseHeaders(false),
  });
  return jsonOrThrow(res);
}

// One clean (overlay-free) JPEG from a running system camera — the same
// endpoint the Front Desk uses. The main camera pipeline is the frame
// source, so browsers never need to touch RTSP.
async function apiSnapshot(cameraId) {
  const res = await fetch(
    `${API_URL}/stream/${encodeURIComponent(cameraId)}/snapshot`,
    { credentials: "include", headers: baseHeaders(false) },
  );
  if (res.status === 401) throw new AuthError("not authenticated");
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail || `Snapshot failed (${res.status})`);
  }
  return await res.blob();
}

// MJPEG live-stream URL for the <img> tag (same feed the dashboard shows).
function streamUrl(cameraId) {
  return `${API_URL}/stream/${encodeURIComponent(cameraId)}`;
}

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "numeric", month: "long", hour: "numeric",
      minute: "2-digit", hour12: true,
    });
  } catch {
    return iso;
  }
}

// ── Root ─────────────────────────────────────────────────────────────────
export default function KioskApp() {
  const [config, setConfig] = useState(loadConfig);
  // Set when any call returns 401 (auth on + no/expired session). Cleared on
  // successful device login. Stays false forever when AUTH_ENABLED is off.
  const [needsLogin, setNeedsLogin] = useState(false);
  // false → serial-entry boot screen (pull central config); true → the
  // manual pickers (fallback for an unregistered kiosk).
  const [manual, setManual] = useState(false);

  const accept = (cfg) => { saveConfig(cfg); setConfig(cfg); };

  if (needsLogin) {
    return <LoginScreen onLoggedIn={() => setNeedsLogin(false)} />;
  }
  if (!config && !manual) {
    return (
      <ConfigLoadScreen
        onLoaded={accept}
        onManual={() => setManual(true)}
        onAuthError={() => setNeedsLogin(true)}
      />
    );
  }
  if (!config) {
    return (
      <SetupScreen
        onDone={accept}
        onAuthError={() => setNeedsLogin(true)}
      />
    );
  }
  return (
    <KioskScreen
      config={config}
      onReconfigure={() => {
        localStorage.removeItem(CONFIG_KEY);
        setConfig(null);
      }}
      onAuthError={() => setNeedsLogin(true)}
    />
  );
}

// ── Device login (only shown when AUTH_ENABLED and no valid session) ──────
function LoginScreen({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e?.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiLogin(username.trim(), password);
      onLoggedIn();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const field =
    "w-full bg-background border border-primary/15 focus:border-primary rounded-xl px-4 py-3 text-base font-semibold outline-none";

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <form onSubmit={submit}
        className="bg-card rounded-3xl shadow-xl border border-primary/10 p-8 w-full max-w-sm flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-text-main">
            <span className="text-gradient">Iris</span> Kiosk
          </h1>
          <p className="text-sm text-text-muted mt-1">
            Sign in with this kiosk's device account.
          </p>
        </div>
        {error && (
          <div className="text-sm font-semibold text-danger bg-danger/10 rounded-xl px-4 py-3">
            {error}
          </div>
        )}
        <input className={field} placeholder="Device username" autoFocus
          value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className={field} type="password" placeholder="Password"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <button type="submit" disabled={busy}
          className="bg-primary text-white rounded-xl px-4 py-3.5 text-base font-extrabold hover:opacity-90 disabled:opacity-50">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

// ── Config load (central config by serial) ───────────────────────────────
function ConfigLoadScreen({ onLoaded, onManual, onAuthError }) {
  const [serial, setSerial] = useState(
    () => localStorage.getItem("iris-kiosk-serial") || "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    const s = serial.trim();
    if (!s) { setError("Enter this kiosk's serial."); return; }
    setBusy(true);
    setError("");
    try {
      const cfg = await apiKioskConfig(s);
      if (!cfg) {
        setError(`Serial "${s}" is not registered. Register it in the admin console, or set up manually.`);
        setBusy(false);
        return;
      }
      localStorage.setItem("iris-kiosk-serial", s);
      onLoaded({
        sourceType: cfg.source_type,
        cameraId: cfg.camera_id || undefined,
        cameraLabel: cfg.camera_id || s,
        facilityId: cfg.facility_id,
        facilityName: cfg.facility_name || "",
        mode: cfg.mode,
        serial: s,
      });
    } catch (err) {
      if (err instanceof AuthError) { onAuthError(); return; }
      setError(err.message);
      setBusy(false);
    }
  };

  const field =
    "w-full bg-background border border-primary/15 focus:border-primary rounded-xl px-4 py-3 text-base font-semibold outline-none";

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="bg-card rounded-3xl shadow-xl border border-primary/10 p-8 w-full max-w-sm flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-text-main">
            <span className="text-gradient">Iris</span> Kiosk
          </h1>
          <p className="text-sm text-text-muted mt-1">
            Enter this kiosk's serial to load its configuration.
          </p>
        </div>
        {error && (
          <div className="text-sm font-semibold text-danger bg-danger/10 rounded-xl px-4 py-3">
            {error}
          </div>
        )}
        <input
          className={field}
          placeholder="Kiosk serial (e.g. SR4536)"
          autoFocus
          value={serial}
          onChange={(e) => setSerial(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
        />
        <button onClick={load} disabled={busy}
          className="bg-primary text-white rounded-xl px-4 py-3.5 text-base font-extrabold hover:opacity-90 disabled:opacity-50">
          {busy ? "Loading…" : "Start kiosk"}
        </button>
        <button onClick={onManual}
          className="text-sm font-semibold text-text-muted hover:text-text-main">
          Set up manually instead
        </button>
      </div>
    </div>
  );
}

// ── Setup (one-time per device) ──────────────────────────────────────────
function SetupScreen({ onDone, onAuthError }) {
  // sourceType: "webcam" (this device's USB/built-in cam) or "system" (a
  // registered IP/RTSP camera served by the main camera pipeline).
  const [sourceType, setSourceType] = useState("webcam");
  const [webcams, setWebcams] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [systemCameras, setSystemCameras] = useState([]);
  const [systemCameraId, setSystemCameraId] = useState("");
  const [facility, setFacility] = useState(null);
  const [facilityId, setFacilityId] = useState("");
  // AUTO = one kiosk handles both directions (in & out), resolved per person
  // from their last punch. IN/OUT lock a one-way gate.
  const [mode, setMode] = useState("AUTO");
  const [serial, setSerial] = useState("");
  const [error, setError] = useState("");

  // Singleton facility + registered cameras via the kiosk-scoped endpoint
  // (no admin-tier reads, no camera permission needed).
  useEffect(() => {
    (async () => {
      try {
        const opts = await apiSetupOptions();
        const fac = opts.facility || null;
        setFacility(fac);
        if (fac) setFacilityId(String(fac.id));
        const cams = opts.cameras || [];
        setSystemCameras(cams);
        if (cams[0]) setSystemCameraId(cams[0].camera_id);
      } catch (err) {
        if (err instanceof AuthError) { onAuthError(); return; }
        setError(`Could not load setup options: ${err.message}`);
      }
    })();
  }, [onAuthError]);

  // Enumerate local webcams only when that source is chosen (asks for the
  // camera permission at that point, not upfront).
  useEffect(() => {
    if (sourceType !== "webcam") return;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        stream.getTracks().forEach((t) => t.stop());
        const devices = await navigator.mediaDevices.enumerateDevices();
        const cams = devices.filter((d) => d.kind === "videoinput");
        setWebcams(cams);
        if (cams[0]) setDeviceId(cams[0].deviceId);
      } catch {
        setError("Camera access denied — allow camera permission and reload.");
      }
    })();
  }, [sourceType]);

  const submit = () => {
    if (!facilityId) {
      setError("Facility configuration is unavailable.");
      return;
    }
    const common = {
      sourceType,
      facilityId: Number(facilityId),
      facilityName: facility?.display_name || facility?.name || "",
      mode,
      serial: serial.trim(),
    };
    if (sourceType === "system") {
      if (!systemCameraId) {
        setError("Select a system camera.");
        return;
      }
      const cam = systemCameras.find((c) => c.camera_id === systemCameraId);
      onDone({
        ...common,
        cameraId: systemCameraId,
        cameraLabel: cam?.name || systemCameraId,
      });
    } else {
      if (!deviceId) {
        setError("Select a camera.");
        return;
      }
      const cam = webcams.find((c) => c.deviceId === deviceId);
      onDone({ ...common, deviceId, cameraLabel: cam?.label || "kiosk-cam" });
    }
  };

  const field =
    "w-full bg-background border border-primary/15 focus:border-primary rounded-xl px-4 py-3 text-base font-semibold outline-none";

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="bg-card rounded-3xl shadow-xl border border-primary/10 p-8 w-full max-w-md flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-extrabold text-text-main">Kiosk setup</h1>
          <p className="text-sm text-text-muted mt-1">
            One-time configuration for this device.
          </p>
        </div>
        {error && (
          <div className="text-sm font-semibold text-danger bg-danger/10 rounded-xl px-4 py-3">
            {error}
          </div>
        )}
        <label className="text-xs font-bold text-text-muted -mb-2">Camera source</label>
        <div className="grid grid-cols-2 gap-2">
          {[
            ["webcam", "This device's webcam"],
            ["system", "IP / system camera"],
          ].map(([val, lbl]) => (
            <button
              key={val}
              onClick={() => { setError(""); setSourceType(val); }}
              className={`rounded-xl px-3 py-2.5 text-sm font-bold transition-all ${
                sourceType === val
                  ? "bg-primary text-white shadow-lg"
                  : "bg-background border border-primary/15 text-text-muted hover:border-primary/40"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>

        {sourceType === "webcam" ? (
          <>
            <label className="text-xs font-bold text-text-muted -mb-2">Webcam</label>
            <select className={field} value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
              {webcams.map((c) => (
                <option key={c.deviceId} value={c.deviceId}>
                  {c.label || `Camera ${c.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
          </>
        ) : (
          <>
            <label className="text-xs font-bold text-text-muted -mb-2">
              System camera (from the camera roster)
            </label>
            {systemCameras.length === 0 ? (
              <div className="text-xs font-semibold text-text-muted bg-background border border-primary/15 rounded-xl px-4 py-3">
                No registered cameras found. Add one under Settings → Cameras, or
                use this device's webcam.
              </div>
            ) : (
              <select className={field} value={systemCameraId}
                onChange={(e) => setSystemCameraId(e.target.value)}>
                {systemCameras.map((c) => (
                  <option key={c.camera_id} value={c.camera_id}>
                    {c.name} · {c.role}{c.active ? "" : " (inactive)"}
                  </option>
                ))}
              </select>
            )}
          </>
        )}

        <label className="text-xs font-bold text-text-muted -mb-2">Mode</label>
        <div className="grid grid-cols-3 gap-2">
          {[
            ["AUTO", "Automatic"],
            ["IN", "IN only"],
            ["OUT", "OUT only"],
          ].map(([m, lbl]) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-xl px-3 py-3 text-sm font-extrabold transition-all ${
                mode === m
                  ? "bg-primary text-white shadow-lg"
                  : "bg-background border border-primary/15 text-text-muted hover:border-primary/40"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
        <p className="text-[11px] font-semibold text-text-muted -mt-2">
          Automatic detects punch in vs out per person from their last punch —
          one kiosk covers both directions. Use IN/OUT only for a one-way gate.
        </p>
        <label className="text-xs font-bold text-text-muted -mb-2">
          Kiosk serial (for attendance export, optional)
        </label>
        <input
          className={field}
          placeholder="e.g. SR4536"
          value={serial}
          onChange={(e) => setSerial(e.target.value)}
        />
        <button
          onClick={submit}
          className="bg-primary text-white rounded-xl px-4 py-3.5 text-base font-extrabold hover:opacity-90 mt-2"
        >
          Start kiosk
        </button>
      </div>
    </div>
  );
}

// ── Main kiosk screen ────────────────────────────────────────────────────
// phase: scanning | greeting | unknown | prereg | prereg-done
function KioskScreen({ config, onReconfigure, onAuthError }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const inFlight = useRef(false);
  const phaseRef = useRef("scanning");
  const unknownStreak = useRef(0);
  const timerRef = useRef(null);

  const [phase, setPhase] = useState("scanning");
  const [scanResult, setScanResult] = useState(null);
  const [preregResult, setPreregResult] = useState(null);
  const [camError, setCamError] = useState("");

  const goto = useCallback((next, autoBackMs) => {
    phaseRef.current = next;
    setPhase(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    if (autoBackMs) {
      timerRef.current = setTimeout(() => {
        phaseRef.current = "scanning";
        setPhase("scanning");
        setScanResult(null);
        setPreregResult(null);
      }, autoBackMs);
    }
  }, []);

  const isSystemCam = config.sourceType === "system";

  // Webcam stream (local source only). System cameras display via the MJPEG
  // <img> below — the main pipeline owns the RTSP capture.
  useEffect(() => {
    if (isSystemCam) return;
    let stream;
    (async () => {
      try {
        // A central-config webcam kiosk has no specific browser deviceId —
        // fall back to the default camera.
        const video = config.deviceId
          ? { deviceId: { exact: config.deviceId }, width: 1280, height: 720 }
          : { width: 1280, height: 720 };
        stream = await navigator.mediaDevices.getUserMedia({ video });
        if (videoRef.current) videoRef.current.srcObject = stream;
      } catch {
        setCamError("Camera unavailable — check the connection and reload.");
      }
    })();
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, [config.deviceId, isSystemCam]);

  // Scan loop — one frame per tick, from the webcam canvas or a system-camera
  // snapshot, posted to the same /kiosk/scan endpoint.
  useEffect(() => {
    const grabBlob = async () => {
      if (isSystemCam) {
        // Clean JPEG straight from the running camera pipeline.
        return await apiSnapshot(config.cameraId);
      }
      const video = videoRef.current;
      if (!video || video.readyState < 2) return null;
      const canvas = canvasRef.current;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      return await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
    };

    const tick = async () => {
      if (phaseRef.current !== "scanning" || inFlight.current) return;
      inFlight.current = true;
      try {
        const blob = await grabBlob();
        if (!blob) return;
        const res = await apiScan(blob, config);
        if (phaseRef.current !== "scanning") return; // phase moved on meanwhile
        if (res.matched) {
          unknownStreak.current = 0;
          setScanResult(res);
          // Use the server-resolved direction (correct for AUTO too), not
          // the configured mode.
          const isPatientIn =
            res.user.person_type === "PATIENT" && res.punch?.visit_type === "IN";
          goto("greeting", isPatientIn ? PATIENT_CHOICE_MS : GREETING_MS);
        } else if (res.faces_detected > 0) {
          unknownStreak.current += 1;
          if (unknownStreak.current >= UNKNOWN_STREAK_TO_SHOW) {
            unknownStreak.current = 0;
            goto("unknown", UNKNOWN_MS);
          }
        } else {
          unknownStreak.current = 0;
        }
      } catch (err) {
        if (err instanceof AuthError) { onAuthError(); return; }
        console.warn("scan failed:", err.message);
      } finally {
        inFlight.current = false;
      }
    };
    const id = setInterval(tick, SCAN_INTERVAL_MS);
    return () => clearInterval(id);
  }, [config, goto, isSystemCam, onAuthError]);

  useEffect(() => () => timerRef.current && clearTimeout(timerRef.current), []);

  const submitPrereg = async (form) => {
    const payload = {
      user_id: scanResult.user.id,
      facility_id: config.facilityId,
      ...form,
    };
    try {
      const res = await apiPrereg(payload);
      setPreregResult(res.pre_registration);
      goto("prereg-done", 8000);
    } catch (err) {
      if (err instanceof AuthError) { onAuthError(); return; }
      throw err; // shown inline by the form
    }
  };

  const modeColor =
    config.mode === "IN" ? "bg-success/10 text-success"
      : config.mode === "OUT" ? "bg-danger/10 text-danger"
        : "bg-primary/10 text-primary";

  return (
    // Exactly one viewport, never scrolls; inner padding gives a safety
    // margin so content is never flush to the screen edge.
    <div className="h-screen w-screen overflow-hidden bg-background flex flex-col p-3 sm:p-4 gap-3">
      {/* Top bar */}
      <div className="shrink-0 flex items-center justify-between px-1">
        <div className="flex items-center gap-3">
          <span className="text-lg font-extrabold text-text-main">
            <span className="text-gradient">Iris</span> Kiosk
          </span>
          <span className="text-xs font-bold px-2.5 py-1 rounded-full bg-primary/10 text-primary">
            {config.facilityName || `Facility #${config.facilityId}`}
          </span>
          <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${modeColor}`}>
            MODE {config.mode}
          </span>
        </div>
        {/* Low-key reconfigure — staff only, double-click to avoid accidents */}
        <button
          onDoubleClick={onReconfigure}
          title="Double-click to reconfigure this kiosk"
          className="text-xs font-semibold text-text-light hover:text-text-muted"
        >
          ⚙
        </button>
      </div>

      {/* Body: camera + panel — fills remaining height, no page scroll */}
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[1fr_380px] gap-3 overflow-hidden">
        <div className="relative bg-black rounded-2xl overflow-hidden flex items-center justify-center min-h-0">
          {isSystemCam ? (
            // MJPEG feed from the main camera pipeline (overlays drawn server-side).
            <img
              src={streamUrl(config.cameraId)}
              alt="camera feed"
              className="w-full h-full object-cover"
              onError={() => setCamError("Camera stream unavailable — is the camera system running?")}
            />
          ) : (
            <video ref={videoRef} autoPlay playsInline muted className="w-full h-full object-cover" />
          )}
          <canvas ref={canvasRef} className="hidden" />
          {camError && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/80 text-white text-lg font-bold p-8 text-center">
              {camError}
            </div>
          )}
          {phase === "scanning" && !camError && (
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 px-5 py-2.5 rounded-full bg-black/60 text-white text-sm font-bold flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-success animate-pulse" />
              Look at the camera to {config.mode === "IN" ? "punch in"
                : config.mode === "OUT" ? "punch out" : "punch in or out"}
            </div>
          )}
        </div>

        <div className="bg-card border border-primary/10 rounded-2xl p-6 flex flex-col justify-center overflow-y-auto min-h-0">
          {phase === "scanning" && <ScanIdle mode={config.mode} />}
          {phase === "unknown" && <UnknownPanel />}
          {phase === "greeting" && scanResult && (
            <GreetingPanel
              result={scanResult}
              onPrereg={() => goto("prereg")}
              onVisitor={() => goto("scanning")}
            />
          )}
          {phase === "prereg" && scanResult && (
            <PreRegForm
              user={scanResult.user}
              onSubmit={submitPrereg}
              onCancel={() => goto("scanning")}
            />
          )}
          {phase === "prereg-done" && (
            <PreregDone result={preregResult} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Panels ───────────────────────────────────────────────────────────────
function ScanIdle({ mode }) {
  return (
    <div className="text-center flex flex-col items-center gap-4">
      <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
      </div>
      <h2 className="text-2xl font-extrabold text-text-main">
        {mode === "IN" ? "Welcome!" : mode === "OUT" ? "Leaving?" : "Hello!"}
      </h2>
      <p className="text-text-muted font-semibold">
        Please stand in front of the camera.
        <br />
        Your {mode === "IN" ? "punch-in" : mode === "OUT" ? "punch-out" : "punch"} is automatic.
      </p>
    </div>
  );
}

function UnknownPanel() {
  return (
    <div className="text-center flex flex-col items-center gap-4">
      <div className="w-16 h-16 rounded-full bg-warning/10 flex items-center justify-center text-3xl">
        👋
      </div>
      <h2 className="text-2xl font-extrabold text-text-main">Welcome!</h2>
      <p className="text-text-muted font-semibold">
        We couldn't recognize you yet.
        <br />
        <span className="text-text-main font-bold">
          Please visit the front desk to register.
        </span>
      </p>
    </div>
  );
}

function GreetingPanel({ result, onPrereg, onVisitor }) {
  const { user, punch, facility_name } = result;
  // The server resolves the real direction (AUTO included); trust it.
  const isIn = punch.visit_type === "IN";
  const staff = user.person_type === "EMPLOYEE" || user.person_type === "DOCTOR";
  const patientChoices = user.person_type === "PATIENT" && isIn;

  return (
    <div className="text-center flex flex-col items-center gap-4">
      {user.photo_url ? (
        <img
          src={user.photo_url.startsWith("http") ? user.photo_url : `${API_URL.replace(/\/api\/v1$/, "")}${user.photo_url}`}
          alt=""
          className="w-20 h-20 rounded-full object-cover ring-4 ring-primary/20"
        />
      ) : (
        <div className="w-20 h-20 rounded-full bg-primary/10 flex items-center justify-center text-3xl">
          {isIn ? "🙏" : "👋"}
        </div>
      )}
      <div>
        <h2 className="text-2xl font-extrabold text-text-main">
          {isIn ? "Welcome" : "Goodbye"}, {user.prefix ? `${user.prefix} ` : ""}{user.name}!
        </h2>
        <p className="text-sm font-bold text-text-muted mt-1">
          {user.person_type}{user.mrn ? ` · MRN ${user.mrn}` : ""}
        </p>
      </div>
      <div className="bg-primary/5 rounded-2xl px-6 py-4 w-full">
        <p className="text-xs font-bold text-text-muted uppercase tracking-wide">
          {facility_name}
        </p>
        <p className="text-lg font-extrabold text-primary mt-1">
          PUNCH {punch.visit_type}: {fmtTime(punch.event_time)}
        </p>
        {punch.duplicate && (
          <p className="text-[11px] font-semibold text-text-muted mt-1">
            (already punched a moment ago)
          </p>
        )}
        {isIn && punch.visit_number ? (
          <p className="text-[11px] font-semibold text-text-muted mt-1">
            Visit #{punch.visit_number} at this facility
          </p>
        ) : null}
      </div>
      {staff && (
        <p className="text-text-muted font-semibold text-sm">
          {isIn ? "Have a great day at work!" : "See you tomorrow. Take care!"}
        </p>
      )}
      {patientChoices && (
        <div className="flex flex-col gap-2.5 w-full mt-1">
          <button
            onClick={onPrereg}
            className="bg-primary text-white rounded-xl px-4 py-3.5 text-base font-extrabold hover:opacity-90"
          >
            Pre-register for my visit
          </button>
          <button
            onClick={onVisitor}
            className="bg-background border border-primary/20 text-text-main rounded-xl px-4 py-3.5 text-base font-extrabold hover:border-primary/50"
          >
            I am visiting someone else
          </button>
        </div>
      )}
    </div>
  );
}

const PREREG_FIELDS = [
  ["prefix", "Prefix (Mr./Mrs.)"], ["first_name", "First name"],
  ["middle_name", "Middle name"], ["last_name", "Last name"],
  ["gender", "Gender (M/F/O)"], ["dob", "DOB (YYYY-MM-DD)"],
  ["contact_number", "Phone"], ["whatsapp_number", "WhatsApp"],
  ["email", "Email"], ["national_id_type", "ID type (UID/ABHA/PAN…)"],
  ["national_id", "National ID"], ["address", "Address"],
  ["city", "City"], ["state", "State"],
  ["country", "Country"], ["pin_code", "PIN code"],
];

function PreRegForm({ user, onSubmit, onCancel }) {
  const [form, setForm] = useState(() => {
    const f = { token_type: "General", mrn: user.mrn || "" };
    for (const [k] of PREREG_FIELDS) f[k] = user[k] || "";
    return f;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const genMrn = async () => {
    try {
      const res = await apiNewMrn();
      setForm((f) => ({ ...f, mrn: res.mrn }));
    } catch (err) {
      setError(err.message);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await onSubmit(form);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const field =
    "bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-2 text-sm font-semibold outline-none w-full";

  return (
    <div className="flex flex-col gap-3 max-h-full overflow-y-auto">
      <h2 className="text-xl font-extrabold text-text-main">Pre-registration</h2>
      {error && (
        <div className="text-xs font-semibold text-danger bg-danger/10 rounded-lg px-3 py-2">
          {error}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        {PREREG_FIELDS.map(([key, label]) => (
          <input
            key={key}
            className={field}
            placeholder={label}
            value={form[key]}
            onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          />
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className={field}
          placeholder="MRN"
          value={form.mrn}
          onChange={(e) => setForm({ ...form, mrn: e.target.value })}
        />
        <button
          onClick={genMrn}
          className="shrink-0 bg-background border border-primary/20 text-primary rounded-lg px-3 py-2 text-sm font-bold hover:border-primary/50"
        >
          Generate
        </button>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-xs font-bold text-text-muted">Token type</span>
        {["General", "Cash"].map((t) => (
          <label key={t} className="inline-flex items-center gap-1.5 cursor-pointer">
            <input
              type="radio"
              checked={form.token_type === t}
              onChange={() => setForm({ ...form, token_type: t })}
            />
            <span className="text-sm font-semibold text-text-main">{t}</span>
          </label>
        ))}
      </div>
      <div className="flex gap-2 mt-1">
        <button
          onClick={submit}
          disabled={busy}
          className="flex-1 bg-primary text-white rounded-xl px-4 py-3 text-base font-extrabold hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Submitting…" : "Pre-register"}
        </button>
        <button
          onClick={onCancel}
          className="bg-background border border-primary/20 text-text-muted rounded-xl px-4 py-3 text-base font-bold hover:border-primary/50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function PreregDone({ result }) {
  return (
    <div className="text-center flex flex-col items-center gap-4">
      <div className="w-16 h-16 rounded-full bg-success/10 flex items-center justify-center">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <h2 className="text-2xl font-extrabold text-text-main">Pre-registration saved</h2>
      {result?.token_no ? (
        <div className="bg-primary/5 rounded-2xl px-8 py-5">
          <p className="text-xs font-bold text-text-muted uppercase tracking-wide">Your token</p>
          <p className="text-4xl font-extrabold text-primary mt-1">{result.token_no}</p>
        </div>
      ) : (
        <p className="text-text-muted font-semibold">
          Your details are in — please collect your token at the front desk.
        </p>
      )}
    </div>
  );
}
