/**
 * logger.js — Drop-in replacement for `console.*` that respects config.
 *
 * Usage:
 *   import log from "../utils/logger";
 *   log.info("Something happened", data);
 *   log.error("Oops", err);
 *   log.warn("Heads up");
 *   log.debug("Verbose detail");
 *
 * When ENABLE_LOGGING is false, the verbose channels (log / info / debug)
 * become no-ops. `warn` and `error` ALWAYS pass through — they carry
 * signal we don't want to silence even in production. This matches the
 * global console override in main.jsx.
 */
import { ENABLE_LOGGING } from "../config";

const noop = () => {};

const log = {
  log:   ENABLE_LOGGING ? console.log.bind(console)   : noop,
  info:  ENABLE_LOGGING ? console.info.bind(console)  : noop,
  debug: ENABLE_LOGGING ? console.debug.bind(console) : noop,
  warn:  console.warn.bind(console),
  error: console.error.bind(console),
};

export default log;
