"""
client_api.py — thin HTTP layer for the B2B partner's HIS.

Two outbound calls: punch attendance and patient pre-registration. Kept
dependency-free (standard-library ``urllib``) so the backend gains no new
package for a couple of JSON POSTs.

Everything here is config-driven and fail-closed:

- If the endpoint URL is empty the call is *disabled* — callers check
  ``punch_enabled()`` / ``prereg_enabled()`` first and no request is
  ever attempted, so a partial config can't leak data to the wrong host.
- The API key is attached per ``CLIENT_API_AUTH_HEADER`` /
  ``CLIENT_API_AUTH_SCHEME`` (default ``Authorization: Bearer <key>``);
  an empty key sends no auth header.
- Secrets live only in env — never in the payloads persisted to the DB.

Both functions return a :class:`ClientResult`; they never raise for
network/HTTP errors (the worker decides retry vs dead-letter from the
result), only for genuinely unexpected bugs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from backend.app.core.config import settings

logger = logging.getLogger("backend.client_api")


@dataclass
class ClientResult:
    ok: bool
    status_code: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # Convenience fields pulled out of a pre-registration response.
    token_no: Optional[str] = None
    pre_regn_id: Optional[int] = None
    queue_setup_id: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def punch_enabled() -> bool:
    return bool(settings.CLIENT_PUNCH_API_URL.strip())


def prereg_enabled() -> bool:
    return bool(settings.CLIENT_PREREG_API_URL.strip())


def _auth_headers(api_key: str) -> Dict[str, str]:
    key = (api_key or "").strip()
    if not key:
        return {}
    header = (settings.CLIENT_API_AUTH_HEADER or "Authorization").strip()
    scheme = (settings.CLIENT_API_AUTH_SCHEME or "").strip()
    value = f"{scheme} {key}".strip() if scheme else key
    return {header: value}


def _post_json(url: str, api_key: str, payload: Any) -> ClientResult:
    """POST ``payload`` as JSON; parse a JSON response if present.

    Returns a :class:`ClientResult` with ``ok`` true only on a 2xx that
    does not carry an explicit ``success: false`` body.
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(_auth_headers(api_key))
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=settings.CLIENT_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
            data = _safe_json(raw)
            # A 2xx that the app-layer marks unsuccessful is a failure.
            if isinstance(data, dict) and data.get("success") is False:
                return ClientResult(
                    ok=False, status_code=status, data=data,
                    error=str(data.get("message") or "client reported success=false"),
                )
            return ClientResult(ok=True, status_code=status, data=data if isinstance(data, dict) else None)
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return ClientResult(
            ok=False, status_code=exc.code, data=_safe_json(raw) if raw else None,
            error=f"HTTP {exc.code}: {raw[:300]}" if raw else f"HTTP {exc.code}",
        )
    except urllib.error.URLError as exc:
        return ClientResult(ok=False, error=f"connection error: {exc.reason}")
    except Exception as exc:  # pragma: no cover — defensive
        return ClientResult(ok=False, error=f"unexpected error: {exc}")


def _safe_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def push_punch(payload: Dict[str, Any]) -> ClientResult:
    """Send ONE punch to the client attendance API (their array-of-one
    device-emulation contract). Idempotent on their side via
    ``biometricIDX``, so a retry after an ambiguous failure is safe."""
    url = settings.CLIENT_PUNCH_API_URL.strip()
    if not url:
        return ClientResult(ok=False, error="punch API disabled (no URL)")
    # The client expects a JSON array even for a single punch.
    return _post_json(url, settings.CLIENT_PUNCH_API_KEY, [payload])


def push_prereg(payload: Dict[str, Any]) -> ClientResult:
    """Send a pre-registration; pull ``preRegnId`` / ``tokenNo`` /
    ``queueSetupID`` out of the response (their shape nests these under
    ``data``)."""
    url = settings.CLIENT_PREREG_API_URL.strip()
    if not url:
        return ClientResult(ok=False, error="prereg API disabled (no URL)")
    result = _post_json(url, settings.CLIENT_PREREG_API_KEY, payload)
    if result.ok and isinstance(result.data, dict):
        data = result.data.get("data")
        if isinstance(data, dict):
            token = data.get("tokenNo")
            result.token_no = str(token) if token not in (None, "") else None
            pre_id = data.get("preRegnId")
            result.pre_regn_id = int(pre_id) if isinstance(pre_id, int) else None
            qsid = data.get("queueSetupID")
            try:
                result.queue_setup_id = int(qsid) if qsid not in (None, "") else None
            except (TypeError, ValueError):
                result.queue_setup_id = None
    return result
