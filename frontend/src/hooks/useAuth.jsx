/**
 * useAuth.jsx — app-wide authentication state.
 *
 * Boot sequence:
 *   1. GET /auth/config → is enforcement on?
 *   2. If off: mark ready; the app renders normally (no login gate) and
 *      hasPermission() returns true for everything — the demo build is
 *      completely unaffected.
 *   3. If on: GET /auth/me. 200 → logged in (store account + permissions);
 *      401 → not logged in (App shows the Login page).
 *
 * A global "auth:unauthorized" event (emitted by the fetch shim on any API
 * 401) clears the session so the user is bounced back to login when their
 * cookie expires or is revoked mid-session.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import * as authApi from "../api/auth";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [ready, setReady] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [account, setAccount] = useState(null); // { id, username, role, permissions }

  const loadMe = useCallback(async () => {
    try {
      const me = await authApi.getMe();
      setAccount(me);
      return me;
    } catch {
      setAccount(null);
      return null;
    }
  }, []);

  // Initial boot.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let enabled = false;
      try {
        const cfg = await authApi.getAuthConfig();
        enabled = !!cfg?.auth_enabled;
      } catch {
        enabled = false; // if we can't reach config, don't lock the user out
      }
      if (cancelled) return;
      setAuthEnabled(enabled);
      if (enabled) await loadMe();
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  // React to mid-session expiry/revocation.
  useEffect(() => {
    const onUnauthorized = () => {
      if (authEnabled) setAccount(null);
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, [authEnabled]);

  const login = useCallback(async (username, password) => {
    const me = await authApi.login(username, password);
    setAccount(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setAccount(null);
    }
  }, []);

  const permissions = useMemo(
    () => new Set(account?.permissions || []),
    [account],
  );

  // When auth is off, everything is permitted (UI mirrors the open backend).
  const hasPermission = useCallback(
    (perm) => !authEnabled || permissions.has(perm),
    [authEnabled, permissions],
  );

  const value = useMemo(
    () => ({
      ready,
      authEnabled,
      account,
      role: account?.role || null,
      permissions,
      hasPermission,
      login,
      logout,
      reloadMe: loadMe,
      // True when the app should render its normal content.
      isAuthed: !authEnabled || !!account,
    }),
    [ready, authEnabled, account, permissions, hasPermission, login, logout, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Provider + hook share this file by design (matches useSocket.jsx). The
// hook export trips react-refresh's components-only rule, which is a dev-HMR
// nicety, not a correctness issue here.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
