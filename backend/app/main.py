"""
main.py — FastAPI application entry point.

Initializes the app, wires routers, and manages startup/shutdown lifecycle.

Run:
    uvicorn backend.app.main:app --reload          # Development
    uvicorn backend.app.main:app --host 0.0.0.0    # Production
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Add project root to sys.path so 'backend.*' imports work from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging
from backend.app.api.router import router


logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan — runs startup and shutdown logic.

    Startup:
        1. Configure logging
        2. Initialize PostgreSQL schema (create tables if missing)
        3. Initialize the pipeline singleton (detector + recognizer + FAISS)
        4. Mirror the singletons onto ``app.state`` for legacy callers

    Shutdown:
        1. Clear ``app.state`` handles (the singletons live in
           ``pipeline_service`` and die with the process)
    """
    # ── Startup ──────────────────────────────────────────────────────────
    setup_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.VERSION)

    # ── Security config sanity checks ──
    # These misconfigurations silently break auth or expose the service, so
    # surface them loudly at boot rather than as confusing runtime failures.
    if settings.AUTH_ENABLED:
        if "*" in settings.ALLOWED_ORIGINS:
            logger.error(
                "AUTH_ENABLED=true but ALLOWED_ORIGINS contains '*'. Browsers "
                "reject credentialed (cookie) requests to a wildcard origin — "
                "set ALLOWED_ORIGINS to the exact public origin(s)."
            )
        if not settings.SESSION_SECRET and not settings.DEBUG:
            logger.error(
                "AUTH_ENABLED=true but SESSION_SECRET is empty. Session cookies "
                "cannot be signed securely — set SESSION_SECRET in the environment."
            )
        if not settings.SESSION_COOKIE_SECURE:
            logger.warning(
                "AUTH_ENABLED=true with SESSION_COOKIE_SECURE=false — set it true "
                "in production so the session cookie is HTTPS-only."
            )

    # ── Relational schema bootstrap ──
    # Creates any missing tables registered on Base.metadata. Idempotent,
    # safe to run on every boot. For production migrations, use Alembic.
    from backend.app.db.postgres import init_db

    try:
        init_db()
        logger.info("PostgreSQL schema initialized")
    except Exception as exc:
        # Don't let a missing / unreachable database crash the API on
        # boot — log loudly and continue. Routes that need the relational
        # store will fail individually, which is easier to diagnose.
        logger.error("Failed to initialize PostgreSQL schema: %s", exc)
    else:
        # One-time compatibility import. A durable data_migrations marker
        # prevents re-import even if operators later delete every camera;
        # PostgreSQL remains the sole runtime source of truth.
        from backend.camera.store import seed_from_legacy_json
        try:
            imported_cameras = seed_from_legacy_json()
            if imported_cameras:
                logger.info("Imported %d camera(s) into PostgreSQL", imported_cameras)
        except Exception as exc:
            logger.error("Legacy camera import failed: %s", exc)

    # ── Heavy model loading (ONCE, via the singleton) ──
    # ``pipeline_service`` owns the detector, recognizer, and FAISS index
    # for the lifetime of this process. Every route and background job
    # reaches them through ``get_*`` accessors, so there is exactly one
    # copy in memory. Do NOT instantiate ``FaceDatabase`` or
    # ``_load_recognizer`` anywhere else — doing so creates a second,
    # divergent copy and writes will silently fail to propagate.
    from backend.app.services.pipeline_service import (
        init_pipeline,
        get_face_db,
        get_recognizer,
        reconcile_face_db_with_postgres,
    )

    init_pipeline()

    # Drop FAISS keys for users that no longer exist in Postgres. Cheap
    # one-shot scan that prevents the "FAISS hit but no Postgres row"
    # warning storm if the two stores ever drift.
    try:
        reconcile_face_db_with_postgres()
    except Exception as exc:
        logger.error("FAISS reconciliation failed: %s", exc)

    # Mirror onto app.state so legacy callers that still do
    # ``request.app.state.db`` / ``request.app.state.recognizer`` keep
    # working. These are aliases to the same singleton objects — not
    # fresh copies.
    app.state.db = get_face_db()
    app.state.recognizer = get_recognizer()
    logger.info(
        "Pipeline ready — FAISS size=%d, recognizer loaded",
        app.state.db.size,
    )

    # ── Camera stack + WS bridge ──
    # Spawns camera/AI/event processor child processes so their ws_queue
    # lives in THIS process's memory — only then can the bridge reach it.
    # Opt in with START_CAMERA_SYSTEM=1 (off by default so `uvicorn`
    # reloads don't fork-bomb).
    camera_procs = []
    event_listener_task = None
    if os.environ.get("START_CAMERA_SYSTEM") == "1":
        from backend.camera.manager import start_camera_system
        from backend.app.services.event_bridge import (
            set_ws_queue, start_event_listener,
        )

        try:
            camera_procs, ws_queue = start_camera_system(
                test_mode=os.environ.get("CAMERA_TEST_MODE") == "1"
            )
            set_ws_queue(ws_queue)
            event_listener_task = asyncio.create_task(start_event_listener())
            logger.info("Camera system + WS bridge started (%d procs)",
                        len(camera_procs))
        except Exception as exc:
            logger.error("Failed to start camera system: %s", exc)

    # ── Outbound export worker (client HIS integration) ──
    # Drains the punch + pre-registration queues to the B2B partner's API.
    # Dormant unless a CLIENT_*_API_URL is configured — safe to always call.
    try:
        from backend.app.services.export_worker import start_export_worker
        start_export_worker()
    except Exception as exc:
        logger.error("Failed to start export worker: %s", exc)

    logger.info("%s ready — accepting requests", settings.APP_NAME)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("Shutting down %s", settings.APP_NAME)

    # Stop the export worker so uvicorn isn't left waiting on its thread.
    try:
        from backend.app.services.export_worker import stop_export_worker
        stop_export_worker()
    except Exception as exc:
        logger.debug("Export worker shutdown error: %s", exc)

    # Flush any pending FAISS save so we don't lose in-memory registrations
    # that were sitting in the debounce window.
    try:
        from backend.app.services.pipeline_service import flush_save
        flush_save()
    except Exception as exc:
        logger.debug("FAISS flush on shutdown failed: %s", exc)

    # Cancel the WS bridge task so uvicorn isn't left waiting on it.
    if event_listener_task is not None and not event_listener_task.done():
        event_listener_task.cancel()
        try:
            await asyncio.wait_for(event_listener_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            logger.debug("Event listener shutdown error: %s", exc)

    # Stop registry-owned camera workers first. The registry handles
    # MJPEG cancellation, terminate/kill escalation, and shared-dict
    # cleanup; ``camera_procs`` only contains the non-camera siblings
    # (event processor, AI workers, test simulator).
    try:
        from backend.camera.registry import get_registry
        get_registry().stop_all()
    except Exception as exc:
        logger.debug("registry stop_all error: %s", exc)

    for p in camera_procs:
        if p.is_alive():
            try:
                p.terminate()
            except Exception:
                pass
    for p in camera_procs:
        try:
            p.join(timeout=3)
        except Exception:
            pass
        try:
            if p.is_alive():
                p.kill()
        except Exception:
            pass

    # Release the MJPEG encode thread pool so uvicorn doesn't hang on
    # idle threads during shutdown.
    try:
        from backend.app.api.routes.stream import shutdown_encode_pool
        shutdown_encode_pool(wait=False)
    except Exception as exc:
        logger.debug("MJPEG encode pool shutdown error: %s", exc)

    # Unlink the SharedMemory frame pool now that camera/AI workers have
    # been joined — otherwise the OS keeps the named segments around.
    try:
        from backend.camera.manager import shutdown_frame_pool
        shutdown_frame_pool()
    except Exception as exc:
        logger.debug("Frame pool shutdown error: %s", exc)

    app.state.db = None
    app.state.recognizer = None
    logger.info("Shutdown complete")


# Interactive API docs expose the full endpoint map, so they are only left
# open when auth is not enforcing (or in DEBUG). When AUTH_ENABLED is on we
# disable the built-in routes and re-serve them behind a metrics.read guard
# below, so only super_admins (who hold every permission) can reach them.
_docs_public = settings.DEBUG or not settings.AUTH_ENABLED

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _docs_public else None,
    redoc_url="/redoc" if _docs_public else None,
    openapi_url="/openapi.json" if _docs_public else None,
)

if not _docs_public:
    from fastapi import Depends
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
    from fastapi.openapi.utils import get_openapi

    from backend.app.core.deps import require_permission
    from backend.app.db.auth_models import Permission

    _docs_guard = Depends(require_permission(Permission.METRICS_READ))

    @app.get("/openapi.json", include_in_schema=False)
    async def _protected_openapi(_=_docs_guard):
        return get_openapi(
            title=settings.APP_NAME, version=settings.VERSION, routes=app.routes
        )

    @app.get("/docs", include_in_schema=False)
    async def _protected_docs(_=_docs_guard):
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title=f"{settings.APP_NAME} — docs"
        )

    @app.get("/redoc", include_in_schema=False)
    async def _protected_redoc(_=_docs_guard):
        return get_redoc_html(
            openapi_url="/openapi.json", title=f"{settings.APP_NAME} — docs"
        )

# ── CORS ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────────────────
app.include_router(router)

# ── Media (profile photos) ───────────────────────────────────────────────
# Local storage served by this API until a dedicated media server exists;
# then MEDIA_BASE_URL redirects resolved URLs there and this mount goes
# unused. StaticFiles refuses path traversal outside the directory.
# NOTE: like every route today this is unauthenticated — the
# feat/auth-rbac merge must gate /media with the other PII surfaces.
from fastapi.staticfiles import StaticFiles

from backend.app.services.media_service import media_root

app.mount("/media", StaticFiles(directory=str(media_root())), name="media")


@app.get("/")
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    # When run directly (e.g. `python main.py` inside the app folder)
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
