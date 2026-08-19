"""
run_frontend.py — One-shot launcher for the Synora React (Vite) frontend.

What it does
------------
1. ``cd``s into ``frontend/``.
2. Runs ``npm install`` only when ``node_modules`` is missing or stale,
   so subsequent boots are instant.
3. Invokes the appropriate npm script: ``dev`` (default), ``build``,
   ``preview``, or ``lint``.

Usage
-----
    python run_frontend.py                 # vite dev server (default)
    python run_frontend.py --mode build    # production build → frontend/dist
    python run_frontend.py --mode preview  # serve the production build
    python run_frontend.py --mode lint     # ESLint over the codebase
    python run_frontend.py --port 5174     # override the dev server port
    python run_frontend.py --api-url http://localhost:8000/api/v1

The ``--api-url`` flag exports ``VITE_API_URL`` for the child process,
which the frontend reads in ``frontend/src/config.js``. Use it when
your backend isn't on the default ``http://127.0.0.1:8000`` (for
example, when developing against a remote dev box).

See ``docs/CONFIGURATION.md`` for the full backend tuning reference.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

VALID_MODES = ("dev", "build", "preview", "lint")


def _resolve_npm() -> str:
    """Find npm across platforms.

    On Windows, npm is usually ``npm.cmd``. ``shutil.which`` handles
    PATHEXT so this works on bash/PowerShell/cmd alike.
    """
    for candidate in ("npm", "npm.cmd"):
        path = shutil.which(candidate)
        if path:
            return path
    print(
        "[run_frontend] ERROR: npm was not found on PATH. Install Node.js "
        "(https://nodejs.org) and re-run.",
        file=sys.stderr,
    )
    sys.exit(1)


def _ensure_dependencies(npm: str) -> None:
    """Run ``npm install`` only when needed.

    A bare ``node_modules`` directory is the cheapest sentinel — it's
    created by every successful install. Doesn't catch the case where
    package.json was edited but node_modules wasn't refreshed; for that,
    run ``npm install`` manually inside ``frontend/``.
    """
    if (FRONTEND_DIR / "node_modules").is_dir():
        return
    print("[run_frontend] node_modules missing — running npm install (one-time)")
    subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=True)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_frontend",
        description="Start / build / preview / lint the Synora React frontend.",
    )
    p.add_argument("--mode", choices=VALID_MODES, default="dev",
                   help="Which npm script to run (default: dev)")
    p.add_argument("--port", type=int, default=None,
                   help="Override the dev server port (Vite default: 5173)")
    p.add_argument("--host", default=None,
                   help="Override the dev server host (default: localhost)")
    p.add_argument("--api-url", default=None,
                   help="Sets VITE_API_URL for the child process — point the "
                        "frontend at a non-default backend (e.g. a remote dev box)")
    return p.parse_args()


def main() -> int:
    if not FRONTEND_DIR.is_dir():
        print(f"[run_frontend] ERROR: {FRONTEND_DIR} not found.", file=sys.stderr)
        return 1

    args = _parse_args()
    npm = _resolve_npm()
    _ensure_dependencies(npm)

    env = os.environ.copy()
    if args.api_url:
        env["VITE_API_URL"] = args.api_url

    cmd: list[str] = [npm, "run", args.mode]
    # Pass-through args after `--` so npm forwards them to vite. Only the
    # dev / preview modes accept --host / --port.
    extra: list[str] = []
    if args.mode in ("dev", "preview"):
        if args.host:
            extra += ["--host", args.host]
        if args.port is not None:
            extra += ["--port", str(args.port)]
    if extra:
        cmd += ["--", *extra]

    # Hand off — replace this process with npm so Ctrl-C goes straight to
    # vite without the Python wrapper sitting in the middle. On Windows
    # exec is emulated via subprocess; we accept that and propagate the
    # exit code.
    try:
        completed = subprocess.run(cmd, cwd=FRONTEND_DIR, env=env)
    except KeyboardInterrupt:
        return 130
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
