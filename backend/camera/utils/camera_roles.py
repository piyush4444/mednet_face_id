"""Fast runtime camera-role lookup backed by shared process state.

The parent process loads camera configuration from PostgreSQL once and keeps a
``multiprocessing.Manager`` dictionary synchronized after each mutation. The
event processor reads this map on its hot path, avoiding a database query for
every detection while still seeing role edits immediately.
"""

from collections.abc import Mapping


_role_map: Mapping = {}


def bind_role_map(role_map: Mapping | None) -> None:
    """Bind the local process to the manager-backed role map."""
    global _role_map
    _role_map = role_map if role_map is not None else {}


def get_camera_role(camera_id: str) -> str:
    return _role_map.get(camera_id, "inside")


def has_exit_camera() -> bool:
    return any(role == "exit" for role in _role_map.values())
