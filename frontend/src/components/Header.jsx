import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import synoraLogo from "../assets/Synora Logo.png";
import { useConnected } from "../store/connectionStore";
import { useThemeMode, toggleThemeMode } from "../store/themeStore";
import { useAuth } from "../hooks/useAuth";
import HeaderSearch from "./HeaderSearch";
import { Drawer, Box, IconButton } from "@mui/material";

// Permissions that make the Settings tab worth showing (any one suffices).
const SETTINGS_PERMS = [
  "users.write",
  "cameras.manage",
  "frontdesk_admin.manage",
  "accounts.manage_staff",
  "backup.manage",
];

// Sun / moon glyphs for the theme toggle — same outline style as the nav icons.
function ThemeToggleIcon({ mode }) {
  return mode === "dark" ? (
    <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
    </svg>
  ) : (
    <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
    </svg>
  );
}

export default function Header() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const connected = useConnected();
  const themeMode = useThemeMode();
  const location = useLocation();
  const navigate = useNavigate();
  const { authEnabled, account, hasPermission, logout } = useAuth();

  // Derive the "current page" from the route. /patients/:id still counts
  // as the Manage tab so the profile view keeps the expected highlight.
  const path = location.pathname;
  const currentPage =
    path === "/" ? "dashboard"
    : path.startsWith("/register") ? "register"
    : path.startsWith("/history") ? "history"
    : path.startsWith("/frontdesk") ? "frontdesk"
    : path.startsWith("/settings") ? "settings"
    : "";

  const tabs = [
    {
      key: "dashboard",
      route: "/",
      label: "Live View",
      perm: "tracking.read",
      icon: (
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
            d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"
          />
        </svg>
      ),
    },
    {
      key: "frontdesk",
      route: "/frontdesk",
      label: "Front Desk",
      perm: "frontdesk.operate",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M9 17v-2a2 2 0 012-2h2a2 2 0 012 2v2m-9 4h14a2 2 0 002-2V7a2 2 0 00-2-2h-3.586a1 1 0 01-.707-.293l-1.414-1.414A1 1 0 0011.586 3H8.414a1 1 0 00-.707.293L6.293 4.707A1 1 0 015.586 5H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
      ),
    },
    {
      key: "register",
      route: "/register",
      label: "Register",
      perm: "faces.enroll",
      icon: (
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
            d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"
          />
        </svg>
      ),
    },
    {
      key: "history",
      route: "/history",
      label: "History",
      perm: "history.read",
      icon: (
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
            d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
          />
        </svg>
      ),
    },
    {
      key: "settings",
      route: "/settings",
      label: "Settings",
      perm: "__settings__", // special: any of SETTINGS_PERMS (handled below)
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      ),
    },
  ];

  // Permission-filter the nav. When auth is off, hasPermission() is always
  // true, so every tab shows (demo behaviour unchanged).
  const canSee = (tab) =>
    tab.perm === "__settings__"
      ? SETTINGS_PERMS.some((p) => hasPermission(p))
      : !tab.perm || hasPermission(tab.perm);
  const visibleTabs = tabs.filter(canSee);

  return (
    <header className="glass-strong sticky top-0 z-30 border-b border-primary/10 shadow-sm">
      <div className="w-full mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* ── Brand ── */}
          <div className="shrink-0 flex items-center gap-2 sm:gap-3">
            <div className="md:hidden">
              <IconButton onClick={() => setDrawerOpen(true)} size="small" sx={{ ml: -1 }}>
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-text-main" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </IconButton>
            </div>
            <div className="w-9 h-9 rounded-xl overflow-hidden shadow-lg shadow-primary/20 bg-white flex items-center justify-center ring-1 ring-primary/10">
              <img
                src={synoraLogo}
                alt="Iris by Synora AI Labs"
                className="w-full h-full object-cover"
              />
            </div>
            <div className="flex flex-col">
              <h1 className="text-lg font-extrabold text-text-main leading-tight tracking-tight">
                <span className="text-gradient">Iris</span>
              </h1>
              <span className="text-[10px] font-medium text-text-muted leading-none -mt-0.5 hidden sm:block">
                by Synora AI Labs
              </span>
            </div>
          </div>

          {/* ── Navigation Tabs ── */}
          <nav className="hidden md:flex items-center gap-1">
            {visibleTabs.map((tab) => {
              const isActive = currentPage === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => navigate(tab.route)}
                  className={`
                    relative px-4 py-2 rounded-xl text-sm font-semibold transition-all duration-200 cursor-pointer
                    flex items-center gap-2
                    ${
                      isActive
                        ? "bg-primary/10 text-primary shadow-sm"
                        : "text-text-muted hover:text-text-main hover:bg-primary/5"
                    }
                  `}
                >
                  <span
                    className={isActive ? "text-primary" : "text-text-light"}
                  >
                    {tab.icon}
                  </span>
                  <span className="hidden sm:inline">{tab.label}</span>
                  {/* Active indicator bar */}
                  {isActive && (
                    <span className="absolute -bottom-2.25 left-3 right-3 h-[2.5px] rounded-full bg-primary animate-scale-in" />
                  )}
                </button>
              );
            })}
          </nav>

          {/* ── Right cluster: search + status ── */}
          <div className="flex items-center gap-3">
            <HeaderSearch />

            <button
              onClick={toggleThemeMode}
              title={themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              className="p-2 rounded-xl text-text-muted hover:text-primary hover:bg-primary/10 transition-all duration-200 cursor-pointer"
            >
              <ThemeToggleIcon mode={themeMode} />
            </button>

            <div className="h-6 w-px bg-primary/10" />

            <div className="flex items-center gap-2.5">
              <span className="relative flex h-2.5 w-2.5">
              {connected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-60"></span>
              )}
              <span
                className={`relative inline-flex rounded-full h-2.5 w-2.5 shadow-sm ${
                  connected
                    ? "bg-success shadow-success/50"
                    : "bg-danger shadow-danger/50"
                }`}
              ></span>
            </span>
              <span
                className={`text-xs font-semibold hidden sm:inline ${
                  connected ? "text-success" : "text-danger"
                }`}
              >
                {connected ? "Live" : "Offline"}
              </span>
            </div>

            {/* Account chip + logout — only when auth is enforced. */}
            {authEnabled && account && (
              <>
                <div className="h-6 w-px bg-primary/10" />
                <div className="hidden sm:flex flex-col items-end leading-tight">
                  <span className="text-xs font-bold text-text-main">{account.username}</span>
                  <span className="text-[10px] font-medium text-text-muted capitalize">
                    {account.role?.replace("_", " ")}
                  </span>
                </div>
                <button
                  onClick={logout}
                  title="Sign out"
                  aria-label="Sign out"
                  className="p-2 rounded-xl text-text-muted hover:text-danger hover:bg-danger/10 transition-all duration-200 cursor-pointer"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                  </svg>
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Mobile Drawer Menu ── */}
      <Drawer anchor="left" open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        <Box sx={{ width: 260, p: 2, display: "flex", flexDirection: "column", gap: 1 }}>
          <div className="flex items-center gap-3 mb-6 px-2 mt-2">
            <div className="w-8 h-8 rounded-lg overflow-hidden shadow-md bg-white flex items-center justify-center ring-1 ring-primary/10">
              <img src={synoraLogo} alt="Iris by Synora AI Labs" className="w-full h-full object-cover" />
            </div>
            <div className="flex flex-col">
              <h1 className="text-base font-extrabold text-text-main leading-tight tracking-tight">
                <span className="text-gradient">Iris</span>
              </h1>
              <span className="text-[10px] font-medium text-text-muted leading-none mt-0.5">
                by Synora AI Labs
              </span>
            </div>
          </div>
          {visibleTabs.map((tab) => {
            const isActive = currentPage === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => {
                  navigate(tab.route);
                  setDrawerOpen(false);
                }}
                className={`
                  w-full text-left px-4 py-3 rounded-xl text-sm font-semibold transition-all duration-200 cursor-pointer
                  flex items-center gap-3
                  ${
                    isActive
                      ? "bg-primary/10 text-primary shadow-sm"
                      : "text-text-muted hover:text-text-main hover:bg-primary/5"
                  }
                `}
              >
                <span className={isActive ? "text-primary" : "text-text-light"}>
                  {tab.icon}
                </span>
                <span>{tab.label}</span>
              </button>
            );
          })}
        </Box>
      </Drawer>
    </header>
  );
}
