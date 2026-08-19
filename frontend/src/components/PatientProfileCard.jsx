/**
 * PatientProfileCard — pure presentational component.
 *
 * Renders a patient's identity + current-presence state in a card. Does
 * NOT fetch data. Pass in a patient object already shaped by the caller.
 *
 * Used by: Patient Profile page, Dashboard, search results, history.
 */

const TYPE_BADGE = {
  PATIENT:  { bg: "bg-blue-100 dark:bg-blue-500/15",       text: "text-blue-700 dark:text-blue-300",       label: "Patient" },
  DOCTOR:   { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300", label: "Doctor" },
  EMPLOYEE: { bg: "bg-amber-100 dark:bg-amber-500/15",     text: "text-amber-700 dark:text-amber-300",     label: "Employee" },
  VISITOR:  { bg: "bg-purple-100 dark:bg-purple-500/15",   text: "text-purple-700 dark:text-purple-300",   label: "Visitor" },
  RELATIVE: { bg: "bg-pink-100 dark:bg-pink-500/15",       text: "text-pink-700 dark:text-pink-300",       label: "Relative" },
};

const formatTimestamp = (value) => {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
};

const formatValue = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  return value;
};

function StatusPill({ status }) {
  const isIn = status === "INSIDE" || status === "IN";
  const isOut = status === "OUT";
  const color = isIn
    ? "bg-green-100 text-green-700 border-green-200 dark:bg-green-500/15 dark:text-green-300 dark:border-green-500/30"
    : isOut
      ? "bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-500/15 dark:text-gray-300 dark:border-gray-500/30"
      : "bg-yellow-100 text-yellow-700 border-yellow-200 dark:bg-yellow-500/15 dark:text-yellow-300 dark:border-yellow-500/30";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold border ${color}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          isIn ? "bg-green-500" : isOut ? "bg-gray-400" : "bg-yellow-500"
        }`}
      />
      {status || "Unknown"}
    </span>
  );
}

function Field({ label, value }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-bold uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span className="text-sm font-semibold text-text-main wrap-break-word">
        {formatValue(value)}
      </span>
    </div>
  );
}

export default function PatientProfileCard({ patient, compact = false }) {
  if (!patient) return null;

  const name = patient.name || `User ${patient.id}`;
  const initial = name.charAt(0).toUpperCase();
  const userType = patient.user_type || "PATIENT";
  const badge = TYPE_BADGE[userType] || TYPE_BADGE.PATIENT;

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden animate-fade-in">
      {/* Header */}
      <div className="px-5 py-4 border-b border-primary/8 flex items-center gap-4 bg-linear-to-r from-primary/5 to-transparent">
        <div className="w-14 h-14 rounded-2xl bg-primary/10 text-primary flex items-center justify-center text-2xl font-bold shadow-sm">
          {initial}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-lg font-extrabold text-text-main truncate">
              {name}
            </h2>
            <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${badge.bg} ${badge.text}`}>
              {badge.label}
            </span>
            {patient.is_active === false && (
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-gray-200 text-gray-600 dark:bg-gray-500/20 dark:text-gray-300">
                Inactive
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[11px] font-mono text-text-muted">
              ID: {patient.id}
            </span>
            {patient.mrn && (
              <span className="text-[11px] font-mono text-text-muted">
                MRN: {patient.mrn}
              </span>
            )}
            <StatusPill status={patient.status} />
          </div>
        </div>
      </div>

      {/* Presence */}
      <div className="px-5 py-4 grid grid-cols-2 sm:grid-cols-3 gap-4 border-b border-primary/8">
        <Field label="Current Floor" value={patient.floor} />
        <Field label="Current Camera" value={patient.camera_id} />
        <Field label="Last Seen" value={formatTimestamp(patient.last_seen_at)} />
      </div>

      {/* Type-aware details (hidden in compact mode) */}
      {!compact && (
        <div className="px-5 py-4 grid grid-cols-2 sm:grid-cols-3 gap-4">
          <Field label="Contact" value={patient.contact_number} />

          {userType === "PATIENT" && (
            <>
              <Field label="Age" value={patient.age} />
              <Field label="Gender" value={patient.gender} />
              <Field label="Date of Birth" value={patient.dob} />
              <Field label="Department" value={patient.department} />
              <Field label="Doctor" value={patient.doctor} />
              <Field label="Category" value={patient.category} />
              <div className="col-span-2 sm:col-span-3">
                <Field label="Address" value={patient.address} />
              </div>
            </>
          )}

          {userType === "DOCTOR" && (
            <>
              <Field label="Specialty" value={patient.specialty} />
              <Field label="OPD Dept ID" value={patient.opd_department_id} />
              <Field label="OPD Room ID" value={patient.opd_room_id} />
              <Field label="Department" value={patient.department} />
            </>
          )}

          {userType === "EMPLOYEE" && (
            <>
              <Field label="Role" value={patient.role} />
              <Field label="Staff Department" value={patient.staff_department} />
            </>
          )}

          {userType === "VISITOR" && (
            <div className="col-span-2 sm:col-span-3">
              <Field label="Purpose" value={patient.purpose} />
            </div>
          )}

          {userType === "RELATIVE" && (
            <div className="col-span-2 sm:col-span-3 text-xs text-text-muted">
              See the Relations section below for linked patients.
            </div>
          )}

          {patient.note && (
            <div className="col-span-2 sm:col-span-3">
              <Field label="Note" value={patient.note} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
