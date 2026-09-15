"""
router.py — Top-level API router.

Aggregates all route modules under /api/v1.

RBAC lives here: each router is mounted with a ``require_permission(...)``
dependency so this file is the single, readable map of the whole permission
surface. Guards are **no-ops until ``settings.AUTH_ENABLED`` is True** (see
``core/deps.require_permission``), so mounting them does not change the
demo-stable behaviour on ``main`` until the login UI ships.

Mixed routers (``users``) carry a router-level *read* guard here plus
stricter per-route *write* guards inside the route module. Read-only routers
(``patients``, ``tracking``, ``history``) need only the router-level guard.
WebSocket auth (``ws``) can't use an HTTP dependency and is handled at the
handshake in Phase 3.
"""

from fastapi import APIRouter, Depends

from backend.app.api.routes import (
    accounts, audit, auth, health, recognize, register, users, patients,
    tracking, ws, stream, history, metrics, metrics_clients, cameras,
    frontdesk, frontdesk_admin, facility, locations, integrations,
    attendance,
)
from backend.app.core.deps import require_permission, verify_csrf
from backend.app.db.auth_models import Permission as P

router = APIRouter(prefix="/api/v1")


def _guard(perm: P):
    """Router-level guard: the permission + a CSRF check.

    ``verify_csrf`` is a no-op on safe methods (GET/HEAD/OPTIONS) and while
    ``AUTH_ENABLED`` is False, so read-only routers are unaffected. Login is
    exempt because ``auth.router`` is mounted without a guard.
    """
    return [Depends(require_permission(perm)), Depends(verify_csrf)]


# ── Unguarded: auth + health ─────────────────────────────────────────────
# Login is how you become authenticated, so /auth/* stays open. Account
# management (/auth/accounts) is guarded inside its own module. /health is a
# public liveness probe.
router.include_router(auth.router)
router.include_router(accounts.router)
router.include_router(audit.router, dependencies=_guard(P.AUDIT_READ))
router.include_router(health.router)

# ── Face enrolment / recognition ─────────────────────────────────────────
router.include_router(register.router, dependencies=_guard(P.FACES_ENROLL))
router.include_router(recognize.router, dependencies=_guard(P.FRONTDESK_OPERATE))

# ── Subjects (unified users) — mixed: read here, writes in the module ─────
router.include_router(users.router, dependencies=_guard(P.USERS_READ))
router.include_router(patients.router, dependencies=_guard(P.USERS_READ))

# ── Tracking / history (read-only) ───────────────────────────────────────
router.include_router(tracking.router, dependencies=_guard(P.TRACKING_READ))
router.include_router(history.router, dependencies=_guard(P.HISTORY_READ))

# ── Live media ───────────────────────────────────────────────────────────
# WS handshake auth lands in Phase 3; HTTP MJPEG/snapshot is guarded now.
router.include_router(ws.router)
router.include_router(stream.router, dependencies=_guard(P.STREAMS_VIEW))

# ── Metrics ──────────────────────────────────────────────────────────────
router.include_router(metrics.router, dependencies=_guard(P.METRICS_READ))
router.include_router(metrics_clients.router, dependencies=_guard(P.METRICS_READ))

# ── Admin: cameras + front-desk config ───────────────────────────────────
router.include_router(cameras.router, dependencies=_guard(P.CAMERAS_MANAGE))
router.include_router(frontdesk.router, dependencies=_guard(P.FRONTDESK_OPERATE))
router.include_router(
    frontdesk_admin.router, dependencies=_guard(P.FRONTDESK_ADMIN_MANAGE)
)

# ── Single-facility config and integrations ──────────────────────────────
router.include_router(facility.router, dependencies=_guard(P.LOCATIONS_MANAGE))
router.include_router(locations.router, dependencies=_guard(P.LOCATIONS_MANAGE))
router.include_router(integrations.router, dependencies=_guard(P.INTEGRATIONS_MANAGE))
router.include_router(attendance.router, dependencies=_guard(P.ATTENDANCE_READ))
