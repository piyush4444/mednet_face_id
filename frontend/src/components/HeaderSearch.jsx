/**
 * HeaderSearch — global search control rendered in the Header.
 *
 * - A magnifier-icon button that toggles a floating popover.
 * - Popover contains: input, Find action, result card, Pause/Resume/Clear.
 * - State lives in ../store/searchStore so the Dashboard can react to
 *   the same user/state without prop drilling.
 */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  useSearchStore,
  toggleSearch,
  closeSearch,
  openSearch,
  setSearchInput,
  setSearchUser,
  setSearchState,
  clearSearch,
} from "../store/searchStore";
import { API_URL as API } from "../config";

const STATUS_STYLES = {
  searching: { label: "Searching…", color: "text-primary bg-primary/10" },
  found: { label: "On camera", color: "text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-500/15" },
  lost: { label: "Lost — still looking", color: "text-yellow-800 bg-yellow-100 dark:text-yellow-300 dark:bg-yellow-500/15" },
  paused: { label: "Paused", color: "text-gray-600 bg-gray-100 dark:text-gray-300 dark:bg-gray-500/15" },
};

export default function HeaderSearch() {
  const { open, input, user, state } = useSearchStore();
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  // Focus input when opened
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        closeSearch();
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && closeSearch();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const handleFind = async () => {
    const query = input.trim();
    if (!query) return;

    // Route users to the Dashboard where the search feedback (camera
    // highlighting, active-cameras map) lives.
    if (window.location.pathname !== "/") {
      navigate("/");
    }

    try {
      const res = await fetch(
        `${API}/tracking/find?query=${encodeURIComponent(query)}`,
        { headers: { "ngrok-skip-browser-warning": "true" } },
      );
      const data = await res.json();

      if (!data.patient) {
        setSearchUser(null);
        setSearchState("idle");
        return;
      }

      setSearchUser({
        id: data.patient.id,
        name: data.patient.name,
        mrn: data.patient.mrn,
        lastSeenCamera: data.location?.camera_id || null,
        lastSeenCameraName: data.location?.camera_name || null,
        lastSeenFloor: data.location?.floor || null,
        lastSeenAt: data.last_seen_at,
        status: data.status,
      });
      // If the backend says they're currently inside, the dashboard's WS
      // listener will flip the state to "found" the next time we see them
      // on camera. Start in "searching" (or "found" if location is known
      // right now) so the operator sees immediate feedback.
      setSearchState(data.found ? "found" : "searching");
    } catch (err) {
      console.error("[SEARCH] failed:", err);
      setSearchState("idle");
    }
  };

  const statusStyle = state && STATUS_STYLES[state];

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => (open ? closeSearch() : openSearch())}
        aria-label="Search patient"
        aria-expanded={open}
        className={`
          flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold cursor-pointer
          transition-all duration-200
          ${
            open || user
              ? "bg-primary/10 text-primary shadow-sm"
              : "text-text-muted hover:text-text-main hover:bg-primary/5"
          }
        `}
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
            strokeWidth={2}
            d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z"
          />
        </svg>
        <span className="hidden md:inline">
          {user ? user.name : "Search"}
        </span>
      </button>

      {/* Popover */}
      {open && (
        <div
          className="absolute right-0 top-full mt-2 w-90 bg-card rounded-2xl shadow-xl border border-primary/10 overflow-hidden animate-fade-in z-40"
        >
          {/* Input row */}
          <div className="p-3 flex gap-2 border-b border-primary/8 bg-linear-to-b from-primary/5 to-transparent">
            <div className="flex-1 relative">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-primary/70"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z"
                />
              </svg>
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleFind()}
                placeholder="Search MRN / name…"
                className="w-full pl-9 pr-3 py-2 text-sm font-semibold bg-card rounded-xl border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 outline-none transition"
              />
            </div>
            <button
              onClick={handleFind}
              disabled={!input.trim()}
              className="px-3.5 py-2 rounded-xl text-sm font-bold gradient-primary text-white shadow-sm hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition"
            >
              Find
            </button>
          </div>

          {/* Result */}
          {user ? (
            <div className="p-4 flex flex-col gap-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="font-extrabold text-base text-text-main truncate">
                    {user.name}
                  </h3>
                  <p className="text-[11px] font-mono text-text-muted mt-0.5">
                    MRN: {user.mrn} · ID: {user.id}
                  </p>
                </div>
                {statusStyle && (
                  <span
                    className={`text-[10px] font-bold uppercase tracking-wide px-2 py-1 rounded-full whitespace-nowrap ${statusStyle.color}`}
                  >
                    {statusStyle.label}
                  </span>
                )}
              </div>

              {/* Current detection — camera + floor.
                  Hidden once the search is "lost" / "idle" since we no
                  longer have a live fix on the patient. */}
              {(state === "found" || state === "searching" || state === "paused") &&
              (user.lastSeenCameraName || user.lastSeenCamera || user.lastSeenFloor) ? (
                <div className="rounded-xl border border-primary/15 bg-primary/5 px-3 py-2 flex flex-col gap-1.5">
                  <div className="flex items-center gap-2 text-xs">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-3.5 w-3.5 text-primary shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
                      />
                    </svg>
                    <span className="text-[10px] font-bold uppercase tracking-wide text-text-muted">
                      Camera
                    </span>
                    <span className="font-semibold text-text-main truncate">
                      {user.lastSeenCameraName || user.lastSeenCamera || "—"}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-3.5 w-3.5 text-primary shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"
                      />
                    </svg>
                    <span className="text-[10px] font-bold uppercase tracking-wide text-text-muted">
                      Floor
                    </span>
                    <span className="font-semibold text-text-main truncate">
                      {user.lastSeenFloor
                        ? String(user.lastSeenFloor).replace(/_/g, " ")
                        : "—"}
                    </span>
                  </div>
                </div>
              ) : null}

              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => {
                    setSearchState(state === "paused" ? "searching" : "paused");
                  }}
                  className="px-3 py-1.5 rounded-lg text-xs font-bold border border-primary/20 text-primary hover:bg-primary/5 transition cursor-pointer"
                >
                  {state === "paused" ? "Resume" : "Pause"}
                </button>
                <button
                  onClick={() => clearSearch()}
                  className="px-3 py-1.5 rounded-lg text-xs font-bold border border-danger/20 text-danger hover:bg-danger/5 transition cursor-pointer"
                >
                  Clear
                </button>
              </div>

              <button
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => {
                  const patientId = user.id;
                  closeSearch();
                  navigate(`/patients/${patientId}`);
                }}
                className="mt-1 text-xs font-semibold text-primary hover:underline cursor-pointer text-left"
              >
                Open full profile →
              </button>
            </div>
          ) : (
            <div className="px-4 py-6 text-center">
              <p className="text-xs font-medium text-text-light">
                Enter a patient's MRN or name and press Enter.
              </p>
              <p className="text-[10px] text-text-light mt-1">
                Matching will highlight cameras where they're detected live.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
