import { useState, useEffect, useRef, memo } from "react";
import { API_URL as API } from "../config";

/**
 * Convert underscore IDs to human-readable labels.
 *   cam_1 → Camera 1
 */
function formatLabel(raw) {
  if (!raw) return "";
  return raw
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Convert floor IDs to ordinal labels.
 *   floor_1 → 1st Floor,  floor_2 → 2nd Floor,  floor_3 → 3rd Floor, etc.
 */
function formatFloor(raw) {
  if (!raw) return "";
  const match = raw.match(/(\d+)/);
  if (!match) return formatLabel(raw);
  const n = parseInt(match[1], 10);
  const suffix =
    n % 100 >= 11 && n % 100 <= 13
      ? "th"
      : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th";
  return `${n}${suffix} Floor`;
}

const ROLE_STYLES = {
  entry:  { bg: "#dcfce7", text: "#166534", label: "Entry" },
  exit:   { bg: "#fee2e2", text: "#991b1b", label: "Exit" },
  inside: { bg: "#dbeafe", text: "#1e40af", label: "Inside" },
};

/** Seconds to wait for the first MJPEG frame before showing offline */
const LOAD_TIMEOUT = 6;

function CameraCard({
  cam,
  selected,
  patients: livePatients,
  isActive,
  searchUser,
  searchState,
  streamVersion = 0,
  offline = false,
  isSpotlight = false,
  onSpotlight,
}) {
  const [imgError, setImgError] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [streamEnabled, setStreamEnabled] = useState(true);
  const loadTimerRef = useRef(null);

  // When streamVersion changes (camera reconnected) or offline clears → reset and remount
  useEffect(() => {
    if (!offline) {
      setImgError(false);
      setImgLoaded(false);
      setRetryCount((c) => c + 1);
    }
  }, [streamVersion, offline]);

  // Start a load timeout each time the img remounts — if no frame arrives
  // within LOAD_TIMEOUT seconds, treat the stream as offline.
  useEffect(() => {
    if (offline || imgError || imgLoaded || !streamEnabled) {
      clearTimeout(loadTimerRef.current);
      return;
    }

    loadTimerRef.current = setTimeout(() => {
      if (!imgLoaded) {
        setImgError(true);
      }
    }, LOAD_TIMEOUT * 1000);

    return () => clearTimeout(loadTimerRef.current);
  }, [retryCount, offline, imgError, imgLoaded, streamEnabled]);

  const showOffline = offline || imgError;
  const showPaused = !streamEnabled && !showOffline;

  const handleToggleStream = (e) => {
    e.stopPropagation();
    setStreamEnabled((v) => {
      const next = !v;
      if (next) {
        setImgError(false);
        setImgLoaded(false);
        setRetryCount((c) => c + 1);
      }
      return next;
    });
  };

  const patients =
    livePatients !== undefined ? livePatients : cam?.patients || [];
  const isSelected =
    selected &&
    selected.location &&
    selected.location.camera_id === cam.camera_id;
  const isUserInCamera = isActive && searchUser && searchState === "found";

  const role = cam?.role;
  const roleStyle = ROLE_STYLES[role] || null;

  const handleRetry = (e) => {
    e.stopPropagation();
    setImgError(false);
    setImgLoaded(false);
    setRetryCount((c) => c + 1);
  };

  return (
    <div
      id={`cam-${cam.camera_id}`}
      className={`
        group relative bg-gray-900 rounded-2xl overflow-hidden flex flex-col m-1
        transition-all duration-300 md:cursor-pointer select-none
        ${isSpotlight ? "ring-2 ring-primary shadow-xl shadow-primary/10" : "hover:ring-1 hover:ring-primary/40"}
        ${isUserInCamera ? "ring-2 ring-emerald-400 shadow-lg shadow-emerald-400/20" : ""}
        ${isSelected ? "ring-2 ring-blue-500 shadow-lg" : ""}
      `}
      style={{ aspectRatio: isSpotlight ? "16/9" : "16/10" }}
      onClick={() => onSpotlight?.(cam.camera_id)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* ── Video / Offline Area ── */}
      <div className="relative w-full h-full">
        {showPaused ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-white/60 bg-gray-900">
            <div className="w-14 h-14 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-7 w-7 text-white/30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-xs font-medium text-white/40">Stream Paused</p>
            <p className="text-[10px] text-white/25 -mt-1">Detection still running</p>
          </div>
        ) : showOffline ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-white/60 bg-gray-900">
            <div className="w-14 h-14 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-7 w-7 text-white/30"
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
                <line
                  x1="3"
                  y1="3"
                  x2="21"
                  y2="21"
                  stroke="currentColor"
                  strokeWidth={1.5}
                  strokeLinecap="round"
                />
              </svg>
            </div>
            <p className="text-xs font-medium text-white/40">
              Camera Offline
            </p>
            {offline ? (
              <p className="text-[10px] text-white/25 mt-1">
                Waiting for reconnection…
              </p>
            ) : (
              <button
                onClick={handleRetry}
                className="mt-1 px-4 py-1.5 text-xs font-semibold rounded-lg bg-white/10 hover:bg-white/20 text-white/70 hover:text-white transition-all duration-200 cursor-pointer border border-white/10 hover:border-white/20"
              >
                Retry
              </button>
            )}
          </div>
        ) : (
          <img
            key={`${cam.camera_id}-v${streamVersion}-r${retryCount}`}
            src={`${API}/stream/${cam.camera_id}?v=${streamVersion}&r=${retryCount}`}
            alt={`Live stream from ${cam.name || formatLabel(cam.camera_id)}`}
            className="w-full h-full object-cover"
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
          />
        )}

        {/* ── Top-left: Camera Name Overlay ── */}
        <div className={`absolute top-0 left-0 right-0 p-3 flex justify-between items-start transition-opacity duration-200 ${hovered || isSpotlight ? "opacity-100" : "opacity-80"}`}>
          <div className="flex items-center gap-2">
            <div className="bg-black/60 backdrop-blur-sm rounded-lg px-2.5 py-1 flex items-center gap-2">
              <span className="text-white text-xs font-semibold">
                {cam?.name || formatLabel(cam?.camera_id) || "Camera"}
              </span>
              {formatFloor(cam?.floor) && (
                <span className="text-white/50 text-[10px]">
                  {formatFloor(cam?.floor)}
                </span>
              )}
            </div>
            {roleStyle && (
              <span
                className="text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full"
                style={{ backgroundColor: roleStyle.bg, color: roleStyle.text }}
              >
                {roleStyle.label}
              </span>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            {/* Stream toggle */}
            <button
              onClick={handleToggleStream}
              title={streamEnabled ? "Turn stream off (detection keeps running)" : "Turn stream on"}
              className={`flex items-center gap-1 px-2 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wide cursor-pointer transition-colors border ${
                streamEnabled
                  ? "bg-white/10 hover:bg-white/20 text-white/80 border-white/10"
                  : "bg-white/5 hover:bg-white/15 text-white/50 border-white/10"
              }`}
            >
              {streamEnabled ? "Stream On" : "Stream Off"}
            </button>

            {/* Status badge */}
            <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wide ${
              showOffline
                ? "bg-red-500/20 text-red-300"
                : showPaused
                  ? "bg-white/10 text-white/50"
                  : "bg-emerald-500/20 text-emerald-300"
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${
                showOffline ? "bg-red-400" : showPaused ? "bg-white/40" : "bg-emerald-400 animate-pulse"
              }`}></span>
              {showOffline ? "Offline" : showPaused ? "Paused" : "Live"}
            </div>
          </div>
        </div>

        {/* ── Bottom overlay: Spotlight hint on hover ── */}
        <div className={`hidden md:block absolute bottom-0 left-0 right-0 p-3 bg-linear-to-t from-black/60 to-transparent transition-opacity duration-200 ${hovered && !isSpotlight ? "opacity-100" : "opacity-0"}`}>
          <div className="flex items-center justify-center gap-1.5">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-white/80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
            </svg>
            <span className="text-white/80 text-[11px] font-medium">Click to spotlight</span>
          </div>
        </div>

        {/* ── Spotlight indicator ── */}
        {isSpotlight && (
          <div className="absolute bottom-3 left-3">
            <div className="bg-primary/90 backdrop-blur-sm text-white text-[10px] font-semibold px-2.5 py-1 rounded-full flex items-center gap-1.5">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
              </svg>
              Spotlight
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Custom comparator: re-render only when a prop that visibly affects
// this camera changes. React's default shallow compare would re-render
// on every Dashboard state update because `cam`, `patients`, etc. are
// new references each render even when contents are unchanged.
function propsEqual(prev, next) {
  if (prev.streamVersion !== next.streamVersion) return false;
  if (prev.offline !== next.offline) return false;
  if (prev.isActive !== next.isActive) return false;
  if (prev.isSpotlight !== next.isSpotlight) return false;
  if (prev.searchState !== next.searchState) return false;
  if (prev.onSpotlight !== next.onSpotlight) return false;

  // Shallow-ish checks on reference types
  if (prev.cam?.camera_id !== next.cam?.camera_id) return false;
  if (prev.cam?.name !== next.cam?.name) return false;
  if (prev.cam?.floor !== next.cam?.floor) return false;
  if (prev.cam?.role !== next.cam?.role) return false;

  const ps = prev.selected?.location?.camera_id;
  const ns = next.selected?.location?.camera_id;
  if (ps !== ns) return false;

  const pu = prev.searchUser?.id ?? null;
  const nu = next.searchUser?.id ?? null;
  if (pu !== nu) return false;

  // livePatients: compare by camera-matching subset length. Dashboard
  // rebuilds this array every event; a length change is a real change.
  const pp = prev.patients || [];
  const np = next.patients || [];
  if (pp.length !== np.length) return false;
  for (let i = 0; i < pp.length; i++) {
    if (pp[i]?.id !== np[i]?.id) return false;
  }

  return true;
}

export default memo(CameraCard, propsEqual);
