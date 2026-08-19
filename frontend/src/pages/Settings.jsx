/**
 * Settings — /settings
 *
 * Centralised configuration page. Left rail lists categories; the
 * right column renders the chosen section. Adding a new section is
 * a matter of dropping a new entry into SECTIONS.
 */
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import FrontDeskConfig from "./FrontDeskAdmin";
import { useAuth } from "../hooks/useAuth";

// Cameras is heavy (WebSocket subscriber + table) — lazy-load it so the
// Settings page isn't slowed down when the user isn't on that section.
const Cameras = lazy(() => import("./Cameras"));
const Patients = lazy(() => import("./Patients"));
const Locations = lazy(() => import("./Locations"));
const AccountsAdmin = lazy(() => import("./AccountsAdmin"));

// Each section: stable key, label, optional icon, and the component to render.
const SECTIONS = [
  {
    key: "frontdesk",
    label: "Front Desk",
    description: "Departments, doctors & rooms",
    perm: "frontdesk_admin.manage",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M9 17v-2a2 2 0 012-2h2a2 2 0 012 2v2m-9 4h14a2 2 0 002-2V7a2 2 0 00-2-2h-3.586a1 1 0 01-.707-.293l-1.414-1.414A1 1 0 0011.586 3H8.414a1 1 0 00-.707.293L6.293 4.707A1 1 0 015.586 5H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
    component: FrontDeskConfig,
  },
  {
    key: "manage",
    label: "Manage Users",
    description: "Edit, update face & remove",
    perm: "users.write",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
    ),
    component: Patients,
  },
  {
    key: "locations",
    label: "Locations",
    description: "Floors, rooms & gates",
    perm: "locations.manage",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
    component: Locations,
  },
  {
    key: "cameras",
    label: "Cameras",
    description: "IP & system camera setup",
    perm: "cameras.manage",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
      </svg>
    ),
    component: Cameras,
  },
  {
    key: "accounts",
    label: "Accounts",
    description: "Logins, roles & permissions",
    perm: "accounts.manage_staff",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 11c0 1.657-1.343 3-3 3s-3-1.343-3-3 1.343-3 3-3 3 1.343 3 3zM3 20a6 6 0 0112 0M17 8v6m3-3h-6" />
      </svg>
    ),
    component: AccountsAdmin,
  },
];

export default function Settings() {
  const location = useLocation();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();

  // Only show sections the account may use. When auth is off, hasPermission()
  // is always true so every section renders (demo behaviour unchanged).
  const sections = useMemo(
    () => SECTIONS.filter((s) => !s.perm || hasPermission(s.perm)),
    [hasPermission],
  );

  // Allow ?section=frontdesk to deep-link. Falls back to the first section
  // the account can actually see.
  const initialKey = (() => {
    const params = new URLSearchParams(location.search);
    const wanted = params.get("section");
    return sections.find((s) => s.key === wanted)?.key || sections[0]?.key || "";
  })();
  const [active, setActive] = useState(initialKey);

  // Keep URL in sync so refresh / bookmark works.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("section") !== active) {
      params.set("section", active);
      navigate({ pathname: "/settings", search: `?${params}` }, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const Section = sections.find((s) => s.key === active)?.component;

  return (
    <div className="max-w-7xl mx-auto w-full pt-2">
      <div className="flex items-center justify-between mb-4 px-1">
        <h1 className="text-xl font-extrabold text-text-main">Settings</h1>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-5">
        {/* Sidebar */}
        <aside className="bg-card rounded-2xl shadow-md border border-primary/8 p-3 h-fit">
          <nav className="flex flex-col gap-1.5">
            {sections.map((s) => {
              const isActive = s.key === active;
              return (
                <button
                  key={s.key}
                  onClick={() => setActive(s.key)}
                  className={`text-left px-3.5 py-3 rounded-xl transition-all flex items-start gap-3 ${
                    isActive
                      ? "bg-primary/10 text-primary"
                      : "text-text-main hover:bg-primary/5"
                  }`}
                >
                  <span className={`mt-0.5 ${isActive ? "text-primary" : "text-text-light"}`}>
                    {s.icon}
                  </span>
                  <span className="flex flex-col">
                    <span className="text-[15px] font-bold leading-tight">{s.label}</span>
                    {s.description && (
                      <span className={`text-[11px] leading-snug mt-1 ${
                        isActive ? "text-primary/70" : "text-text-muted"
                      }`}>
                        {s.description}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </nav>
        </aside>

        {/* Section content */}
        <section className="min-w-0">
          {Section ? (
            <Suspense
              fallback={
                <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-8 flex items-center justify-center">
                  <div className="w-8 h-8 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
                </div>
              }
            >
              <Section />
            </Suspense>
          ) : null}
        </section>
      </div>
    </div>
  );
}
