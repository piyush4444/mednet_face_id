import { createContext, useContext, useEffect, useRef, useState } from "react";
import { setConnected } from "../store/connectionStore";

const MAX_BACKOFF_MS = 30_000;

// Derive the WebSocket URL from the same base the HTTP API uses.
import { API_URL as _apiBase } from "../config";

// The WebSocket constructor requires an absolute ws(s):// URL, so a relative
// API base (same-origin dev proxy or prod nginx, e.g. "/api/v1") must be
// resolved against the page origin. An absolute http(s) base is converted
// scheme-wise instead.
function deriveWsUrl(apiBase) {
  if (/^https?:\/\//i.test(apiBase)) {
    return (
      apiBase
        .replace(/^http/i, "ws")        // http → ws, https → wss
        .replace(/\/api\/v1\/?$/, "") + // strip trailing /api/v1
      "/api/v1/ws/live"
    );
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const pathPrefix = apiBase.replace(/\/api\/v1\/?$/, "");
  return `${proto}//${window.location.host}${pathPrefix}/api/v1/ws/live`;
}
const _WS_URL = deriveWsUrl(_apiBase);

// WS close code the backend uses to reject an unauthorized handshake.
const WS_UNAUTHORIZED = 4401;

/* ───────────────────── Context ───────────────────── */
const SocketContext = createContext(null);

/**
 * Provider that keeps a single WebSocket connection alive regardless of which
 * page is rendered.  Mount it once near the top of the tree (e.g. MainLayout).
 *
 * Any component that needs to react to incoming WS messages can call
 * `useSocketMessages(handler)`.
 */
export function SocketProvider({ children }) {
  const socketRef = useRef(null);
  const [isConnected, setIsConnected] = useState(false);
  // A Set of listener callbacks; components subscribe/unsubscribe via useSocketMessages.
  const listenersRef = useRef(new Set());

  useEffect(() => {
    let closedByUs = false;
    let backoff = 2000;
    let retryTimer = null;

    const connect = () => {
      const ws = new WebSocket(_WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        console.log("[WS] Connected");
        backoff = 2000;
        setIsConnected(true);
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          // Fan-out: notify every registered listener
          listenersRef.current.forEach((fn) => fn(data));
        } catch (err) {
          console.error("WS parse error:", err);
        }
      };

      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* noop */
        }
      };

      ws.onclose = (event) => {
        setIsConnected(false);
        setConnected(false);
        if (closedByUs) return;
        // Auth rejection: don't hammer the endpoint reconnecting. Surface a
        // re-login instead — the AuthProvider will drop to the Login page.
        if (event?.code === WS_UNAUTHORIZED) {
          console.warn("[WS] Unauthorized (4401) — re-login required");
          window.dispatchEvent(new CustomEvent("auth:unauthorized"));
          return;
        }
        console.log(`[WS] Reconnecting in ${backoff}ms...`);
        retryTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      };
    };

    connect();

    return () => {
      closedByUs = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (socketRef.current) {
        try {
          socketRef.current.close();
        } catch {
          /* noop */
        }
      }
      setIsConnected(false);
      setConnected(false);
    };
  }, []);

  // Stable context value
  const ctx = useRef({ socketRef, isConnected, listenersRef });
  ctx.current.isConnected = isConnected;

  return (
    <SocketContext.Provider value={ctx}>
      {children}
    </SocketContext.Provider>
  );
}

/**
 * Subscribe to incoming WebSocket messages.  The `handler` callback is invoked
 * for every parsed message while the calling component is mounted.
 *
 * Usage (e.g. in Dashboard):
 *   useSocketMessages(handleWS);
 */
export function useSocketMessages(handler) {
  const ctx = useContext(SocketContext);
  const handlerRef = useRef(handler);

  // Keep ref up-to-date so we don't re-subscribe on every render
  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    if (!ctx) return;
    const wrapper = (data) => handlerRef.current?.(data);
    ctx.current.listenersRef.current.add(wrapper);
    return () => {
      ctx.current.listenersRef.current.delete(wrapper);
    };
  }, [ctx]);
}

/**
 * Backwards-compatible hook — returns `{ isConnected }`.
 * Prefer `useSocketMessages` if you need to listen to messages.
 */
export default function useSocket(onMessage) {
  const ctx = useContext(SocketContext);
  useSocketMessages(onMessage);
  return { socket: ctx?.current?.socketRef, isConnected: ctx?.current?.isConnected ?? false };
}
