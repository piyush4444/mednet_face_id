import { useState, useEffect, useCallback, useMemo } from "react";
import CameraCard from "./CameraCard";

/**
 * Google Meet-style camera grid with spotlight support.
 *
 * Layout modes:
 * 1. **Spotlight** — One camera is large, the rest are in a filmstrip on the right.
 * 2. **Auto-grid** — All cameras share equal space, grid adapts to count.
 */
export default function CameraGrid({
  cameras,
  selected,
  livePatients = [],
  activeCameras = new Map(),
  searchUser,
  searchState,
  streamVersion = {},
  cameraOffline = {},
}) {
  const [spotlightId, setSpotlightId] = useState(null);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Stable handler — CameraCard is memoized and compares `onSpotlight`
  // by reference. A fresh function every render would bust memo.
  const handleSpotlight = useCallback(
    (cameraId) => {
      if (isMobile) return;
      setSpotlightId((prev) => (prev === cameraId ? null : cameraId));
    },
    [isMobile],
  );

  // If we're on mobile, ignore any active spotlight
  const spotlightCam = (spotlightId && !isMobile)
    ? cameras.find((c) => c.camera_id === spotlightId)
    : null;
  const otherCams = spotlightCam
    ? cameras.filter((c) => c.camera_id !== spotlightId)
    : cameras;

  // Group livePatients by camera_id ONCE per render so buildCardProps
  // doesn't redo an O(N) filter per card. Memoised on the reference —
  // Dashboard currently rebuilds the array each event, which will still
  // recompute, but at least the cost is linear not quadratic.
  const patientsByCam = useMemo(() => {
    const m = new Map();
    for (const p of livePatients) {
      const list = m.get(p.camera_id) || [];
      list.push(p);
      m.set(p.camera_id, list);
    }
    return m;
  }, [livePatients]);

  const buildCardProps = (cam) => ({
    cam,
    selected,
    patients: patientsByCam.get(cam.camera_id) || [],
    isActive: activeCameras.has(cam.camera_id),
    searchUser,
    searchState,
    streamVersion: streamVersion[cam.camera_id] || 0,
    offline: cameraOffline[cam.camera_id] || false,
    onSpotlight: handleSpotlight,
  });

  /* ──────────────── SPOTLIGHT LAYOUT ──────────────── */
  if (spotlightCam) {
    return (
      <div className="flex flex-col gap-3 w-full overflow-y-auto">
        {/* Spotlight — full width, large */}
        <div className="w-full" style={{ minHeight: "60vh" }}>
          <CameraCard
            key={spotlightCam.camera_id}
            {...buildCardProps(spotlightCam)}
            isSpotlight={true}
          />
        </div>

        {/* Other cameras — same responsive grid below */}
        {otherCams.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 w-full">
            {otherCams.map((cam) => (
              <CameraCard
                key={cam.camera_id}
                {...buildCardProps(cam)}
                isSpotlight={false}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  /* ──────────────── AUTO-GRID LAYOUT (responsive cols, scrollable) ──────────────── */
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 w-full overflow-y-auto">
      {cameras.map((cam) => (
        <CameraCard
          key={cam.camera_id}
          {...buildCardProps(cam)}
          isSpotlight={false}
        />
      ))}
    </div>
  );
}
