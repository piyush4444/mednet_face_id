import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";
import { ENABLE_LOGGING } from "./config";

// Silence verbose console output when logging is disabled. We patch the
// three noisy levels (log / info / debug) globally at bootstrap so every
// call site — ours and third-party — goes through the gate without having
// to rewrite call sites to use a custom logger. `warn` and `error` always
// pass through: they carry signal we don't want to lose.
if (!ENABLE_LOGGING) {
  const noop = () => {};
  console.log = noop;
  console.info = noop;
  console.debug = noop;
}

// Global fetch shim. Two jobs:
//   1. Inject ngrok-skip-browser-warning so ngrok's free tier doesn't return
//      its HTML interstitial (which breaks JSON parsing/CORS).
//   2. Auth: send the session cookie on every request (credentials:'include')
//      and echo the CSRF cookie in the X-CSRF-Token header on mutating
//      methods (double-submit). On a 401 from the API, emit a global
//      "auth:unauthorized" event so the AuthProvider can bounce to login.
//
// Doing this once here means the ~80 existing fetch call sites need no
// changes and can't accidentally forget credentials. It's inert when the
// backend has AUTH_ENABLED=false (no cookie is set, header is harmless).
function readCookie(name) {
  const m = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)"),
  );
  return m ? decodeURIComponent(m[1]) : null;
}

const _origFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const headers = new Headers(
    init.headers || (input instanceof Request ? input.headers : undefined),
  );
  headers.set("ngrok-skip-browser-warning", "true");

  const method = (
    init.method || (input instanceof Request ? input.method : "GET")
  ).toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readCookie("iris_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }

  const res = await _origFetch(input, {
    ...init,
    headers,
    credentials: "include", // send the HttpOnly session cookie
  });

  if (res.status === 401) {
    const url = typeof input === "string" ? input : input?.url || "";
    // Ignore the probe endpoints the AuthProvider itself calls, so we don't
    // loop when simply checking whether the user is logged in.
    if (!/\/auth\/(me|config|login)\b/.test(url)) {
      window.dispatchEvent(new CustomEvent("auth:unauthorized"));
    }
  }
  return res;
};

// NOTE: React.StrictMode is intentionally omitted here.
//
// StrictMode double-mounts every component in dev mode (React 18+).
// CameraCard renders an MJPEG <img> tag whose src is a long-lived
// multipart HTTP stream backed by a multiprocessing.Queue(maxsize=1).
// Double-mount creates TWO concurrent consumers on that queue — each
// steals every other frame from the other, causing the video feed to
// freeze every 1-2 seconds.  Removing StrictMode gives a single
// consumer per camera and a smooth stream.
createRoot(document.getElementById("root")).render(
  <App />,
);
