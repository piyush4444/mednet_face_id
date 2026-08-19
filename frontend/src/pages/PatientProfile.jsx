/**
 * PatientProfile — /patients/:id
 *
 * Responsibility:
 *   - Read :id from route params
 *   - Fetch GET /api/v1/patients/{id}
 *   - Render <PatientProfileCard /> with the result
 *
 * Data fetching lives here; the card is pure UI.
 */
import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import PatientProfileCard from "../components/PatientProfileCard";
import { API_URL as API } from "../config";
import { listPatientVisits, getVisitHistory } from "../api/frontdesk";

const TYPE_BADGE = {
  PATIENT:  { bg: "bg-blue-100 dark:bg-blue-500/15",       text: "text-blue-700 dark:text-blue-300",       label: "Patient" },
  DOCTOR:   { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300", label: "Doctor" },
  EMPLOYEE: { bg: "bg-amber-100 dark:bg-amber-500/15",     text: "text-amber-700 dark:text-amber-300",     label: "Employee" },
  VISITOR:  { bg: "bg-purple-100 dark:bg-purple-500/15",   text: "text-purple-700 dark:text-purple-300",   label: "Visitor" },
  RELATIVE: { bg: "bg-pink-100 dark:bg-pink-500/15",       text: "text-pink-700 dark:text-pink-300",       label: "Relative" },
};

export default function PatientProfile() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [patient, setPatient] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [visits, setVisits] = useState([]);
  const [visitsLoading, setVisitsLoading] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setVisitsLoading(true);
    listPatientVisits(id)
      .then((res) => {
        if (!cancelled) setVisits(res.visits || []);
      })
      .catch(() => {
        if (!cancelled) setVisits([]);
      })
      .finally(() => {
        if (!cancelled) setVisitsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();

    const fetchPatient = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API}/users/${id}`, {
          signal: ctrl.signal,
          headers: { "ngrok-skip-browser-warning": "true" },
        });
        if (res.status === 404) {
          throw new Error("User not found");
        }
        if (!res.ok) {
          throw new Error(`Server error (${res.status})`);
        }
        const data = await res.json();
        // /users/{id} uses the user_to_dict shape (current_status,
        // current_floor, current_camera_id). The PatientProfileCard
        // still reads `status`/`floor`/`camera_id`, so alias them
        // here rather than touch every consumer.
        const normalized = {
          ...data,
          status: data.status ?? data.current_status,
          floor: data.floor ?? data.current_floor,
          camera_id: data.camera_id ?? data.current_camera_id,
        };
        if (!cancelled) setPatient(normalized);
      } catch (err) {
        if (err.name === "AbortError") return;
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchPatient();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [id]);

  return (
    <div className="max-w-5xl mx-auto w-full pt-2">
      {/* Back button */}
      <button
        onClick={() => navigate(-1)}
        className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-text-muted hover:text-primary transition-colors cursor-pointer"
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
            d="M15 19l-7-7 7-7"
          />
        </svg>
        Back
      </button>

      {loading && (
        <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-8 flex items-center justify-center">
          <svg
            className="animate-spin h-5 w-5 text-primary mr-3"
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
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span className="text-sm font-semibold text-text-muted">
            Loading patient…
          </span>
        </div>
      )}

      {!loading && error && (
        <div className="bg-danger/8 text-danger border border-danger/20 rounded-2xl p-6 text-center">
          <p className="font-bold text-sm mb-1">Could not load patient</p>
          <p className="text-xs">{error}</p>
        </div>
      )}

      {!loading && !error && patient && (
        <>
          <PatientProfileCard patient={patient} />

          {/* Relations */}
          <RelationsPanel
            relations={patient.relations || []}
            ownerId={patient.id}
          />

          <div className="mt-5 bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
            <div className="px-5 py-3.5 border-b border-primary/8 flex items-center justify-between">
              <h3 className="text-sm font-bold text-text-main">OPD Visit History</h3>
              <span className="text-[11px] text-text-muted font-semibold">
                {visits.length} visit{visits.length === 1 ? "" : "s"}
              </span>
            </div>
            {visitsLoading ? (
              <p className="text-xs text-text-muted text-center py-6">Loading…</p>
            ) : visits.length === 0 ? (
              <p className="text-xs text-text-muted text-center py-6">
                No OPD visits yet for this patient.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-background">
                    <tr className="text-left text-[10px] font-bold text-text-muted uppercase tracking-wider">
                      <th className="px-3 py-2">Date</th>
                      <th className="px-3 py-2">Token</th>
                      <th className="px-3 py-2">Type</th>
                      <th className="px-3 py-2">Dept</th>
                      <th className="px-3 py-2">Doctor</th>
                      <th className="px-3 py-2">Room</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visits.map((v) => (
                      <VisitRow key={v.id} visit={v} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function RelationsPanel({ relations, ownerId }) {
  if (!relations || relations.length === 0) return null;
  return (
    <div className="mt-5 bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-primary/8 flex items-center justify-between">
        <h3 className="text-sm font-bold text-text-main">Relations</h3>
        <span className="text-[11px] text-text-muted font-semibold">
          {relations.length} link{relations.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="divide-y divide-primary/8">
        {relations.map((r) => {
          const partnerId = r.user_id === ownerId ? r.related_user_id : r.user_id;
          const badge = TYPE_BADGE[r.related_user_type] || TYPE_BADGE.PATIENT;
          return (
            <li key={r.id} className="px-5 py-3 flex items-center justify-between gap-3">
              <Link
                to={`/patients/${partnerId}`}
                className="flex items-center gap-2 min-w-0 hover:text-primary"
              >
                <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 ${badge.bg} ${badge.text}`}>
                  {badge.label}
                </span>
                <span className="text-sm font-semibold text-text-main truncate">
                  {r.related_user_name}
                </span>
                <span className="text-[10px] text-text-light font-mono shrink-0">
                  #{partnerId}
                </span>
              </Link>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-primary/8 text-primary">
                  {r.relation_type}
                </span>
                {r.note && (
                  <span className="text-[11px] text-text-muted italic max-w-50 truncate">
                    {r.note}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function VisitRow({ visit: v }) {
  const [open, setOpen] = useState(false);
  const [timeline, setTimeline] = useState(null);
  const [timelineLoading, setTimelineLoading] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && timeline === null && !timelineLoading) {
      setTimelineLoading(true);
      try {
        const res = await getVisitHistory(v.id);
        setTimeline(res.history || []);
      } catch {
        setTimeline([]);
      } finally {
        setTimelineLoading(false);
      }
    }
  };

  return (
    <>
      <tr className="border-t border-primary/8 cursor-pointer hover:bg-primary/5" onClick={toggle}>
        <td className="px-3 py-2 whitespace-nowrap">
          {v.created_at ? new Date(v.created_at).toLocaleString() : "—"}
        </td>
        <td className="px-3 py-2 font-bold text-primary">{v.token_number}</td>
        <td className="px-3 py-2">{v.visit_type}</td>
        <td className="px-3 py-2">{v.department_name ?? "—"}</td>
        <td className="px-3 py-2">{v.doctor_name ?? "—"}</td>
        <td className="px-3 py-2">{v.room_name ?? "—"}</td>
        <td className="px-3 py-2">{v.status}</td>
        <td className="px-3 py-2 max-w-xs truncate">{v.note ?? "—"}</td>
      </tr>
      {open && (
        <tr className="bg-background/60">
          <td colSpan={8} className="px-5 py-3">
            <div className="text-[10px] font-bold text-text-muted uppercase tracking-wider mb-2">
              Status Timeline
            </div>
            {timelineLoading ? (
              <p className="text-xs text-text-muted">Loading…</p>
            ) : !timeline || timeline.length === 0 ? (
              <p className="text-xs text-text-muted">No history.</p>
            ) : (
              <ol className="text-xs flex flex-col gap-1">
                {timeline.map((h) => (
                  <li key={h.id} className="flex items-center gap-3">
                    <span className="font-mono text-text-muted">
                      {new Date(h.changed_at).toLocaleString()}
                    </span>
                    <span className="font-semibold text-text-main">
                      {h.from_status ? `${h.from_status} → ` : ""}
                      <span className="text-primary">{h.to_status}</span>
                    </span>
                    {h.changed_by && (
                      <span className="text-text-light">by {h.changed_by}</span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
