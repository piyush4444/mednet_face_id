import { useCallback, useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import CameraGrid from "../components/CameraGrid";
import { toast } from "react-toastify";
import useSocket from "../hooks/useSocket";
import { useAlertStore } from "../store/alertStore";
import { useConnected } from "../store/connectionStore";
import {
  useSearchStore,
  setSearchUser,
  setSearchState,
} from "../store/searchStore";
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Stack,
} from "@mui/material";
import { API_URL as API } from "../config";

// Must be > backend's GLOBAL_TIMEOUT (10s) so the backend EXIT event
// arrives before the frontend garbage-collects the entry.  At 5s this
// caused flicker: patient vanishes → late detection re-adds → EXIT removes.
const STALE_MS = 12_000;

export default function Dashboard() {
  const [cameras, setCameras] = useState([]);
  const [patientsInside, setPatientsInside] = useState({});
  const [activeCameras, setActiveCameras] = useState(new Map()); // camera_id -> last_seen_timestamp
  const [detectedPatients, setDetectedPatients] = useState({}); // camera_id -> { patient_id: { name, timestamp } }
  const [streamVersion, setStreamVersion] = useState({}); // camera_id -> stream version
  const [cameraOffline, setCameraOffline] = useState({}); // camera_id -> boolean
  const pendingCameraUpdates = useRef({}); // Track pending camera switches with debounce
  const { addAlert } = useAlertStore();
  const connected = useConnected();
  const navigate = useNavigate();

  // Search feature lives in the navbar (HeaderSearch) but drives camera
  // highlighting here. Read the shared state.
  const { user: searchUser, state: searchState } = useSearchStore();

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch(`${API}/tracking/cameras`, {
          headers: { "ngrok-skip-browser-warning": "true" },
        });
        if (!res.ok) return;
        const data = await res.json();
        console.log("[DASHBOARD] Cameras fetched:", data.cameras);
        console.log(
          "[DASHBOARD] Camera IDs:",
          data.cameras?.map((c) => c.camera_id),
        );
        setCameras(data.cameras || []);
      } catch (err) {
        console.error("[DASHBOARD] Fetch cameras error:", err);
      }
    };

    fetchData();
    // Camera list is static config — only changes on backend restart or
    // admin action.  30s is more than enough; real-time data flows via WS.
    const interval = setInterval(fetchData, 30_000);
    return () => clearInterval(interval);
  }, []);

  const handleWS = useCallback(
    (event) => {

      // ── Detection matching for search feature ──
      if (event.type === "DETECTION") {
        const faces = event.faces || [];
        const now = Date.now();

        // Track detected patients per camera (for global display)
        setDetectedPatients((prev) => {
          const updated = { ...prev };
          updated[event.camera_id] = {};

          faces.forEach((face) => {
            if (face.user_id) {
              // Backend already resolves identity → patient name (see
              // pipeline_runner._resolve_name). Prefer that; fall back to
              // patientsInside lookup, then to "User {id}" as a last resort.
              const trackedPatient = Object.values(patientsInside).find(
                (p) => String(p.id) === String(face.user_id),
              );
              const name =
                (face.identity &&
                  face.identity !== "Unknown" &&
                  face.identity) ||
                trackedPatient?.name ||
                `User ${face.user_id}`;

              updated[event.camera_id][face.user_id] = {
                name,
                timestamp: now,
                confidence: face.identity_confidence || 0,
                user_type: face.user_type || "PATIENT",
              };
            }

            // Search feature detection
            if (!searchUser) return;
            const faceUserId = String(face.user_id);
            const searchUserId = String(searchUser.id);

            if (faceUserId === searchUserId) {
              setActiveCameras((prev) => {
                const map = new Map(prev);
                map.set(event.camera_id, now);
                return map;
              });
              console.log("[FIND MATCH] Found user on", event.camera_id);

              // Resolve a friendly name + floor from the cached roster so
              // the HeaderSearch popover can show "Lobby Cam · Floor 2"
              // instead of the bare cam_id slug. Falls back to id / "—"
              // when the roster hasn't loaded or the camera was removed.
              const cam = cameras.find(
                (c) => c.camera_id === event.camera_id,
              );

              setSearchUser({
                ...searchUser,
                status: "IN",
                lastSeenCamera: event.camera_id,
                lastSeenCameraName: cam?.name || event.camera_id,
                lastSeenFloor: cam?.floor || searchUser.lastSeenFloor || null,
                lastSeenAt: new Date().toISOString(),
              });

              if (searchState !== "paused") {
                setSearchState("found");
              }
            }
          });

          return updated;
        });

        // Alert for unknown faces
        faces.forEach((f) => {
          if (f.identity === "Unknown" && f.identity_confidence > 0.6) {
            addAlert({
              type: "error",
              message: "Unknown person detected",
            });
            toast.error("Unknown detected");
          }
        });
        return;
      }

      if (event.type === "ENTRY") {
        addAlert({
          type: "success",
          message: `${event.name} entered (${event.floor})`,
        });
        toast.success(`${event.name} entered`);
      }

      if (event.type === "ENTRY" || event.type === "UPDATE") {
        const patientId = event.patient_id;

        // Clear any existing pending update for this patient
        if (pendingCameraUpdates.current[patientId]) {
          clearTimeout(pendingCameraUpdates.current[patientId]);
        }

        // Only update if it's a new patient (ENTRY) or same camera
        setPatientsInside((prev) => {
          const existing = prev[patientId];
          const currentCamera = existing?.camera_id;
          const newCamera = event.camera_id;

          // If same camera or no existing entry, update immediately
          if (!existing || currentCamera === newCamera) {
            return {
              ...prev,
              [patientId]: {
                id: patientId,
                name: event.name,
                camera_id: newCamera,
                floor: event.floor,
                last_seen: Date.now(),
              },
            };
          }

          // Different camera: wait 400ms for stability before switching
          pendingCameraUpdates.current[patientId] = setTimeout(() => {
            setPatientsInside((prev2) => {
              const existing2 = prev2[patientId];
              // Only switch if still the new camera after waiting
              if (existing2) {
                return {
                  ...prev2,
                  [patientId]: {
                    ...existing2,
                    camera_id: newCamera,
                    last_seen: Date.now(),
                  },
                };
              }
              return prev2;
            });
            delete pendingCameraUpdates.current[patientId];
          }, 400);

          return prev;
        });
        return;
      }

      if (event.type === "EXIT") {
        addAlert({
          type: "warning",
          message: `${event.name} exited`,
        });
        toast.warn(`${event.name} exited`);
        setPatientsInside((prev) => {
          const updated = { ...prev };
          delete updated[event.patient_id];
          return updated;
        });
        return;
      }

      if (event.type === "CAMERA_DISCONNECTED") {
        console.log("[CAM DISCONNECTED]", event.camera_id);
        setCameraOffline((prev) => ({ ...prev, [event.camera_id]: true }));
        return;
      }

      if (event.type === "CAMERA_RECONNECTED") {
        console.log("[CAM RECONNECTED]", event.camera_id, "v" + event.version);
        setCameraOffline((prev) => ({ ...prev, [event.camera_id]: false }));
        setStreamVersion((prev) => ({
          ...prev,
          [event.camera_id]: event.version,
        }));
        return;
      }
    },
    [addAlert, searchState, searchUser],
  );

  useSocket(handleWS);

  useEffect(() => {
    const interval = setInterval(() => {
      setPatientsInside((prev) => {
        const now = Date.now();
        const updated = {};
        let changed = false;
        Object.values(prev).forEach((p) => {
          if (now - p.last_seen < STALE_MS) {
            updated[p.id] = p;
          } else {
            changed = true;
          }
        });
        return changed ? updated : prev;
      });
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  // Decay detected patients: remove entries older than 1.5 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();

      setDetectedPatients((prev) => {
        let changed = false;
        const updated = {};

        for (const camId of Object.keys(prev)) {
          const camPatients = prev[camId];
          const filtered = {};
          for (const userId of Object.keys(camPatients)) {
            if (now - camPatients[userId].timestamp < 1500) {
              filtered[userId] = camPatients[userId];
            } else {
              changed = true;
            }
          }
          if (Object.keys(filtered).length > 0) {
            updated[camId] = filtered;
          } else if (Object.keys(camPatients).length > 0) {
            changed = true;
          }
        }

        // Return the SAME reference when nothing expired — React
        // skips the render entirely, keeping the main thread free
        // for the MJPEG <img> tag to process incoming chunks.
        return changed ? updated : prev;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  // Debug activeCamera state changes
  useEffect(() => {
    if (activeCameras.size > 0) {
      const first = Array.from(activeCameras.keys())[0];
      const el = document.getElementById(`cam-${first}`);
      if (el) {
        console.log("[DASHBOARD] Scrolling to camera:", first);
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }, [activeCameras]);

  // Debounce for clearing cameras (anti-flicker)
  useEffect(() => {
    if (searchState === "lost") {
      const timer = setTimeout(() => {
        console.log("[DASHBOARD] Clearing active cameras after lost delay");
        setActiveCameras(new Map());
        setSearchState("searching");
      }, 1200);

      return () => clearTimeout(timer);
    }
  }, [searchState]);

  // Auto-transition from found to lost if no detections for 3 seconds
  useEffect(() => {
    if (searchState === "found" && searchUser) {
      const timer = setTimeout(() => {
        console.log("[DASHBOARD] No detection received, transitioning to lost");
        setSearchState("lost");
      }, 3000);

      return () => clearTimeout(timer);
    }
  }, [searchState, searchUser, searchUser?.lastSeenAt]);

  // Build global detected patients list
  const detectedPatientsList = [];
  Object.entries(detectedPatients).forEach(([camId, patients]) => {
    Object.entries(patients).forEach(([userId, data]) => {
      const existing = detectedPatientsList.find((p) => p.userId === userId);
      if (existing) {
        existing.cameras.push(camId);
      } else {
        detectedPatientsList.push({
          userId,
          name: data.name,
          cameras: [camId],
          confidence: data.confidence,
          user_type: data.user_type || "PATIENT",
        });
      }
    });
  });

  return (
    <div className="flex flex-col" style={{ minHeight: "calc(100vh - 130px)" }}>
      {connected && cameras.length > 0 ? (
        <div className="flex flex-col xl:flex-row gap-6 xl:gap-8 flex-1 min-h-0 px-4 md:px-8 py-4 md:py-6">
          {/* ── Main: Camera Grid ── */}
          <div className="flex-1 min-w-0 order-2 xl:order-1">
            <CameraGrid
              cameras={cameras}
              selected={null}
              livePatients={Object.values(patientsInside)}
              activeCameras={activeCameras}
              searchUser={searchUser}
              searchState={searchState}
              streamVersion={streamVersion}
              cameraOffline={cameraOffline}
            />
          </div>

          {/* ── Sidebar: Detected Patients panel ── */}
          <div className="w-full xl:w-85 shrink-0 order-1 xl:order-2 mb-2 xl:mb-0">
            <Card
              sx={{
                boxShadow: 1,
                borderRadius: 3,
                minHeight: { xs: 250, xl: 750 },
                maxHeight: { xs: "50vh", xl: "calc(100vh - 100px)" },
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
                border: "1px solid rgba(124, 58, 237, 0.08)",
                position: { xs: "static", xl: "sticky" },
                top: { xl: 114 },
              }}
            >
              <CardContent sx={{ p: 2.5, flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    mb: 1.5,
                  }}
                >
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontWeight: 700,
                      color: "text.primary",
                      fontSize: "0.85rem",
                    }}
                  >
                    Detected ({detectedPatientsList.length})
                  </Typography>
                  {searchUser && (
                    <Chip
                      size="small"
                      label={`Tracking: ${searchUser.name}`}
                      sx={{
                        height: 20,
                        fontSize: "0.65rem",
                        fontWeight: 600,
                        bgcolor: "primary.main",
                        color: "white",
                      }}
                    />
                  )}
                </Box>

                {detectedPatientsList.length > 0 ? (
                  <Stack spacing={1}>
                    {detectedPatientsList.map((patient) => (
                      <Box
                        key={patient.userId}
                        onClick={() => navigate(`/patients/${patient.userId}`)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            navigate(`/patients/${patient.userId}`);
                          }
                        }}
                        sx={{
                          p: 1.5,
                          // CSS vars from index.css so the tile follows the
                          // Tailwind light/dark palette (MUI sx doesn't see
                          // the `.dark` class overrides otherwise).
                          bgcolor: "var(--color-card-hover)",
                          borderRadius: 2,
                          border: "1px solid",
                          borderColor: "divider",
                          cursor: "pointer",
                          transition: "all 0.2s ease",
                          "&:hover": {
                            bgcolor: "rgba(124, 58, 237, 0.08)",
                            borderColor: "primary.main",
                            transform: "translateY(-1px)",
                            boxShadow: 1,
                          },
                          "&:focus-visible": {
                            outline: "2px solid",
                            outlineColor: "primary.main",
                            outlineOffset: 2,
                          },
                        }}
                      >
                        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 600,
                              color: "text.primary",
                              fontSize: "0.85rem",
                            }}
                          >
                            {patient.name || `User ${patient.userId}`}
                          </Typography>
                          {patient.user_type && patient.user_type !== "PATIENT" && (
                            <Chip
                              label={patient.user_type}
                              size="small"
                              sx={{
                                height: 16,
                                fontSize: "0.55rem",
                                fontWeight: 700,
                                letterSpacing: "0.05em",
                                // Alpha backgrounds read correctly on both
                                // light and dark surfaces; text flips with
                                // the MUI palette mode.
                                bgcolor:
                                  patient.user_type === "DOCTOR" ? "rgba(52, 211, 153, 0.18)"
                                  : patient.user_type === "EMPLOYEE" ? "rgba(251, 191, 36, 0.18)"
                                  : patient.user_type === "VISITOR" ? "rgba(139, 92, 246, 0.18)"
                                  : patient.user_type === "RELATIVE" ? "rgba(244, 114, 182, 0.18)"
                                  : "rgba(59, 130, 246, 0.18)",
                                color: (theme) =>
                                  theme.palette.mode === "dark"
                                    ? (patient.user_type === "DOCTOR" ? "#6ee7b7"
                                      : patient.user_type === "EMPLOYEE" ? "#fcd34d"
                                      : patient.user_type === "VISITOR" ? "#c4b5fd"
                                      : patient.user_type === "RELATIVE" ? "#f9a8d4"
                                      : "#93c5fd")
                                    : (patient.user_type === "DOCTOR" ? "#047857"
                                      : patient.user_type === "EMPLOYEE" ? "#b45309"
                                      : patient.user_type === "VISITOR" ? "#6d28d9"
                                      : patient.user_type === "RELATIVE" ? "#be185d"
                                      : "#1d4ed8"),
                              }}
                            />
                          )}
                        </Box>
                        <Box
                          sx={{
                            display: "flex",
                            gap: 0.5,
                            flexWrap: "wrap",
                            mt: 0.75,
                          }}
                        >
                          {patient.cameras.map((cam) => (
                            <Chip
                              key={cam}
                              label={cameras.find((c) => c.camera_id === cam)?.name || cam}
                              size="small"
                              variant="filled"
                              sx={{
                                height: 20,
                                fontSize: "0.65rem",
                                fontWeight: 500,
                                bgcolor: "primary.main",
                                color: "white",
                              }}
                            />
                          ))}
                        </Box>
                      </Box>
                    ))}
                  </Stack>
                ) : (
                  <Box sx={{ textAlign: "center", py: 6 }}>
                    <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center mx-auto mb-2">
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
                          d="M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                        />
                      </svg>
                    </div>
                    <Typography
                      variant="body2"
                      sx={{ color: "text.secondary", fontSize: "0.75rem" }}
                    >
                      No one detected
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ color: "text.disabled", fontSize: "0.65rem" }}
                    >
                      Detections appear here in real-time
                    </Typography>
                  </Box>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      ) : (
        /* ── Disconnected / No cameras state ── */
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="w-16 h-16 rounded-2xl bg-gray-100 dark:bg-gray-500/10 flex items-center justify-center mx-auto mb-4">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-8 w-8 text-gray-300 dark:text-gray-600"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </svg>
            </div>
            <p className="text-sm font-semibold text-text-muted">
              {connected ? "No cameras available" : "Connecting to server…"}
            </p>
            <p className="text-xs text-text-light mt-1">
              {connected
                ? "Configure cameras in the backend"
                : "Please ensure the backend is running"}
            </p>
          </div>
        </div>
      )}

    </div>
  );
}
