import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.core.config import settings
from backend.app.db.auth_models import Permission
from backend.app.db.postgres import SessionLocal
from backend.app.services import auth_service
from backend.app.services.ws_manager import manager

router = APIRouter()

# Application-defined WS close code for "unauthorized" (4000–4999 range).
WS_POLICY_UNAUTHORIZED = 4401


async def _ws_authorized(websocket: WebSocket) -> bool:
    """Validate the session cookie on the WS handshake.

    The live feed pushes DETECTION events (named presence of tracked
    people), so it requires ``tracking.read``. A no-op while
    ``AUTH_ENABLED`` is False. Cookies ride the WS handshake automatically
    (same-origin), so the browser needs no special handling.
    """
    if not settings.AUTH_ENABLED:
        return True
    token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    account_id = auth_service.read_session_token(token or "")
    if account_id is None:
        return False
    db = SessionLocal()
    try:
        acct = auth_service.get_account(db, account_id)
        if acct is None or not acct.is_active:
            return False
        return auth_service.has_permission(acct, Permission.TRACKING_READ, db)
    finally:
        db.close()


@router.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    # Authorize BEFORE accept() so an unauthenticated client is rejected at
    # the handshake (Starlette turns a pre-accept close into a 403).
    if not await _ws_authorized(websocket):
        await websocket.close(code=WS_POLICY_UNAUTHORIZED)
        return

    await manager.connect(websocket)

    try:
        # Block on receive — this is a server-push-only socket, but
        # awaiting receive_text() lets the ASGI server detect client
        # disconnections immediately instead of up to 10s later.
        # Any incoming messages (pings, keep-alives) are silently discarded.
        while True:
            await websocket.receive_text()

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        manager.disconnect(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
