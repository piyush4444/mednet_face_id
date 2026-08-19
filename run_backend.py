"""
run_backend.py — One-shot launcher for the Synora FastAPI backend.

What it does
------------
1. Adds the project root to ``sys.path`` so ``backend.*`` imports work
   regardless of where Python is invoked from.
2. Sets the env vars the camera stack expects, with sensible defaults
   that you can override by exporting them yourself before running.
3. Boots uvicorn with the same configuration the deploy uses.

Usage
-----
    python run_backend.py
    python run_backend.py --no-reload        # disable autoreload
    python run_backend.py --port 8080
    python run_backend.py --no-cameras       # boot WITHOUT spawning workers
    python run_backend.py --debug-log        # verbose logging (SYNORA_DEBUG_LOG=true)
    python run_backend.py --test-mode        # synthetic camera simulator instead of real feeds

Anything not understood is forwarded to ``uvicorn.run`` via env vars
where applicable; for fully custom uvicorn flags, run uvicorn directly:

    set START_CAMERA_SYSTEM=1
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

See ``docs/CONFIGURATION.md`` for the full backend tuning reference.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_backend",
        description="Start the Synora FastAPI backend with the camera stack.",
    )
    p.add_argument("--host", default="0.0.0.0",
                   help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8000,
                   help="Bind port (default: 8000)")
    p.add_argument("--no-reload", dest="reload", action="store_false",
                   help="Disable uvicorn autoreload (recommended for production)")
    p.add_argument("--no-cameras", dest="cameras", action="store_false",
                   help="Boot the API without starting camera/AI workers — "
                        "useful for schema-only or admin-only sessions")
    p.add_argument("--debug-log", action="store_true",
                   help="Equivalent to SYNORA_DEBUG_LOG=true (verbose stdout)")
    p.add_argument("--test-mode", action="store_true",
                   help="Use the synthetic test simulator instead of real cameras")
    p.add_argument("--workers", type=int, default=1,
                   help="uvicorn worker count. Leave at 1 — the camera stack "
                        "spawns multiprocessing children that aren't safe to "
                        "duplicate across uvicorn workers.")
    p.set_defaults(reload=True, cameras=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # Make `backend.*` importable from anywhere.
    sys.path.insert(0, str(PROJECT_ROOT))

    # Camera stack opt-in. Keep the env var pattern so the lifespan in
    # backend/app/main.py keeps working unchanged.
    os.environ.setdefault(
        "START_CAMERA_SYSTEM", "1" if args.cameras else "0",
    )

    # RTSP capture tuning. Three knobs:
    #   rtsp_transport;udp     — UDP wins on Tailscale / WireGuard tunnels
    #                            because TCP head-of-line blocking causes
    #                            "stale frame age=2s" symptoms when the
    #                            tunnel jitters. For straight LAN with
    #                            no tunnel, switch to ;tcp for cleaner
    #                            decoding under packet loss.
    #   stimeout;5000000       — 5 s socket timeout (microseconds).
    #   fflags;nobuffer        — don't pre-buffer frames; deliver each
    #                            packet to the decoder immediately.
    #   flags;low_delay        — H.264 decoder skips reorder buffer.
    #   max_delay;500000       — 0.5 s ceiling on demuxer reorder window.
    # Override per-host by exporting OPENCV_FFMPEG_CAPTURE_OPTIONS yourself.
    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "rtsp_transport;udp|stimeout;5000000|fflags;nobuffer|flags;low_delay|max_delay;500000",
    )
    if args.test_mode:
        os.environ["CAMERA_TEST_MODE"] = "1"
    if args.debug_log:
        os.environ["SYNORA_DEBUG_LOG"] = "true"

    if args.workers > 1:
        # Surface the footgun loudly rather than silently duplicating
        # camera workers across N uvicorn replicas.
        print(
            "[run_backend] WARNING: --workers > 1 will spawn the camera "
            "stack once per uvicorn worker. This is almost certainly not "
            "what you want. Use a single uvicorn worker; scale AI workers "
            "via NUM_AI_WORKERS in backend/config.py instead.",
            file=sys.stderr,
        )

    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
