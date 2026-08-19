import React, { lazy, Suspense, useMemo } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ToastContainer } from "react-toastify";
import { ThemeProvider } from "@mui/material/styles";
import "react-toastify/dist/ReactToastify.css";
import AppShell from "./layout/AppShell";
// Dashboard is the landing route — keep it eager so first paint is
// instant and useSocket subscribes without a chunk fetch.
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { getMuiTheme } from "./theme/muiTheme";
import { useThemeMode } from "./store/themeStore";

// Code-split the secondary routes. Each one becomes its own chunk and
// only downloads when the user navigates to it. Each admin panel is now a
// first-class route (promoted out of the old Settings tabs).
const Register        = lazy(() => import("./pages/Register"));
const Patients        = lazy(() => import("./pages/Patients"));
const PatientProfile  = lazy(() => import("./pages/PatientProfile"));
const History         = lazy(() => import("./pages/History"));
const Cameras         = lazy(() => import("./pages/Cameras"));
const FrontDesk       = lazy(() => import("./pages/FrontDesk"));
const Locations       = lazy(() => import("./pages/Locations"));
const FrontDeskAdmin  = lazy(() => import("./pages/FrontDeskAdmin"));
const AccountsAdmin   = lazy(() => import("./pages/AccountsAdmin"));
const Settings        = lazy(() => import("./pages/Settings"));

// Cheap inline fallback — avoids pulling MUI Skeleton just for this.
function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="w-10 h-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
    </div>
  );
}

// Full-screen boot spinner shown while the AuthProvider resolves session state.
function BootSplash() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-10 h-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
    </div>
  );
}

// Redirect to the dashboard if the account lacks `perm`. Nav already hides
// these entries; this backstops a hand-typed URL. No-op when auth is off.
function RequirePermission({ perm, children }) {
  const { hasPermission } = useAuth();
  return hasPermission(perm) ? children : <Navigate to="/" replace />;
}

// Gate: wait for auth to resolve, then show Login (if enforced + no session)
// or the app. MainLayout (and its WebSocket) only mounts once authed.
function AuthedApp() {
  const { ready, isAuthed } = useAuth();
  if (!ready) return <BootSplash />;
  if (!isAuthed) return <Login />;

  const Guard = RequirePermission;
  return (
    <AppShell>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/frontdesk" element={
            <Guard perm="frontdesk.operate"><FrontDesk /></Guard>} />
          <Route path="/register" element={
            <Guard perm="faces.enroll"><Register /></Guard>} />

          {/* People */}
          <Route path="/users" element={
            <Guard perm="users.read"><Patients /></Guard>} />
          <Route path="/patients/:id" element={<PatientProfile />} />
          {/* Records */}
          <Route path="/history" element={
            <Guard perm="history.read"><History /></Guard>} />

          {/* Administration */}
          <Route path="/locations" element={
            <Guard perm="locations.manage"><Locations /></Guard>} />
          <Route path="/cameras" element={
            <Guard perm="cameras.manage"><Cameras /></Guard>} />
          <Route path="/frontdesk-admin" element={
            <Guard perm="frontdesk_admin.manage"><FrontDeskAdmin /></Guard>} />

          {/* System */}
          <Route path="/accounts" element={
            <Guard perm="accounts.manage_staff"><AccountsAdmin /></Guard>} />

          {/* Back-compat: old links / bookmarks → new routes */}
          <Route path="/patients" element={<Navigate to="/users" replace />} />
          <Route path="/frontdesk/admin" element={<Navigate to="/frontdesk-admin" replace />} />
          <Route path="/settings" element={<Settings />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}

function App() {
  const mode = useThemeMode();
  const muiTheme = useMemo(() => getMuiTheme(mode), [mode]);

  return (
    <ThemeProvider theme={muiTheme}>
      <AuthProvider>
        <BrowserRouter>
          <ToastContainer
            position="bottom-right"
            autoClose={3000}
            hideProgressBar={false}
            newestOnTop
            closeOnClick
            pauseOnFocusLoss
            draggable
            pauseOnHover
            theme={mode === "dark" ? "dark" : "colored"}
            toastStyle={{ borderRadius: "12px", fontWeight: 600, fontSize: "14px" }}
          />
          <AuthedApp />
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
