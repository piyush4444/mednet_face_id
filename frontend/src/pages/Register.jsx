import { useState, useRef, useEffect } from "react";
import { toast } from "react-toastify";
import { useWebcams } from "../hooks/useWebcams";
import CameraSelect from "../components/CameraSelect";
import CustomSelect from "../components/CustomSelect";
import { API_URL } from "../config";

const MAX_SAMPLES = 10;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024; // mirror backend
const ACCEPTED_MIME = ["image/jpeg", "image/png"];

// User types supported by the backend. Order = order shown in the
// type selector at the top of the form.
const USER_TYPES = [
  { value: "PATIENT", label: "Patient" },
  { value: "DOCTOR", label: "Doctor" },
  { value: "EMPLOYEE", label: "Employee" },
  { value: "VISITOR", label: "Visitor" },
  { value: "RELATIVE", label: "Relative" },
];

const RELATION_TYPES = [
  { value: "PARENT", label: "Parent" },
  { value: "CHILD", label: "Child" },
  { value: "SPOUSE", label: "Spouse" },
  { value: "SIBLING", label: "Sibling" },
  { value: "GUARDIAN", label: "Guardian" },
  { value: "OTHER", label: "Other" },
];

const EMPTY_DETAILS = {
  // Common
  age: "",
  gender: "",
  dob: "",
  contact_number: "",
  address: "",
  // Patient
  department: "",
  doctor: "",
  category: "",
  // Doctor / Employee
  role: "",
  specialty: "",
  staff_department: "",
  opd_department_id: "",
  opd_room_id: "",
  // Visitor / general
  purpose: "",
  note: "",
};

export default function Register() {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [isCameraReady, setIsCameraReady] = useState(false);
  const [isCameraOn, setIsCameraOn] = useState(false);
  const [name, setName] = useState("");
  const [userType, setUserType] = useState("PATIENT");
  const [details, setDetails] = useState(EMPTY_DETAILS);
  const [loading, setLoading] = useState(false);
  const [samples, setSamples] = useState([]);

  // Doctor OPD assignments — populated from the admin endpoints on
  // mount. Empty arrays during the brief load window are fine; the
  // CustomSelect renders a "—" placeholder.
  const [opdDepartments, setOpdDepartments] = useState([]);
  const [opdRooms, setOpdRooms] = useState([]);

  // Relative patient picker — async search against /users?type=PATIENT.
  const [patientQuery, setPatientQuery] = useState("");
  const [patientResults, setPatientResults] = useState([]);
  const [patientSearchLoading, setPatientSearchLoading] = useState(false);
  const [selectedRelationType, setSelectedRelationType] = useState("PARENT");
  // Each entry: { related_user_id, related_user_name, relation_type }
  const [pendingRelations, setPendingRelations] = useState([]);

  const updateDetail = (key, value) =>
    setDetails((prev) => ({ ...prev, [key]: value }));

  // Fetch OPD departments + rooms once on mount. Doctor registrations
  // need these for the assignment dropdowns; other user types ignore
  // them — fetching unconditionally keeps the logic simple and the
  // lists are small.
  useEffect(() => {
    let cancelled = false;
    const fetchOpd = async () => {
      try {
        const [deptRes, roomRes] = await Promise.all([
          fetch(`${API_URL}/frontdesk/admin/departments`, {
            headers: { "ngrok-skip-browser-warning": "true" },
          }),
          fetch(`${API_URL}/frontdesk/admin/rooms`, {
            headers: { "ngrok-skip-browser-warning": "true" },
          }),
        ]);
        if (cancelled) return;
        if (deptRes.ok) {
          const depts = await deptRes.json();
          setOpdDepartments(Array.isArray(depts) ? depts : []);
        }
        if (roomRes.ok) {
          const rooms = await roomRes.json();
          setOpdRooms(Array.isArray(rooms) ? rooms : []);
        }
      } catch (err) {
        console.warn("Could not load OPD departments / rooms:", err);
      }
    };
    fetchOpd();
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced patient search for the RELATIVE flow.
  useEffect(() => {
    if (userType !== "RELATIVE") return;
    const q = patientQuery.trim();
    if (q.length < 2) {
      setPatientResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setPatientSearchLoading(true);
      try {
        const res = await fetch(
          `${API_URL}/users?type=PATIENT&q=${encodeURIComponent(q)}`,
          { headers: { "ngrok-skip-browser-warning": "true" } },
        );
        if (res.ok) {
          const rows = await res.json();
          setPatientResults(Array.isArray(rows) ? rows : []);
        }
      } catch (err) {
        console.warn("Patient search failed:", err);
      } finally {
        setPatientSearchLoading(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [patientQuery, userType]);

  const addPendingRelation = (patient) => {
    if (!patient) return;
    if (
      pendingRelations.some((r) => r.related_user_id === patient.id)
    ) {
      toast.info(`${patient.name} is already linked.`);
      return;
    }
    setPendingRelations((prev) => [
      ...prev,
      {
        related_user_id: patient.id,
        related_user_name: patient.name,
        relation_type: selectedRelationType,
      },
    ]);
    setPatientQuery("");
    setPatientResults([]);
  };

  const removePendingRelation = (relatedUserId) => {
    setPendingRelations((prev) =>
      prev.filter((r) => r.related_user_id !== relatedUserId),
    );
  };

  const { devices, selectedDeviceId, setSelectedDeviceId, refreshDevices } =
    useWebcams();

  const stopCamera = () => {
    const video = videoRef.current;
    const stream = video?.srcObject;

    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }

    if (video) {
      video.pause();
      video.srcObject = null;
    }

    setIsCameraOn(false);
    setIsCameraReady(false);
  };

  const startCamera = async () => {
    const stream = videoRef.current?.srcObject;

    if (stream && stream.getTracks().some((t) => t.readyState === "live")) {
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: selectedDeviceId
          ? { deviceId: { exact: selectedDeviceId } }
          : true,
      });

      // ✅ WAIT for React to ensure videoRef exists
      await new Promise((r) => setTimeout(r, 50));

      const video = videoRef.current;

      if (!video) {
        console.error("Video ref not ready");
        return;
      }

      video.srcObject = stream;

      await new Promise((resolve) => {
        video.onloadedmetadata = () => resolve();
      });

      await video.play();

      setIsCameraOn(true);
      setIsCameraReady(true);


      refreshDevices();
    } catch (err) {
      console.error(err);
      toast.error("Unable to access camera.");
    }
  };

  useEffect(() => {
    if (isCameraOn && selectedDeviceId) {
      startCamera();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDeviceId]);

  useEffect(() => {
    startCamera();
    return () => {
      const stream = videoRef.current?.srcObject;
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      samples.forEach((s) => URL.revokeObjectURL(s.url));
    };
  }, []);

  const captureSample = () => {
    if (samples.length >= MAX_SAMPLES) return;

    const video = videoRef.current;
    const canvas = canvasRef.current;

    if (!isCameraOn || !video || !video.srcObject || video.readyState < 1) {
      toast.warn("Camera feed is not active.");
      return;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          toast.error("Failed to capture frame.");
          return;
        }

        const url = URL.createObjectURL(blob);

        setSamples((prev) => [...prev, { blob, url, source: "camera" }]);
        toast.success("Captured ✔");
      },
      "image/jpeg",
      0.9,
    );
  };

  // ── File upload path ──────────────────────────────────────────────────
  const addUploadedFiles = (fileList) => {
    const incoming = Array.from(fileList || []);
    if (incoming.length === 0) return;

    const currentCount = samples.length;
    const slotsLeft = MAX_SAMPLES - currentCount;

    if (slotsLeft <= 0) {
      toast.warn(`Max ${MAX_SAMPLES} samples reached — remove one first.`);
      return;
    }

    const accepted = [];
    const rejected = [];

    for (const file of incoming) {
      if (accepted.length >= slotsLeft) {
        rejected.push(`${file.name}: slot limit reached`);
        continue;
      }
      if (!ACCEPTED_MIME.includes(file.type)) {
        rejected.push(`${file.name}: must be JPEG or PNG`);
        continue;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        rejected.push(`${file.name}: larger than 5MB`);
        continue;
      }
      accepted.push({
        blob: file,
        url: URL.createObjectURL(file),
        source: "upload",
      });
    }

    if (accepted.length > 0) {
      setSamples((prev) => [...prev, ...accepted]);
    }
    if (rejected.length > 0) {
      toast.error(`Skipped ${rejected.length}: ${rejected[0]}`);
    } else if (accepted.length > 0) {
      toast.success(
        `Added ${accepted.length} image${accepted.length === 1 ? "" : "s"} ✔`,
      );
    }
  };

  const removeSample = (idx) => {
    setSamples((prev) => {
      const target = prev[idx];
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((_, i) => i !== idx);
    });
  };

  // Drag-and-drop state + handlers
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);

  const onDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (loading) return;
    setIsDragging(true);
  };
  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget.contains(e.relatedTarget)) return;
    setIsDragging(false);
  };
  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  };
  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (loading) return;
    addUploadedFiles(e.dataTransfer.files);
  };

  const resetSamples = () => {
    samples.forEach((s) => URL.revokeObjectURL(s.url));
    setSamples([]);
  };

  const handleRegister = async () => {
    if (!name.trim()) {
      toast.warn("Please enter a name first.");
      return;
    }
    if (samples.length === 0) {
      toast.warn("Capture at least one sample.");
      return;
    }

    // Per-type required-field checks (mirrors backend validation,
    // but a client-side block keeps the round-trip fast).
    if (userType === "DOCTOR" && !details.opd_department_id) {
      toast.warn("Doctors must be assigned to an OPD department.");
      return;
    }
    if (userType === "EMPLOYEE" && !details.role.trim()) {
      toast.warn("Employees must have a role.");
      return;
    }

    // Validate age if provided
    if (
      details.age !== "" &&
      (isNaN(Number(details.age)) || Number(details.age) < 0)
    ) {
      toast.error("Age must be a non-negative number.");
      return;
    }

    setLoading(true);
    const formData = new FormData();
    formData.append("name", name.trim());
    formData.append("user_type", userType);
    samples.forEach((s, i) => {
      // Preserve original filename for uploads (helps backend error messages);
      // synthesize one for camera captures.
      const ext = s.blob?.type === "image/png" ? "png" : "jpg";
      const fallback = `${s.source || "sample"}_${i}.${ext}`;
      const filename = s.blob?.name || fallback;
      formData.append("images", s.blob, filename);
    });

    // Whitelist which detail keys the backend should see for this
    // type. Sending an irrelevant key is harmless (the backend
    // ignores it), but keeping the form-data tight makes the network
    // tab easier to read during debugging.
    const TYPE_FIELDS = {
      PATIENT: [
        "age", "gender", "dob", "contact_number", "address",
        "department", "doctor", "category", "note",
      ],
      DOCTOR: [
        "contact_number", "specialty", "staff_department",
        "opd_department_id", "opd_room_id", "note",
      ],
      EMPLOYEE: [
        "contact_number", "role", "staff_department", "note",
      ],
      VISITOR: ["contact_number", "purpose", "note"],
      RELATIVE: ["contact_number", "note"],
    };
    const allowedKeys = TYPE_FIELDS[userType] || [];
    allowedKeys.forEach((key) => {
      const value = details[key];
      if (value !== "" && value !== null && value !== undefined) {
        formData.append(key, String(value).trim());
      }
    });

    try {
      const response = await fetch(`${API_URL}/register/multi`, {
        method: "POST",
        body: formData,
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || `Server error: ${response.status}`);
      }

      const newUserId = data.user_id;
      const stored = data.embeddings_stored ?? samples.length;
      const skipped = Array.isArray(data.skipped) ? data.skipped : [];
      const skippedSuffix = skipped.length
        ? ` — skipped ${skipped.length} (${skipped[0].reason})`
        : "";

      // For RELATIVE registrations, link the chosen patient(s) now
      // that we have the new user_id. Failures are surfaced but do
      // not undo the registration itself.
      let relationsLinked = 0;
      let relationsFailed = 0;
      if (
        userType === "RELATIVE" &&
        newUserId &&
        pendingRelations.length > 0
      ) {
        for (const rel of pendingRelations) {
          try {
            const relRes = await fetch(
              `${API_URL}/users/${newUserId}/relations`,
              {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "ngrok-skip-browser-warning": "true",
                },
                body: JSON.stringify({
                  related_user_id: rel.related_user_id,
                  relation_type: rel.relation_type,
                }),
              },
            );
            if (relRes.ok) relationsLinked += 1;
            else relationsFailed += 1;
          } catch {
            relationsFailed += 1;
          }
        }
      }

      const relationsSuffix =
        userType === "RELATIVE"
          ? ` · ${relationsLinked} relation${relationsLinked === 1 ? "" : "s"} linked` +
            (relationsFailed > 0 ? ` (${relationsFailed} failed)` : "")
          : "";

      const typeLabel =
        USER_TYPES.find((t) => t.value === userType)?.label || userType;
      toast.success(
        `${typeLabel} registered — ${stored} embedding${stored === 1 ? "" : "s"} stored${skippedSuffix}${relationsSuffix}`,
      );
      setName("");
      setDetails(EMPTY_DETAILS);
      setPendingRelations([]);
      setPatientQuery("");
      setPatientResults([]);
      resetSamples();
    } catch (err) {
      console.error("Registration failed:", err);
      toast.error(`Registration failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const captureDisabled =
    !isCameraOn || !isCameraReady || samples.length >= MAX_SAMPLES || loading;
  const registerDisabled = !name.trim() || samples.length === 0 || loading;

  // First 5: close-up at the front desk. Last 5: ask patient to step
  // back ~2.5-3 m for varied-distance embeddings (improves recognition
  // at corridor cameras). Operator can stop at any count ≥1.
  const angleLabels = [
    "Front", "Left", "Right", "Up", "Down",
    "Far Front", "Far Left", "Far Right", "Far Up", "Far Down",
  ];

  return (
    <div className="h-full flex flex-col items-center justify-center max-w-7xl mx-auto w-full relative pt-4">
      <div className="bg-card rounded-2xl shadow-md hover:shadow-lg transition-all duration-300 border border-primary/8 flex flex-col w-full overflow-hidden animate-fade-in">
        {/* ── Header ── */}
        <div className="px-6 py-4 border-b border-primary/8 flex justify-between items-center bg-card">
          <h2 className="text-lg font-bold text-text-main flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-4.5 w-4.5 text-primary"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"
                />
              </svg>
            </div>
            Register User
          </h2>
          <div className="flex items-center gap-2.5">
            {devices.length > 0 && (
              <CameraSelect
                devices={devices}
                selectedDeviceId={selectedDeviceId}
                onSelect={setSelectedDeviceId}
                compact
              />
            )}

            {isCameraOn ? (
              <button
                onClick={stopCamera}
                className="text-xs px-3.5 py-1.5 bg-danger/10 text-danger hover:bg-danger/20 rounded-lg transition-all duration-200 font-semibold border border-danger/20 cursor-pointer"
              >
                Stop Camera
              </button>
            ) : (
              <button
                onClick={startCamera}
                className="text-xs px-3.5 py-1.5 bg-success/10 text-green-700 dark:text-green-300 hover:bg-success/20 rounded-lg transition-all duration-200 font-semibold border border-success/20 cursor-pointer"
              >
                Start Camera
              </button>
            )}
          </div>
        </div>

        <div className="p-6 flex flex-col md:flex-row gap-6 items-stretch">
          {/* ── Left: Camera & Capture ── */}
          <div className="w-full md:w-2/3 flex flex-col gap-5">
            <div className="w-full relative h-85 md:h-auto md:max-h-150 bg-gray-900 rounded-xl overflow-hidden flex items-center justify-center">
              {/* ✅ ALWAYS mounted video */}
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={`w-full h-full object-cover ${!isCameraOn ? "hidden" : ""}`}
              />

              <canvas ref={canvasRef} className="hidden" />
              {/* UI overlay */}
              {!isCameraOn && (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-white/50">
                  <p className="text-sm font-medium">Camera inactive</p>
                </div>
              )}
            </div>

            {/* ── Progress Dots ── */}
            <div className="flex items-center justify-center gap-2">
              {Array.from({ length: MAX_SAMPLES }).map((_, i) => (
                <div key={i} className="flex flex-col items-center gap-1">
                  <div
                    className={`w-3 h-3 rounded-full transition-all duration-300 ${
                      i < samples.length
                        ? "bg-primary shadow-sm shadow-primary/40 scale-110"
                        : "bg-text-light/20"
                    }`}
                  />
                  <span
                    className={`text-[9px] font-semibold ${i < samples.length ? "text-primary" : "text-text-light"}`}
                  >
                    {angleLabels[i]}
                  </span>
                </div>
              ))}
            </div>

            {/* Capture / Reset */}
            <div className="flex gap-3">
              <button
                onClick={captureSample}
                disabled={captureDisabled}
                className={`flex-1 px-4 py-3 rounded-xl font-bold transition-all duration-200 flex items-center justify-center gap-2 cursor-pointer shadow-sm
                  ${
                    captureDisabled
                      ? "bg-primary/8 text-text-light cursor-not-allowed"
                      : "gradient-primary text-white hover:opacity-90 shadow-lg shadow-primary/20 hover:-translate-y-0.5 active:translate-y-0"
                  }`}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-5 w-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 01-2 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
                  />
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
                {samples.length >= MAX_SAMPLES
                  ? "Max samples reached"
                  : `Capture (${samples.length}/${MAX_SAMPLES})`}
              </button>
              <button
                onClick={resetSamples}
                disabled={samples.length === 0 || loading}
                className={`px-5 py-3 rounded-xl font-semibold transition-all duration-200 cursor-pointer
                  ${
                    samples.length === 0 || loading
                      ? "bg-primary/5 text-text-light cursor-not-allowed"
                      : "bg-card text-text-main border border-primary/15 hover:bg-danger/5 hover:text-danger hover:border-danger/20"
                  }`}
              >
                Reset
              </button>
            </div>

            {/* ── OR · Upload from file ── */}
            <div className="flex items-center gap-3 pt-1">
              <div className="flex-1 h-px bg-primary/10" />
              <span className="text-[10px] font-bold uppercase tracking-widest text-text-light">
                or upload
              </span>
              <div className="flex-1 h-px bg-primary/10" />
            </div>

            <div
              onDragEnter={onDragEnter}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => !loading && fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && !loading) {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              className={`rounded-xl border-2 border-dashed px-4 py-5 text-center transition-all duration-200 cursor-pointer
                ${
                  loading
                    ? "border-primary/10 bg-primary/5 cursor-not-allowed"
                    : isDragging
                      ? "border-primary bg-primary/10 scale-[1.01]"
                      : "border-primary/20 hover:border-primary/40 hover:bg-primary/5"
                }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png"
                multiple
                className="hidden"
                onChange={(e) => {
                  addUploadedFiles(e.target.files);
                  e.target.value = ""; // allow re-selecting the same file
                }}
                disabled={loading}
              />
              <div className="flex flex-col items-center gap-1.5">
                <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-6 w-6 text-primary"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5-5m0 0l5 5m-5-5v12"
                    />
                  </svg>
                </div>
                <p className="text-sm font-semibold text-text-main">
                  {isDragging
                    ? "Drop to add"
                    : "Drag & drop images or click to browse"}
                </p>
                <p className="text-[11px] text-text-light">
                  JPEG/PNG · max 5MB each · up to {MAX_SAMPLES} total
                </p>
              </div>
            </div>
          </div>

          {/* ── Right: Gallery & Form ── */}
          <div className="w-full md:w-1/3 flex flex-col gap-5 border-t md:border-t-0 md:border-l border-primary/8 pt-5 md:pt-0 md:pl-6">
            <h3 className="text-xs font-bold text-text-muted uppercase tracking-wider">
              Sample Gallery
            </h3>

            {/* Thumbnails */}
            <div className="grow flex flex-row md:flex-col gap-2 flex-wrap min-h-18 md:min-h-0 bg-background/60 rounded-xl border border-primary/8 overflow-y-auto max-h-30 md:max-h-full p-2.5 content-start">
              {samples.length === 0 ? (
                <div className="flex flex-col items-center justify-center w-full h-full text-center p-4">
                  <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center mb-2.5">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-5 w-5 text-primary/40"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
                      />
                    </svg>
                  </div>
                  <p className="text-[11px] text-text-light font-medium leading-snug">
                    Capture {MAX_SAMPLES} samples from different angles
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-2 lg:grid-cols-2 gap-2 w-full">
                  {samples.map((s, i) => (
                    <div
                      key={i}
                      className="relative w-full aspect-video md:aspect-auto md:h-20 lg:h-24 rounded-lg overflow-hidden border-2 border-primary/20 shadow-sm group hover:border-primary/40 transition-all duration-200"
                    >
                      <img
                        src={s.url}
                        alt={`sample-${i}`}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      />
                      {/* Source badge (top-left) */}
                      <span
                        className={`absolute top-1 left-1 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-md backdrop-blur-sm pointer-events-none ${
                          s.source === "upload"
                            ? "bg-blue-500/85 text-white"
                            : "bg-primary/85 text-white"
                        }`}
                      >
                        {s.source === "upload" ? "File" : "Cam"}
                      </span>
                      {/* Remove button (top-right) */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeSample(i);
                        }}
                        disabled={loading}
                        aria-label={`Remove sample ${i + 1}`}
                        className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/60 hover:bg-danger text-white flex items-center justify-center opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all duration-200 cursor-pointer disabled:cursor-not-allowed"
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          className="h-3 w-3"
                          viewBox="0 0 20 20"
                          fill="currentColor"
                        >
                          <path
                            fillRule="evenodd"
                            d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                            clipRule="evenodd"
                          />
                        </svg>
                      </button>
                      {/* Angle label on hover (camera only) */}
                      {s.source === "camera" && (
                        <div className="absolute inset-x-0 bottom-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex items-center justify-center py-1">
                          <span className="text-white text-[10px] font-bold">
                            {angleLabels[i] || `#${i + 1}`}
                          </span>
                        </div>
                      )}
                      <span className="absolute bottom-0 right-0 bg-primary text-white text-[10px] px-1.5 py-0.5 rounded-tl-lg font-bold pointer-events-none">
                        {i + 1}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Form */}
            <div className="flex flex-col gap-3 mt-auto pt-4 border-t border-primary/8">
              {/* Name (universal) */}
              <div className="flex flex-col gap-1.5">
                <label
                  htmlFor="user-name"
                  className="text-xs font-bold text-text-muted uppercase tracking-wider"
                >
                  Name <span className="text-danger">*</span>
                </label>
                <input
                  id="user-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Piyush"
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-4 py-3 text-text-main font-semibold placeholder-text-light/50 transition-all duration-200 outline-none"
                  disabled={loading}
                />
              </div>

              {/* User type selector (universal) */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                  User Type <span className="text-danger">*</span>
                </label>
                <CustomSelect
                  value={userType}
                  onChange={(e) => setUserType(e.target.value)}
                  disabled={loading}
                  options={USER_TYPES}
                />
              </div>

              {/* Contact (universal — every type benefits from it) */}
              <div className="flex flex-col gap-1.5">
                <label
                  htmlFor="user-contact"
                  className="text-[10px] font-bold text-text-muted uppercase tracking-wider"
                >
                  Contact Number
                </label>
                <input
                  id="user-contact"
                  type="tel"
                  value={details.contact_number}
                  onChange={(e) =>
                    updateDetail("contact_number", e.target.value)
                  }
                  placeholder="e.g. 9876543210"
                  maxLength={20}
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                  disabled={loading}
                />
              </div>

              {/* ── PATIENT-specific fields ──────────────────────── */}
              {userType === "PATIENT" && (
                <>
                  <div className="grid grid-cols-2 gap-2.5">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                        Age
                      </label>
                      <input
                        type="number"
                        min="0"
                        max="150"
                        value={details.age}
                        onChange={(e) => updateDetail("age", e.target.value)}
                        placeholder="—"
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                        disabled={loading}
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                        Gender
                      </label>
                      <CustomSelect
                        value={details.gender}
                        onChange={(e) => updateDetail("gender", e.target.value)}
                        disabled={loading}
                        placeholder="—"
                        options={[
                          { value: "", label: "—" },
                          { value: "Male", label: "Male" },
                          { value: "Female", label: "Female" },
                          { value: "Other", label: "Other" },
                        ]}
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Date of Birth
                    </label>
                    <input
                      type="date"
                      value={details.dob}
                      onChange={(e) => updateDetail("dob", e.target.value)}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold outline-none transition-all duration-200"
                      disabled={loading}
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Address
                    </label>
                    <textarea
                      value={details.address}
                      onChange={(e) => updateDetail("address", e.target.value)}
                      placeholder="Street, city, …"
                      rows={2}
                      maxLength={500}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200 resize-none"
                      disabled={loading}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                        Department
                      </label>
                      <input
                        type="text"
                        value={details.department}
                        onChange={(e) =>
                          updateDetail("department", e.target.value)
                        }
                        placeholder="e.g. Cardiology"
                        maxLength={100}
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                        disabled={loading}
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                        Doctor
                      </label>
                      <input
                        type="text"
                        value={details.doctor}
                        onChange={(e) => updateDetail("doctor", e.target.value)}
                        placeholder="e.g. Dr. Sharma"
                        maxLength={100}
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                        disabled={loading}
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Category
                    </label>
                    <input
                      type="text"
                      value={details.category}
                      onChange={(e) =>
                        updateDetail("category", e.target.value)
                      }
                      placeholder="e.g. IPD / OPD / VIP"
                      maxLength={100}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      disabled={loading}
                    />
                  </div>
                </>
              )}

              {/* ── DOCTOR-specific fields ──────────────────────── */}
              {userType === "DOCTOR" && (
                <>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      OPD Department <span className="text-danger">*</span>
                    </label>
                    <CustomSelect
                      value={String(details.opd_department_id || "")}
                      onChange={(e) =>
                        updateDetail(
                          "opd_department_id",
                          e.target.value ? Number(e.target.value) : "",
                        )
                      }
                      disabled={loading}
                      placeholder="Select a department"
                      options={[
                        { value: "", label: "—" },
                        ...opdDepartments.map((d) => ({
                          value: String(d.id),
                          label: d.name,
                        })),
                      ]}
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      OPD Room
                    </label>
                    <CustomSelect
                      value={String(details.opd_room_id || "")}
                      onChange={(e) =>
                        updateDetail(
                          "opd_room_id",
                          e.target.value ? Number(e.target.value) : "",
                        )
                      }
                      disabled={loading}
                      placeholder="—"
                      options={[
                        { value: "", label: "—" },
                        ...opdRooms.map((r) => ({
                          value: String(r.id),
                          label: r.name,
                        })),
                      ]}
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Specialty
                    </label>
                    <input
                      type="text"
                      value={details.specialty}
                      onChange={(e) =>
                        updateDetail("specialty", e.target.value)
                      }
                      placeholder="e.g. Cardiology"
                      maxLength={128}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      disabled={loading}
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Department (free-text)
                    </label>
                    <input
                      type="text"
                      value={details.staff_department}
                      onChange={(e) =>
                        updateDetail("staff_department", e.target.value)
                      }
                      placeholder="e.g. Cardiac Surgery"
                      maxLength={128}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      disabled={loading}
                    />
                  </div>
                </>
              )}

              {/* ── EMPLOYEE-specific fields ────────────────────── */}
              {userType === "EMPLOYEE" && (
                <>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Role <span className="text-danger">*</span>
                    </label>
                    <CustomSelect
                      value={details.role}
                      onChange={(e) => updateDetail("role", e.target.value)}
                      disabled={loading}
                      placeholder="Select a role"
                      options={[
                        { value: "", label: "—" },
                        { value: "Nurse", label: "Nurse" },
                        { value: "Admin", label: "Admin" },
                        { value: "Security", label: "Security" },
                        { value: "Lab", label: "Lab Technician" },
                        { value: "Housekeeping", label: "Housekeeping" },
                        { value: "Other", label: "Other" },
                      ]}
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      Department
                    </label>
                    <input
                      type="text"
                      value={details.staff_department}
                      onChange={(e) =>
                        updateDetail("staff_department", e.target.value)
                      }
                      placeholder="e.g. ICU / Reception"
                      maxLength={128}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      disabled={loading}
                    />
                  </div>
                </>
              )}

              {/* ── VISITOR-specific fields ─────────────────────── */}
              {userType === "VISITOR" && (
                <div className="flex flex-col gap-1.5">
                  <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                    Purpose of Visit
                  </label>
                  <input
                    type="text"
                    value={details.purpose}
                    onChange={(e) => updateDetail("purpose", e.target.value)}
                    placeholder="e.g. Visiting room 304"
                    maxLength={256}
                    className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                    disabled={loading}
                  />
                </div>
              )}

              {/* ── RELATIVE-specific: patient picker + relations ── */}
              {userType === "RELATIVE" && (
                <div className="flex flex-col gap-2">
                  <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                    Linked Patient(s)
                  </label>

                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <input
                        type="text"
                        value={patientQuery}
                        onChange={(e) => setPatientQuery(e.target.value)}
                        placeholder="Search patient by name or MRN…"
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                        disabled={loading}
                      />
                      {patientQuery.trim().length >= 2 &&
                        (patientResults.length > 0 || patientSearchLoading) && (
                          <div className="absolute z-20 mt-1 w-full bg-card border border-primary/15 rounded-xl shadow-lg max-h-48 overflow-y-auto">
                            {patientSearchLoading && (
                              <div className="px-3 py-2 text-xs text-text-light">
                                Searching…
                              </div>
                            )}
                            {!patientSearchLoading &&
                              patientResults.length === 0 && (
                                <div className="px-3 py-2 text-xs text-text-light">
                                  No patients matched.
                                </div>
                              )}
                            {!patientSearchLoading &&
                              patientResults.map((p) => (
                                <button
                                  key={p.id}
                                  type="button"
                                  onClick={() => addPendingRelation(p)}
                                  className="w-full text-left px-3 py-2 text-sm hover:bg-primary/5 border-b border-primary/5 last:border-0 cursor-pointer"
                                >
                                  <div className="font-semibold text-text-main">
                                    {p.name}
                                  </div>
                                  {p.mrn && (
                                    <div className="text-[10px] font-mono text-text-muted">
                                      MRN: {p.mrn}
                                    </div>
                                  )}
                                </button>
                              ))}
                          </div>
                        )}
                    </div>
                    <div className="w-32 shrink-0">
                      <CustomSelect
                        value={selectedRelationType}
                        onChange={(e) =>
                          setSelectedRelationType(e.target.value)
                        }
                        disabled={loading}
                        options={RELATION_TYPES}
                      />
                    </div>
                  </div>

                  {pendingRelations.length > 0 && (
                    <ul className="flex flex-col gap-1 mt-1">
                      {pendingRelations.map((rel) => (
                        <li
                          key={rel.related_user_id}
                          className="flex items-center justify-between gap-2 bg-primary/5 border border-primary/15 rounded-lg px-2.5 py-1.5"
                        >
                          <span className="text-xs font-semibold text-text-main truncate">
                            {rel.related_user_name}
                            <span className="ml-2 text-[10px] font-bold uppercase tracking-wide text-primary">
                              {rel.relation_type}
                            </span>
                          </span>
                          <button
                            type="button"
                            onClick={() =>
                              removePendingRelation(rel.related_user_id)
                            }
                            disabled={loading}
                            className="text-danger text-xs font-bold hover:underline disabled:opacity-50 cursor-pointer"
                          >
                            Remove
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}

                  <p className="text-[10px] text-text-light">
                    At least one linked patient is recommended. More can be
                    added later from Manage Users.
                  </p>
                </div>
              )}

              {/* Note (universal — useful for every type) */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                  Note
                </label>
                <textarea
                  value={details.note}
                  onChange={(e) => updateDetail("note", e.target.value)}
                  placeholder="Any extra information…"
                  rows={2}
                  maxLength={500}
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200 resize-none"
                  disabled={loading}
                />
              </div>

              <button
                onClick={handleRegister}
                disabled={registerDisabled}
                className={`py-3.5 rounded-xl font-bold w-full shadow-md transition-all duration-200 flex items-center justify-center gap-2 cursor-pointer
                  ${
                    registerDisabled
                      ? "bg-primary/8 text-text-light cursor-not-allowed shadow-none"
                      : "gradient-primary text-white hover:opacity-90 shadow-lg shadow-primary/25 hover:-translate-y-px active:translate-y-0"
                  }`}
              >
                {loading ? (
                  <>
                    <svg
                      className="animate-spin h-5 w-5 text-current opacity-70"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      ></circle>
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      ></path>
                    </svg>
                    Processing…
                  </>
                ) : (
                  <>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-5 w-5"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2.5}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    Finalize Registration
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
