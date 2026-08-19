/**
 * Sidebar — collapsible, grouped, RBAC-filtered admin navigation.
 *
 * Renders from navConfig (data-driven); items the account lacks permission
 * for are hidden. Active route is highlighted via useLocation. Collapse state
 * (240px ↔ 72px) persists in localStorage; collapsed shows icon-only with a
 * hover tooltip. A quick filter box narrows the list.
 */
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import synoraLogo from "../assets/Synora Logo.png";
import { useAuth } from "../hooks/useAuth";
import { NAV_GROUPS } from "./navConfig";

const COLLAPSE_KEY = "iris-sidebar-collapsed";

// icon string (navConfig) → SVG. Outline style, 20px, currentColor.
const I = (d) => (
  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    {d}
  </svg>
);
const P = (props) => <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} {...props} />;

const ICONS = {
  dashboard: I(<P d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />),
  frontdesk: I(<P d="M9 17v-2a2 2 0 012-2h2a2 2 0 012 2v2m-9 4h14a2 2 0 002-2V7a2 2 0 00-2-2h-3.586a1 1 0 01-.707-.293l-1.414-1.414A1 1 0 0011.586 3H8.414a1 1 0 00-.707.293L6.293 4.707A1 1 0 015.586 5H5a2 2 0 00-2 2v12a2 2 0 002 2z" />),
  register: I(<P d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />),
  users: I(<P d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />),
  history: I(<P d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />),
  facility: I(<P d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />),
  location: I(<><P d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" /><P d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" /></>),
  camera: I(<P d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />),
  departments: I(<P d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />),
  accounts: I(<P d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />),
};

export default function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  const [q, setQ] = useState("");

  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };

  const isActive = (item) =>
    item.exact ? location.pathname === item.path
      : location.pathname === item.path || location.pathname.startsWith(item.path + "/");

  // RBAC-filter, then text-filter.
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return NAV_GROUPS
      .map((g) => ({
        ...g,
        items: g.items.filter(
          (i) =>
            (!i.perm || hasPermission(i.perm)) &&
            (!needle || i.label.toLowerCase().includes(needle)),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [q, hasPermission]);

  return (
    <aside
      className={`shrink-0 h-screen sticky top-0 bg-card border-r border-primary/10 flex flex-col transition-all duration-200 ${
        collapsed ? "w-18" : "w-62"
      }`}
    >
      {/* Brand + collapse */}
      <div className="h-16 flex items-center gap-2.5 px-3 border-b border-primary/10">
        <div className="w-9 h-9 shrink-0 rounded-xl overflow-hidden shadow-lg shadow-primary/20 bg-white flex items-center justify-center ring-1 ring-primary/10">
          <img src={synoraLogo} alt="Iris" className="w-full h-full object-cover" />
        </div>
        {!collapsed && (
          <div className="flex flex-col leading-tight min-w-0">
            <span className="text-lg font-extrabold text-gradient">Iris</span>
            <span className="text-[10px] font-medium text-text-muted -mt-0.5 truncate">
              by Synora AI Labs
            </span>
          </div>
        )}
        <button
          onClick={toggle}
          title={collapsed ? "Expand" : "Collapse"}
          className="ml-auto p-1.5 rounded-lg text-text-muted hover:text-primary hover:bg-primary/10"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d={collapsed ? "M13 5l7 7-7 7M5 5l7 7-7 7" : "M11 19l-7-7 7-7M19 19l-7-7 7-7"} />
          </svg>
        </button>
      </div>

      {/* Search */}
      {!collapsed && (
        <div className="px-3 pt-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search module…"
            className="w-full bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-2 text-sm font-semibold outline-none"
          />
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 flex flex-col gap-4">
        {groups.map((g) => (
          <div key={g.label} className="flex flex-col gap-0.5">
            {!collapsed && (
              <span className="px-3 pb-1 text-[10px] font-bold uppercase tracking-wider text-text-light">
                {g.label}
              </span>
            )}
            {g.items.map((item) => {
              const active = isActive(item);
              return (
                <button
                  key={item.key}
                  onClick={() => navigate(item.path)}
                  title={collapsed ? item.label : undefined}
                  className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition-all ${
                    active
                      ? "bg-primary/10 text-primary"
                      : "text-text-muted hover:text-text-main hover:bg-primary/5"
                  } ${collapsed ? "justify-center" : ""}`}
                >
                  <span className={active ? "text-primary" : "text-text-light"}>
                    {ICONS[item.icon] || ICONS.dashboard}
                  </span>
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
