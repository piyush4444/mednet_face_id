import { useEffect, useMemo, useState } from "react";
import { LocalizationProvider } from "@mui/x-date-pickers/LocalizationProvider";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFns";
import { DateCalendar } from "@mui/x-date-pickers/DateCalendar";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { API_URL as API_BASE } from "../config";
import { listPatientVisits, listPatientsByDate } from "../api/frontdesk";
import useCameraNames from "../hooks/useCameraNames";

function pad(n) {
  return String(n).padStart(2, "0");
}

function toISODate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

// Collapse many sessions → one row per patient.
function dedupePatients(sessions) {
  const byId = new Map();
  for (const s of sessions) {
    const key = s.patient_id;
    const prev = byId.get(key);
    const duration = s.duration ?? 0;
    if (!prev) {
      byId.set(key, {
        patient_id: s.patient_id,
        name: s.name,
        user_type: s.user_type || "PATIENT",
        floors: new Set(s.floor ? [s.floor] : []),
        sessions: 1,
        totalDuration: duration,
        firstEntry: s.entry,
        lastExit: s.exit,
      });
    } else {
      prev.sessions += 1;
      prev.totalDuration += duration;
      if (s.floor) prev.floors.add(s.floor);
      if (s.entry && (!prev.firstEntry || s.entry < prev.firstEntry)) {
        prev.firstEntry = s.entry;
      }
      if (s.exit && (!prev.lastExit || s.exit > prev.lastExit)) {
        prev.lastExit = s.exit;
      }
    }
  }
  return Array.from(byId.values()).map((p) => ({
    ...p,
    floors: Array.from(p.floors),
  }));
}

const TYPE_BADGE = {
  PATIENT:  { bg: "bg-blue-100 dark:bg-blue-500/15",       text: "text-blue-700 dark:text-blue-300",       label: "Patient" },
  DOCTOR:   { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300", label: "Doctor" },
  EMPLOYEE: { bg: "bg-amber-100 dark:bg-amber-500/15",     text: "text-amber-700 dark:text-amber-300",     label: "Employee" },
  VISITOR:  { bg: "bg-purple-100 dark:bg-purple-500/15",   text: "text-purple-700 dark:text-purple-300",   label: "Visitor" },
  RELATIVE: { bg: "bg-pink-100 dark:bg-pink-500/15",       text: "text-pink-700 dark:text-pink-300",       label: "Relative" },
};
const USER_TYPE_FILTERS = ["PATIENT", "DOCTOR", "EMPLOYEE", "VISITOR", "RELATIVE"];

/* ── Raw CSS for MUI DateCalendar ──
   Injected via <style> tag in JSX because:
   1. MUI's internal styles have very high specificity (sx prop loses)
   2. Tailwind v4 (@tailwindcss/vite) tree-shakes CSS class names not in source files
   The only reliable approach is a raw <style> tag rendered in React. */
const MUI_CALENDAR_CSS = `
.MuiDateCalendar-root {
  width: 100% !important;
  max-width: none !important;
  max-height: none !important;
  height: auto !important;
}
.MuiPickersCalendarHeader-label {
  font-size: 1.3rem !important;
  font-weight: 700 !important;
}
.MuiPickersArrowSwitcher-root .MuiIconButton-root,
.MuiPickersArrowSwitcher-root .MuiPickersArrowSwitcher-button {
  width: 40px !important;
  height: 40px !important;
}
.MuiDayCalendar-header {
  justify-content: space-around !important;
}
.MuiDayCalendar-weekDayLabel {
  font-size: 0.95rem !important;
  font-weight: 600 !important;
  width: 56px !important;
  height: 44px !important;
}
.MuiDayCalendar-slideTransition {
  min-height: 380px !important;
}
.MuiDayCalendar-weekContainer {
  justify-content: space-around !important;
  margin: 6px 0 !important;
}
/* MUI v9 uses singular MuiPickerDay (not MuiPickersDay) */
.MuiPickerDay-root,
.MuiPickersDay-root {
  width: 56px !important;
  height: 56px !important;
  font-size: 1.15rem !important;
  font-weight: 500 !important;
  border-radius: 16px !important;
  transition: all 0.15s ease !important;
}
.MuiPickerDay-root:hover,
.MuiPickersDay-root:hover {
  transform: scale(1.08);
}
.MuiPickerDay-root.Mui-selected,
.MuiPickersDay-root.Mui-selected {
  font-weight: 700 !important;
  font-size: 1.2rem !important;
  box-shadow: 0 4px 14px rgba(124, 58, 237, 0.3) !important;
}
.MuiPickerDay-today:not(.Mui-selected),
.MuiPickersDay-today:not(.Mui-selected) {
  border-width: 2px !important;
}
/* Attribute-selector fallback — catches MUI v7/v8/v9 class names */
[class*="PickersDay-root"],
[class*="PickerDay-root"] {
  width: 56px !important;
  height: 56px !important;
  font-size: 1.15rem !important;
  font-weight: 500 !important;
  border-radius: 16px !important;
}
[class*="CalendarHeader-label"] {
  font-size: 1.3rem !important;
  font-weight: 700 !important;
}
[class*="DayCalendar-weekDayLabel"] {
  font-size: 0.95rem !important;
  font-weight: 600 !important;
  width: 56px !important;
  height: 44px !important;
}
[class*="DateCalendar-root"] {
  width: 100% !important;
  max-width: none !important;
  max-height: none !important;
  height: auto !important;
}
[class*="DayCalendar-slideTransition"] {
  min-height: 380px !important;
}
[class*="DayCalendar-weekContainer"] {
  justify-content: space-around !important;
  margin: 6px 0 !important;
}
[class*="DayCalendar-header"] {
  justify-content: space-around !important;
}
/* ── Year Picker ── */
[class*="YearCalendar-root"],
.MuiYearCalendar-root {
  width: 100% !important;
  max-height: 380px !important;
  padding: 12px 8px !important;
}
[class*="PickersYear-yearButton"],
.MuiPickersYear-yearButton {
  height: 48px !important;
  font-size: 1.1rem !important;
  font-weight: 500 !important;
  border-radius: 14px !important;
  transition: all 0.15s ease !important;
}
[class*="PickersYear-yearButton"]:hover,
.MuiPickersYear-yearButton:hover {
  transform: scale(1.05);
  background-color: rgba(124, 58, 237, 0.08) !important;
}
[class*="PickersYear-yearButton"].Mui-selected,
.MuiPickersYear-yearButton.Mui-selected {
  font-weight: 700 !important;
  font-size: 1.15rem !important;
  box-shadow: 0 4px 14px rgba(124, 58, 237, 0.3) !important;
}
/* ── Mobile View Adjustments ── */
@media (max-width: 768px) {
  .MuiPickerDay-root,
  .MuiPickersDay-root,
  [class*="PickersDay-root"],
  [class*="PickerDay-root"] {
    width: 40px !important;
    height: 40px !important;
    font-size: 0.95rem !important;
  }
  .MuiDayCalendar-weekDayLabel,
  [class*="DayCalendar-weekDayLabel"] {
    width: 40px !important;
    height: 32px !important;
    font-size: 0.8rem !important;
  }
  .MuiDayCalendar-slideTransition,
  [class*="DayCalendar-slideTransition"] {
    min-height: 260px !important;
  }
  .MuiPickersCalendarHeader-label,
  [class*="CalendarHeader-label"] {
    font-size: 1.15rem !important;
  }
  .MuiYearCalendar-root,
  [class*="YearCalendar-root"] {
    max-height: 280px !important;
    grid-template-columns: repeat(3, 1fr) !important;
  }
  .MuiPickersYear-yearButton,
  [class*="PickersYear-yearButton"] {
    height: 40px !important;
    font-size: 1rem !important;
  }
}
`;

export default function History() {
  const [view, setView] = useState("calendar"); // calendar | patient
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [activePatient, setActivePatient] = useState(null); // {patient_id, name, date}
  const [typeFilter, setTypeFilter] = useState(null); // null = ALL

  const dateStr = toISODate(selectedDate);

  // OPD patients (no sessions) for the selected date. Merged into the
  // patient list so front-desk-only patients are visible in History.
  const [opdPatients, setOpdPatients] = useState([]);

  useEffect(() => {
    if (view !== "calendar") return;
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [sessionsRes, opdRes] = await Promise.all([
          fetch(`${API_BASE}/history/date/${dateStr}`).then((r) => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          }),
          listPatientsByDate(dateStr).catch(() => ({ patients: [] })),
        ]);
        if (cancelled) return;
        setSessions(Array.isArray(sessionsRes) ? sessionsRes : []);
        setOpdPatients(opdRes?.patients || []);
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          setSessions([]);
          setOpdPatients([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [dateStr, view]);

  const uniquePatients = useMemo(() => {
    const base = dedupePatients(sessions);
    const byId = new Map(base.map((p) => [String(p.patient_id), p]));
    for (const op of opdPatients) {
      const key = String(op.patient_id);
      const existing = byId.get(key);
      if (existing) {
        existing.opdVisits = op.visits;
      } else {
        byId.set(key, {
          patient_id: op.patient_id,
          name: op.name,
          user_type: op.user_type || "PATIENT",
          floors: [],
          sessions: 0,
          totalDuration: 0,
          firstEntry: op.last_visit_at,
          lastExit: null,
          opdVisits: op.visits,
          opdOnly: true,
        });
      }
    }
    return Array.from(byId.values());
  }, [sessions, opdPatients]);

  const filteredPatients = useMemo(() => {
    if (!typeFilter) return uniquePatients;
    return uniquePatients.filter((p) => (p.user_type || "PATIENT") === typeFilter);
  }, [uniquePatients, typeFilter]);

  const openPatient = (p) => {
    setActivePatient({
      patient_id: p.patient_id,
      name: p.name,
      date: dateStr,
    });
    setView("patient");
  };

  const backToCalendar = () => {
    setActivePatient(null);
    setView("calendar");
  };

  if (view === "patient" && activePatient) {
    return (
      <LocalizationProvider dateAdapter={AdapterDateFns}>
        <PatientDetail patient={activePatient} onBack={backToCalendar} />
      </LocalizationProvider>
    );
  }

  /* ── Avatar color from patient name ── */
  const getAvatarColor = (name) => {
    const charCode = (name || "P").charCodeAt(0);
    const hue = (charCode * 37) % 360;
    return {
      bg: `hsl(${hue}, 70%, 92%)`,
      text: `hsl(${hue}, 55%, 38%)`,
    };
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <style dangerouslySetInnerHTML={{ __html: MUI_CALENDAR_CSS }} />
      <div
        className="animate-fade-in flex flex-col md:px-12 px-5 md:py-3 py-4"
        style={{ minHeight: "calc(100vh - 130px)" }}
      >
        {/* ── Page Header ── */}
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-extrabold text-text-main tracking-tight">
              Session History
            </h1>
            <p className="text-sm text-text-muted mt-1">
              Pick a date to view unique patients
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-text-light">Selected</p>
            <p className="text-lg font-bold text-primary">
              {selectedDate.toLocaleDateString("en-IN", {
                day: "numeric",
                month: "short",
                year: "numeric",
                timeZone: "Asia/Kolkata",
              })}
            </p>
          </div>
        </div>

        {/* ── Main Grid ── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-14 flex-1 md:py-6">
          {/* Calendar Panel */}
          <div className="lg:col-span-2 bg-card rounded-2xl shadow-md border border-primary/8 md:p-6 p-3 flex flex-col">
            <DateCalendar
              value={selectedDate}
              onChange={(d) => d && setSelectedDate(d)}
              disableFuture
            />
          </div>

          {/* Patients Panel */}
          <div className="lg:col-span-3 bg-card rounded-2xl shadow-md border border-primary/8 flex flex-col">
            {/* Panel Header */}
            <div className="px-6 py-5 border-b border-primary/8 flex justify-between items-center">
              <div>
                <h2 className="text-lg font-bold text-text-main">
                  Patients on{" "}
                  {selectedDate.toLocaleDateString("en-IN", {
                    weekday: "long",
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                    timeZone: "Asia/Kolkata",
                  })}
                </h2>
                <p className="text-xs text-text-light mt-1">
                  Click a patient to view their full session history
                </p>
              </div>
              <span className="bg-primary/10 text-primary text-sm font-bold px-4 py-2 rounded-full min-w-9 text-center">
                {filteredPatients.length}
                {typeFilter ? `/${uniquePatients.length}` : ""}
              </span>
            </div>

            {/* Type filter strip */}
            <div className="px-6 py-3 border-b border-primary/8 flex flex-wrap gap-2">
              <button
                onClick={() => setTypeFilter(null)}
                className={`px-3 py-1 text-[11px] font-bold rounded-lg transition-all cursor-pointer ${
                  typeFilter === null
                    ? "bg-primary text-white"
                    : "bg-primary/8 text-text-muted hover:bg-primary/15"
                }`}
              >
                All
              </button>
              {USER_TYPE_FILTERS.map((t) => {
                const count = uniquePatients.filter((p) => (p.user_type || "PATIENT") === t).length;
                if (count === 0 && typeFilter !== t) return null;
                return (
                  <button
                    key={t}
                    onClick={() => setTypeFilter(t)}
                    className={`px-3 py-1 text-[11px] font-bold rounded-lg transition-all cursor-pointer ${
                      typeFilter === t
                        ? "bg-primary text-white"
                        : "bg-primary/8 text-text-muted hover:bg-primary/15"
                    }`}
                  >
                    {TYPE_BADGE[t].label} {count > 0 ? `· ${count}` : ""}
                  </button>
                );
              })}
            </div>

            {/* Patient List */}
            <div className="p-5 grow overflow-y-auto">
              {loading ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3">
                  <div className="w-8 h-8 border-3 border-primary/20 border-t-primary rounded-full animate-spin" />
                  <p className="text-sm text-text-muted font-medium">
                    Loading sessions…
                  </p>
                </div>
              ) : error ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3">
                  <div className="w-12 h-12 rounded-2xl bg-danger/10 flex items-center justify-center">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-6 w-6 text-danger"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z"
                      />
                    </svg>
                  </div>
                  <p className="text-sm text-danger font-semibold">
                    Failed to load: {error}
                  </p>
                </div>
              ) : filteredPatients.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3">
                  <div className="w-14 h-14 rounded-2xl bg-primary/8 flex items-center justify-center">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-7 w-7 text-primary/40"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                      />
                    </svg>
                  </div>
                  <p className="text-sm text-text-muted font-medium">
                    No patients on this date
                  </p>
                  <p className="text-xs text-text-light">
                    Try selecting a different date
                  </p>
                </div>
              ) : (
                <ul className="space-y-3">
                  {filteredPatients.map((p) => {
                    const avatar = getAvatarColor(p.name);
                    const badge = TYPE_BADGE[p.user_type || "PATIENT"] || TYPE_BADGE.PATIENT;
                    return (
                      <li key={p.patient_id}>
                        <button
                          onClick={() => openPatient(p)}
                          className="w-full text-left flex items-center gap-4 p-4 rounded-xl border border-primary/8 bg-card hover:bg-primary/3 hover:border-primary/20 hover:shadow-md transition-all duration-200 cursor-pointer group"
                        >
                          {/* Avatar */}
                          <div
                            className="w-12 h-12 rounded-xl flex items-center justify-center font-bold text-lg shrink-0 transition-transform duration-200 group-hover:scale-105"
                            style={{
                              backgroundColor: avatar.bg,
                              color: avatar.text,
                            }}
                          >
                            {(p.name || "P").charAt(0).toUpperCase()}
                          </div>

                          {/* Info */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <p className="font-semibold text-text-main text-base truncate group-hover:text-primary transition-colors">
                                {p.name || `User #${p.patient_id}`}
                              </p>
                              <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${badge.bg} ${badge.text}`}>
                                {badge.label}
                              </span>
                              {p.opdVisits ? (
                                <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-md bg-primary/10 text-primary">
                                  OPD ×{p.opdVisits}
                                </span>
                              ) : null}
                            </div>
                            <div className="flex items-center gap-3 mt-1">
                              <span className="text-xs text-text-muted flex items-center gap-1">
                                <svg
                                  xmlns="http://www.w3.org/2000/svg"
                                  className="h-3.5 w-3.5"
                                  fill="none"
                                  viewBox="0 0 24 24"
                                  stroke="currentColor"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
                                  />
                                </svg>
                                {p.sessions} session
                                {p.sessions === 1 ? "" : "s"}
                              </span>
                              <span className="text-xs text-text-muted flex items-center gap-1">
                                <svg
                                  xmlns="http://www.w3.org/2000/svg"
                                  className="h-3.5 w-3.5"
                                  fill="none"
                                  viewBox="0 0 24 24"
                                  stroke="currentColor"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M13 10V3L4 14h7v7l9-11h-7z"
                                  />
                                </svg>
                                {formatDuration(p.totalDuration)}
                              </span>
                              {p.floors.length > 0 && (
                                <span className="text-xs text-text-muted flex items-center gap-1">
                                  <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    className="h-3.5 w-3.5"
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
                                  {p.floors.join(", ")}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Arrow */}
                          <div className="w-9 h-9 rounded-xl bg-primary/8 flex items-center justify-center text-primary group-hover:bg-primary group-hover:text-white transition-all duration-200 shrink-0">
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
                                d="M9 5l7 7-7 7"
                              />
                            </svg>
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      </div>
    </LocalizationProvider>
  );
}

function PatientDetail({ patient, onBack }) {
  const cameraName = useCameraNames();
  // Filter mode: "date" = patient.date only | "all" = entire history | "custom" = pick date
  const [mode, setMode] = useState("date");
  const [customDate, setCustomDate] = useState(
    patient.date ? new Date(patient.date + "T00:00:00") : new Date(),
  );

  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [opdVisits, setOpdVisits] = useState([]);
  const [opdLoading, setOpdLoading] = useState(false);
  const [view, setView] = useState("sessions"); // "sessions" | "opd"

  useEffect(() => {
    let cancelled = false;
    setOpdLoading(true);
    listPatientVisits(patient.patient_id)
      .then((res) => {
        if (!cancelled) setOpdVisits(res.visits || []);
      })
      .catch(() => {
        if (!cancelled) setOpdVisits([]);
      })
      .finally(() => {
        if (!cancelled) setOpdLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [patient.patient_id]);

  const filteredOpdVisits = useMemo(() => {
    if (mode === "all") return opdVisits;
    const target = mode === "date" ? patient.date : toISODate(customDate);
    return opdVisits.filter((v) => {
      if (!v.created_at) return false;
      return v.created_at.slice(0, 10) === target;
    });
  }, [opdVisits, mode, customDate, patient.date]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `${API_BASE}/history/patient/${patient.patient_id}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setHistory(Array.isArray(data) ? data : []);
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          setHistory([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [patient.patient_id]);

  const filtered = useMemo(() => {
    if (mode === "all") return history;
    const target = mode === "date" ? patient.date : toISODate(customDate);
    return history.filter((s) => {
      if (!s.entry) return false;
      return s.entry.slice(0, 10) === target;
    });
  }, [history, mode, customDate, patient.date]);

  const summary = useMemo(() => {
    const totalSecs = filtered.reduce(
      (acc, s) =>
        acc +
        (s.exit && s.entry ? (new Date(s.exit) - new Date(s.entry)) / 1000 : 0),
      0,
    );
    return {
      count: filtered.length,
      duration: totalSecs,
    };
  }, [filtered]);

  const filterLabel = () => {
    if (mode === "all") return "All history";
    if (mode === "date") return `On ${patient.date}`;
    return `On ${toISODate(customDate)}`;
  };

  return (
    <div className="animate-fade-in">
      {/* ── Header ── */}
      <div className="mb-6 flex items-center gap-4">
        <button
          onClick={onBack}
          className="w-10 h-10 rounded-xl bg-primary/8 hover:bg-primary/15 text-primary flex items-center justify-center transition-colors cursor-pointer"
          aria-label="Back"
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
              strokeWidth={2.5}
              d="M15 19l-7-7 7-7"
            />
          </svg>
        </button>
        <div>
          <h1 className="text-2xl font-extrabold text-text-main tracking-tight">
            {patient.name || `Patient #${patient.patient_id}`}
          </h1>
          <p className="text-sm text-text-muted mt-0.5">{filterLabel()}</p>
        </div>
      </div>

      {/* ── Filter Bar ── */}
      <div className="bg-card rounded-2xl shadow-md border border-primary/8 mb-6 px-6 py-4 flex flex-wrap items-center gap-4">
        <div className="flex rounded-xl overflow-hidden border border-primary/20">
          <FilterTab
            active={mode === "date"}
            onClick={() => setMode("date")}
            label="This date"
          />
          <FilterTab
            active={mode === "all"}
            onClick={() => setMode("all")}
            label="All history"
          />
          <FilterTab
            active={mode === "custom"}
            onClick={() => setMode("custom")}
            label="Pick date"
          />
        </div>

        {mode === "custom" && (
          <DatePicker
            value={customDate}
            onChange={(d) => d && setCustomDate(d)}
            disableFuture
            slotProps={{ textField: { size: "small" } }}
          />
        )}

        <div className="ml-auto flex items-center gap-5">
          <div className="text-center">
            <p className="text-2xl font-extrabold text-text-main">
              {summary.count}
            </p>
            <p className="text-[11px] text-text-light font-semibold uppercase tracking-wider">
              Sessions
            </p>
          </div>
          <div className="w-px h-8 bg-primary/10" />
          <div className="text-center">
            <p className="text-2xl font-extrabold text-primary">
              {formatDuration(summary.duration)}
            </p>
            <p className="text-[11px] text-text-light font-semibold uppercase tracking-wider">
              Total
            </p>
          </div>
        </div>
      </div>

      {/* ── Body: sidebar + active section ──
          Sidebar stays put; only the right pane scrolls. */}
      <div
        className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-5"
        style={{ height: "calc(100vh - 280px)", minHeight: 360 }}
      >
        {/* Sidebar */}
        <aside className="bg-card rounded-2xl shadow-md border border-primary/8 p-3 h-fit sticky top-0">
          <nav className="flex flex-col gap-1.5">
            <SideButton
              active={view === "sessions"}
              onClick={() => setView("sessions")}
              label="Sessions"
              description="Entry / exit tracking"
              count={summary.count}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              }
            />
            <SideButton
              active={view === "opd"}
              onClick={() => setView("opd")}
              label="OPD Visits"
              description="Front-desk tokens"
              count={filteredOpdVisits.length}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              }
            />
          </nav>
        </aside>

        <section className="min-w-0 overflow-y-auto pr-1 no-scrollbar">
          {view === "sessions" && (
      <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-4">
        {loading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <div className="w-8 h-8 border-3 border-primary/20 border-t-primary rounded-full animate-spin" />
            <p className="text-sm text-text-muted font-medium">
              Loading sessions…
            </p>
          </div>
        ) : error ? (
          <p className="text-sm text-danger text-center py-16 font-semibold">
            Failed to load: {error}
          </p>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <div className="w-14 h-14 rounded-2xl bg-primary/8 flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-7 w-7 text-primary/40"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                />
              </svg>
            </div>
            <p className="text-sm text-text-muted font-medium">
              No sessions for this filter
            </p>
          </div>
        ) : (
          <ul className="space-y-2">
            {filtered.map((s, i) => (
              <li
                key={`${s.entry}-${i}`}
                className="relative pl-6 border-l-2 border-primary/20"
              >
                <span className="absolute -left-1.75 top-3 w-3 h-3 rounded-full bg-primary ring-2 ring-white" />
                <div className="bg-card border border-primary/8 rounded-xl px-4 py-2.5 hover:shadow-sm transition-shadow">
                  <div className="grid grid-cols-2 gap-3 mb-1.5">
                    <div>
                      <span className="text-[10px] uppercase tracking-wide text-text-light font-bold block mb-0.5">
                        Entry
                      </span>
                      <span className="text-sm font-semibold text-text-main">
                        {formatTime(s.entry)}
                      </span>
                    </div>
                    <div>
                      <span className="text-[10px] uppercase tracking-wide text-text-light font-bold block mb-0.5">
                        Exit
                      </span>
                      <span className="text-sm font-semibold text-text-main">
                        {formatTime(s.exit)}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 pt-2 border-t border-primary/6">
                    <span className="text-xs text-text-muted flex items-center gap-1.5">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-3.5 w-3.5"
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
                      Floor: {s.floor || "—"}
                    </span>
                    <span className="text-xs text-text-muted flex items-center gap-1.5">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-3.5 w-3.5"
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
                      Cam: {cameraName(s.camera_id)}
                    </span>
                    {s.exit && s.entry && (
                      <span className="text-xs text-primary font-semibold flex items-center gap-1.5 ml-auto">
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          className="h-3.5 w-3.5"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
                          />
                        </svg>
                        {formatDuration(
                          (new Date(s.exit) - new Date(s.entry)) / 1000,
                        )}
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
          )}

          {view === "opd" && (
      <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
        <div className="px-6 py-4 border-b border-primary/8 flex items-center justify-between">
          <div>
            <h2 className="text-base font-extrabold text-text-main">OPD Visits</h2>
            <p className="text-xs text-text-muted mt-0.5">
              Tokens generated at the front desk for this patient.
            </p>
          </div>
          <span className="text-xs text-text-muted font-semibold">
            {filteredOpdVisits.length} visit{filteredOpdVisits.length === 1 ? "" : "s"}
          </span>
        </div>

        {opdLoading ? (
          <div className="flex items-center justify-center py-10 gap-3">
            <div className="w-6 h-6 border-2 border-primary/20 border-t-primary rounded-full animate-spin" />
            <p className="text-sm text-text-muted font-medium">Loading visits…</p>
          </div>
        ) : filteredOpdVisits.length === 0 ? (
          <p className="text-sm text-text-muted text-center py-10">
            No OPD visits for this filter.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-background">
                <tr className="text-left text-xs font-bold text-text-light uppercase tracking-wide">
                  <th className="px-5 py-4 whitespace-nowrap">Time</th>
                  <th className="px-5 py-4 whitespace-nowrap">Token</th>
                  <th className="px-5 py-4 whitespace-nowrap">Type</th>
                  <th className="px-5 py-4 whitespace-nowrap">Department</th>
                  <th className="px-5 py-4 whitespace-nowrap">Doctor</th>
                  <th className="px-5 py-4 whitespace-nowrap">Room</th>
                  <th className="px-5 py-4 whitespace-nowrap">Status</th>
                  <th className="px-5 py-4">Note</th>
                </tr>
              </thead>
              <tbody>
                {filteredOpdVisits.map((v) => (
                  <tr key={v.id} className="border-t border-primary/8 hover:bg-primary/5">
                    <td className="px-5 py-4 whitespace-nowrap font-semibold text-text-main">
                      {v.created_at ? new Date(v.created_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap font-bold text-primary">
                      {v.token_number}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap text-text-muted font-semibold">
                      {v.visit_type}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap font-semibold text-text-main">
                      {v.department_name ?? "—"}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap font-semibold text-text-main">
                      {v.doctor_name ?? "—"}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap font-semibold text-text-main">
                      {v.room_name ?? "—"}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap">
                      <span className={`px-2.5 py-1 rounded-md text-xs font-bold ${
                        v.status === "DONE" ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 dark:text-emerald-400"
                        : v.status === "IN_CONSULT" ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                        : v.status === "CANCELLED" ? "bg-red-500/10 text-red-600 dark:text-red-400"
                        : "bg-primary/10 text-primary"
                      }`}>
                        {v.status}
                      </span>
                    </td>
                    <td className="px-5 py-4 max-w-xs truncate text-text-muted">
                      {v.note ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
          )}
        </section>
      </div>
    </div>
  );
}

function SideButton({ active, onClick, label, description, count, icon }) {
  return (
    <button
      onClick={onClick}
      className={`text-left px-3.5 py-3 rounded-xl transition-all flex items-start gap-3 ${
        active ? "bg-primary/10 text-primary" : "text-text-main hover:bg-primary/5"
      }`}
    >
      <span className={`mt-0.5 ${active ? "text-primary" : "text-text-light"}`}>
        {icon}
      </span>
      <span className="flex-1 flex flex-col">
        <span className="flex items-center justify-between gap-2">
          <span className="text-[15px] font-bold leading-tight">{label}</span>
          {typeof count === "number" && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${
              active ? "bg-primary text-white" : "bg-primary/10 text-primary"
            }`}>
              {count}
            </span>
          )}
        </span>
        {description && (
          <span className={`text-[11px] leading-snug mt-1 ${
            active ? "text-primary/70" : "text-text-muted"
          }`}>
            {description}
          </span>
        )}
      </span>
    </button>
  );
}

function FilterTab({ active, onClick, label }) {
  return (
    <button
      onClick={onClick}
      className={`px-5 py-2.5 text-sm font-bold transition-colors cursor-pointer ${
        active
          ? "bg-primary text-white"
          : "bg-card text-text-muted hover:bg-primary/8"
      }`}
    >
      {label}
    </button>
  );
}
