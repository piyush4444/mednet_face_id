/**
 * Topbar — context bar above the content area.
 *
 * Left: page breadcrumb (derived from the route). Right: facility switcher
 * (the "Cost Center" analog — hidden when the account can't list facilities),
 * refresh, fullscreen, live-connection dot, theme toggle, and the user menu
 * (username, role, logout).
 */
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useFacility } from "../hooks/useFacility";
import { useConnected } from "../store/connectionStore";
import { useThemeMode, toggleThemeMode } from "../store/themeStore";
import { NAV_BY_PATH } from "./navConfig";

function crumbFor(pathname) {
  if (pathname === "/") return "Live View";
  if (NAV_BY_PATH[pathname]) return NAV_BY_PATH[pathname];
  // /patients/:id and other nested — title-case the first segment.
  const seg = pathname.split("/").filter(Boolean)[0] || "";
  return seg.charAt(0).toUpperCase() + seg.slice(1).replace(/-/g, " ");
}

export default function Topbar() {
  const location = useLocation();
  const connected = useConnected();
  const themeMode = useThemeMode();
  const { account, role, logout, authEnabled } = useAuth();
  const { facilities, facilityId, setFacilityId } = useFacility();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.();
  };

  return (
    <header className="h-16 shrink-0 sticky top-0 z-20 glass-strong border-b border-primary/10 flex items-center gap-3 px-4 sm:px-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-bold text-text-main truncate">
          {crumbFor(location.pathname)}
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {/* Facility switcher (Cost Center analog) */}
        {facilities.length > 0 && (
          <div className="hidden sm:flex items-center gap-2">
            <span className="text-xs font-semibold text-text-muted">Facility</span>
            <select
              value={facilityId ?? ""}
              onChange={(e) => setFacilityId(e.target.value)}
              className="bg-background border border-primary/15 focus:border-primary rounded-lg px-3 py-1.5 text-sm font-bold outline-none max-w-[180px]"
            >
              {facilities.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>
          </div>
        )}

        <button onClick={() => window.location.reload()} title="Refresh"
          className="p-2 rounded-xl text-text-muted hover:text-primary hover:bg-primary/10">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>

        <button onClick={toggleFullscreen} title="Fullscreen"
          className="hidden sm:inline-flex p-2 rounded-xl text-text-muted hover:text-primary hover:bg-primary/10">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
          </svg>
        </button>

        <button onClick={toggleThemeMode} title="Toggle theme"
          className="p-2 rounded-xl text-text-muted hover:text-primary hover:bg-primary/10">
          {themeMode === "dark" ? (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            </svg>
          )}
        </button>

        {/* Live status */}
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            {connected && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-60" />}
            <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${connected ? "bg-success" : "bg-danger"}`} />
          </span>
          <span className={`text-xs font-semibold hidden md:inline ${connected ? "text-success" : "text-danger"}`}>
            {connected ? "Live" : "Offline"}
          </span>
        </div>

        <div className="h-6 w-px bg-primary/10" />

        {/* User menu */}
        <div className="relative" ref={menuRef}>
          <button onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-xl hover:bg-primary/5">
            <span className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-sm">
              {(account?.username || "U").charAt(0).toUpperCase()}
            </span>
            <span className="hidden sm:flex flex-col items-start leading-tight">
              <span className="text-sm font-bold text-text-main">{account?.username || "User"}</span>
              {role && <span className="text-[10px] font-semibold text-text-muted capitalize">{role.replace(/_/g, " ")}</span>}
            </span>
          </button>
          {menuOpen && (
            <div className="absolute right-0 mt-2 w-48 bg-card border border-primary/10 rounded-xl shadow-lg py-1 z-30">
              <div className="px-4 py-2 border-b border-primary/8">
                <p className="text-sm font-bold text-text-main truncate">{account?.username || "User"}</p>
                <p className="text-[11px] font-semibold text-text-muted capitalize">{role?.replace(/_/g, " ") || "—"}</p>
              </div>
              {authEnabled ? (
                <button onClick={() => { setMenuOpen(false); logout?.(); }}
                  className="w-full text-left px-4 py-2.5 text-sm font-semibold text-danger hover:bg-danger/5">
                  Sign out
                </button>
              ) : (
                <p className="px-4 py-2.5 text-xs text-text-muted">Auth disabled (demo mode)</p>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
