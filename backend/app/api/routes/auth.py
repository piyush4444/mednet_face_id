"""
auth.py — Login / logout / session introspection.

Phase 1 of the auth build. These endpoints are always mounted, but until
``settings.AUTH_ENABLED`` is True nothing else in the app is guarded, so the
demo keeps working while the login UI is built.

Session model: an HttpOnly, signed cookie carrying only the account id.
A second, non-HttpOnly CSRF cookie is set alongside for the double-submit
check on mutating requests (see ``core/deps.verify_csrf``).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.deps import CSRF_COOKIE_NAME, get_current_account
from backend.app.db.auth_models import StaffAccount
from backend.app.db.postgres import get_db
from backend.app.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class MeOut(BaseModel):
    id: int
    username: str
    role: str
    permissions: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────
def _client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the proxy's X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookies(response: Response, account: StaffAccount) -> str:
    """Set the session + CSRF cookies. Returns the CSRF token."""
    token = auth_service.mint_session_token(account.id)
    max_age = settings.SESSION_TTL_HOURS * 3600
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf,
        max_age=max_age,
        httponly=False,  # readable by JS so the SPA can echo it in a header
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )
    return csrf


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _me_payload(account: StaffAccount) -> MeOut:
    perms = sorted(p.value for p in auth_service.effective_permissions(account))
    return MeOut(
        id=account.id,
        username=account.username,
        role=account.role,
        permissions=perms,
    )


# ── Routes ───────────────────────────────────────────────────────────────
@router.get("/config")
def auth_config():
    """Public: tell the SPA whether auth is being enforced.

    When ``auth_enabled`` is False the frontend skips the login gate entirely
    and behaves as it always has, so the demo build keeps working. When True
    the SPA requires a session and drives its UI from ``/auth/me``.
    """
    return {"auth_enabled": settings.AUTH_ENABLED}


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Authenticate and start a session.

    Returns the same shape as ``/auth/me`` so the SPA can populate its auth
    context from the login response without a second round-trip.
    """
    ip = _client_ip(request)
    username = payload.username.strip()

    if auth_service.is_locked_out(username, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
        )

    account = auth_service.authenticate(
        db, username=username, password=payload.password, ip=ip
    )
    if account is None:
        auth_service.record_login_failure(username, ip)
        audit_service.record(
            db, action="auth.login_failed", username=username, ip=ip
        )
        # Generic message — don't reveal whether the username exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    auth_service.record_login_success(username, ip)
    audit_service.record(db, action="auth.login", actor=account, ip=ip)
    _set_session_cookies(response, account)
    return _me_payload(account)


@router.post("/logout")
def logout(response: Response):
    """Clear the session. Idempotent — safe to call when not logged in."""
    _clear_session_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
def me(account: StaffAccount = Depends(get_current_account)):
    """Return the current account + its effective permissions.

    401 when unauthenticated — the SPA treats that as 'show login'.
    """
    return _me_payload(account)
