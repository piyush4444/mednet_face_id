/**
 * config.js — Central frontend configuration.
 *
 * Toggle flags here to control runtime behaviour across the app.
 * Import individual settings where needed:
 *
 *   import { ENABLE_LOGGING } from "../config";
 */

/**
 * Master switch for verbose console output (log / info / debug).
 *
 * `warn` and `error` ALWAYS pass through — they carry signal that should
 * not be silenced in production. Set this to `false` to quiet down the
 * devtools console in staging/prod builds.
 *
 * Can also be overridden per-build via Vite env:
 *   VITE_ENABLE_LOGGING=true  → forces on
 *   VITE_ENABLE_LOGGING=false → forces off
 * If unset, falls back to the inline default below.
 */
const ENV_FLAG = import.meta.env.VITE_ENABLE_LOGGING;
export const ENABLE_LOGGING =
  ENV_FLAG === undefined
    ? true // default for dev — flip this one value to silence prod builds
    : ENV_FLAG === "true" || ENV_FLAG === true;

export const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000/api/v1";
