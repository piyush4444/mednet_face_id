/**
 * Kiosk entry point (kiosk.html) — separate bundle from the admin SPA.
 *
 * No router, no MUI, no WebSocket: the kiosk is a single fullscreen
 * scan-loop screen that must boot fast on low-end hardware.
 */
import { createRoot } from "react-dom/client";
import "../index.css";
import KioskApp from "./KioskApp.jsx";
import { ENABLE_LOGGING } from "../config";

if (!ENABLE_LOGGING) {
  const noop = () => {};
  console.log = noop;
  console.info = noop;
  console.debug = noop;
}

createRoot(document.getElementById("root")).render(<KioskApp />);
