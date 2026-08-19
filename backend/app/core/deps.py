"""
deps.py — FastAPI security dependencies.

Two things live here:

* :func:`get_current_account` — resolve the session cookie to a live human
  user or service principal, or 401. Loads fresh from the DB every request so
  grants/revokes/disables take effect immediately.
* :func:`require_permission` — the guard factory used across ``router.py``
  in Phase 2. It is gated by ``settings.AUTH_ENABLED``: while that flag is
  off (the demo-stable default) the guard is a no-op, so routes can be
  decorated now and enforcement switches on with the login UI.

CSRF: mutating requests must echo the non-HttpOnly CSRF cookie in an
``X-CSRF-Token`` header (double-submit). :func:`verify_csrf` enforces it,
also gated by ``AUTH_ENABLED``.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.auth_models import Permission
from backend.app.db.postgres import get_db
from backend.app.services import auth_service

CSRF_COOKIE_NAME = "iris_csrf"
CSRF_HEADER_NAME = "x-csrf-token"


def _account_from_request(request: Request, db: Session) -> auth_service.AuthPrincipal | None:
    """Resolve the session cookie to an active account, or None."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    session = auth_service.read_session_token(token or "")
    if session is None:
        return None
    kind, principal_id = session
    acct = auth_service.get_account(db, principal_id, kind)
    if acct is None or not acct.is_active:
        return None
    return acct


def get_optional_account(
    request: Request, db: Session = Depends(get_db)
) -> auth_service.AuthPrincipal | None:
    """Account if a valid session is present, else None (never raises)."""
    return _account_from_request(request, db)


def get_current_account(
    request: Request, db: Session = Depends(get_db)
) -> auth_service.AuthPrincipal:
    """Require a valid session; 401 otherwise."""
    acct = _account_from_request(request, db)
    if acct is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return acct


def require_permission(perm: Permission):
    """Return a dependency enforcing ``perm``.

    No-op while ``AUTH_ENABLED`` is False so Phase 2 route decoration does
    not change behaviour until the login flow ships. Once enabled: 401 if
    unauthenticated, 403 if the account lacks ``perm``.
    """

    def _dep(
        request: Request, db: Session = Depends(get_db)
    ) -> auth_service.AuthPrincipal | None:
        if not settings.AUTH_ENABLED:
            return None
        acct = get_current_account(request, db)
        if not auth_service.has_permission(acct, perm, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {perm.value}",
            )
        return acct

    return _dep


def verify_csrf(request: Request) -> None:
    """Double-submit CSRF check for mutating requests.

    Gated by ``AUTH_ENABLED``. Safe methods are exempt. The header must
    equal the CSRF cookie value set at login.
    """
    if not settings.AUTH_ENABLED:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie_val = request.cookies.get(CSRF_COOKIE_NAME)
    header_val = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_val or not header_val or cookie_val != header_val:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
