import { useCallback, useEffect, useState } from "react";
import { API_URL } from "../config";

/**
 * Camera-id → display-name lookup.
 *
 * Events, sessions, and broadcasts carry the stable `camera_id`
 * (e.g. "cam_1fe6e1e2"); the human-readable name lives only in the
 * roster. This hook fetches the roster once and returns a resolver
 * that falls back to the raw id for cameras that have since been
 * deleted (old history rows).
 */
export default function useCameraNames() {
  const [names, setNames] = useState({});

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/cameras`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return;
        const map = {};
        for (const cam of list) map[cam.camera_id] = cam.name;
        setNames(map);
      })
      .catch(() => {
        // Roster unavailable — resolver falls back to raw ids.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return useCallback(
    (cameraId) => names[cameraId] || cameraId || "—",
    [names],
  );
}
