"""
logging.py — Logging configuration for the FastAPI backend.

Configures structured logging with consistent format across all modules.
Call setup_logging() once at application startup.
"""

import asyncio
import logging
import sys

from backend.app.core.config import settings
from backend.config import ENABLE_LOGGING


class _CancelledErrorFilter(logging.Filter):
    """Suppress benign asyncio.CancelledError tracebacks from uvicorn.

    Starlette's StreamingResponse spawns a background `listen_for_disconnect`
    task that's cancelled the moment the client closes the MJPEG `<img>`
    (or refreshes / navigates away). uvicorn surfaces that cancellation as
    "Exception in ASGI application" with a full traceback even though it
    is the documented disconnect path — and the MJPEG generator already
    handles cancellation cleanly. The traceback is pure noise that hides
    real errors, so we drop log records whose only exception is a
    CancelledError.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info
        if exc and exc[0] is not None and issubclass(exc[0], asyncio.CancelledError):
            return False
        return True


def setup_logging() -> None:
    """
    Configure root logger for the backend.

    Level selection (highest wins):
      1. ENABLE_LOGGING=False  → WARNING only (master off-switch)
      2. settings.DEBUG=True   → DEBUG
      3. otherwise             → INFO

    - Consistent timestamp | level | module | message format
    - Outputs to stdout (container-friendly)
    """
    if not ENABLE_LOGGING:
        level = logging.WARNING
    elif settings.DEBUG:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    if not root.handlers:
        root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)

    # Drop benign client-disconnect CancelledError tracebacks emitted by
    # uvicorn during MJPEG streaming. Attach to both the named logger and
    # the root, since uvicorn's ASGI error path can route either way.
    cancel_filter = _CancelledErrorFilter()
    logging.getLogger("uvicorn.error").addFilter(cancel_filter)
    logging.getLogger("uvicorn").addFilter(cancel_filter)

    # Only emit the init banner when logging is actually enabled.
    if ENABLE_LOGGING:
        logging.getLogger("backend").info(
            "Logging initialized (level=%s)", logging.getLevelName(level),
        )
