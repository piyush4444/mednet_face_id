"""
logger.py — Centralized logging for multi-process pipeline.

Problem:
    Multiple processes writing to the same log produces garbled output.

Solution:
    Each process configures its own logger with a consistent format that
    includes the process name, making interleaved output readable.

Usage:
    from engine.utils.logger import setup_logger
    setup_logger()   # Call once at the top of each process function
"""

import logging
import sys
import multiprocessing

import backend.config as config


def setup_logger(name: str = None, level: str = None):
    """
    Configure logging for the current process.

    Args:
        name:   Logger name (defaults to current process name).
        level:  Log level string (defaults to config.LOG_LEVEL).
    """
    if name is None:
        name = multiprocessing.current_process().name

    if level is None:
        level = config.LOG_LEVEL

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers if called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)

    return logger


def camera_logger(name: str, camera_id: str):
    """
    Return a logger that prepends "[camera_id] " to every message body.

    Intended for use inside a per-camera pipeline process:

        from backend.engine.utils.logger import setup_logger, camera_logger

        def detection_process(camera_id, ...):
            setup_logger("detection")
            logger = camera_logger("detection", camera_id)   # shadows module-level
            logger.info("Detected %d faces", 3)
            # → output: "[cam_entry] Detected 3 faces"

    When camera_id is empty the plain Logger is returned (no adapter
    overhead, no prefix) so standalone tests that don't know a camera_id
    keep working unchanged.
    """
    base = logging.getLogger(name)
    if not camera_id:
        return base

    tag = f"[{camera_id}] "

    class _CameraPrefixed(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            return tag + msg, kwargs

    return _CameraPrefixed(base, {})
