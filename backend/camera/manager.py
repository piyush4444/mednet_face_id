import logging
import sys
from multiprocessing import Queue, Manager

from backend.config import NUM_AI_WORKERS
from backend.camera import store as camera_store
from backend.camera.worker import CameraWorker
from backend.camera.ai_worker import AIWorker
from backend.camera.event_processor import EventProcessor
from backend.camera.shared_frame_buffer import FrameBufferPool
from backend.camera import metrics as camera_metrics

logger = logging.getLogger(__name__)


# Module-level handles so an in-process caller (e.g. FastAPI startup that
# calls ``start_camera_system()``) can reach the shared queues.
_shared_event_queue = None
_shared_ws_queue = None
_shared_stream_queues: dict = {}
_shared_stream_versions = None  # Manager().dict() — shared across processes
_shared_viewer_counts = None    # Manager().dict() — active MJPEG viewers per camera
_shared_metrics = None          # Manager().dict() — cross-process metrics sink
_shared_camera_roles = None     # Manager().dict() — camera_id -> live role
_manager = None                 # Keep Manager alive for the life of the process
_frame_pool = None              # FrameBufferPool — SharedMemory backing for frames


def get_shared_event_queue():
    return _shared_event_queue


def get_shared_ws_queue():
    return _shared_ws_queue


def get_stream_queue(camera_id: str):
    return _shared_stream_queues.get(camera_id)


def get_stream_version(camera_id: str) -> int:
    """Read current stream version (shared across processes)."""
    if _shared_stream_versions is None:
        return 0
    return _shared_stream_versions.get(camera_id, 0)


def viewer_inc(camera_id: str) -> int:
    """Mark a new MJPEG viewer connected. Returns the new count.

    When count > 0, the camera worker pushes frames into the per-camera
    stream_queue. When 0, it skips the push entirely — saving the pickle
    cost of a ~1.5 MB numpy array per capture. Detection is unaffected.
    """
    if _shared_viewer_counts is None:
        return 0
    n = _shared_viewer_counts.get(camera_id, 0) + 1
    _shared_viewer_counts[camera_id] = n
    return n


def viewer_dec(camera_id: str) -> int:
    """Mark a viewer disconnected. Returns the new count (clamped at 0)."""
    if _shared_viewer_counts is None:
        return 0
    n = max(0, _shared_viewer_counts.get(camera_id, 0) - 1)
    _shared_viewer_counts[camera_id] = n
    return n


def get_viewer_count(camera_id: str) -> int:
    if _shared_viewer_counts is None:
        return 0
    return _shared_viewer_counts.get(camera_id, 0)


def get_metrics_dict():
    """Shared metrics dict, or None if the system hasn't booted yet."""
    return _shared_metrics


def shutdown_frame_pool():
    """Release SharedMemory blocks. Call after joining child processes."""
    global _frame_pool
    if _frame_pool is not None:
        _frame_pool.destroy()
        _frame_pool = None


def start_all_cameras(
    frame_queue, stream_queues, ws_queue, stream_versions, metrics_dict,
    frame_pool_handle=None, viewer_counts=None,
):
    """Legacy bulk spawn — retained as a compatibility helper.

    The live path now goes through :class:`CameraRegistry`, which spawns
    workers one at a time and lets the REST API add/remove them at
    runtime. This function is kept so any external caller (older entry
    points, ad-hoc scripts) keeps working; it reads from the PostgreSQL
    repository.
    """
    workers = []
    for cam in camera_store.load():
        if not cam.get("active", True):
            continue
        cam_id = cam["camera_id"]
        if cam_id not in stream_queues:
            stream_queues[cam_id] = Queue(maxsize=1)
        if stream_versions is not None and cam_id not in stream_versions:
            stream_versions[cam_id] = 0
        if viewer_counts is not None and cam_id not in viewer_counts:
            viewer_counts[cam_id] = 0

        worker = CameraWorker(
            cam, frame_queue, stream_queues[cam_id], ws_queue,
            stream_versions, metrics_dict,
            frame_pool_handle=frame_pool_handle,
            viewer_counts=viewer_counts,
        )
        worker.start()
        workers.append(worker)
    logger.info("started %d camera workers (legacy bulk path)", len(workers))
    return workers


def start_ai_workers(
    frame_queue, event_queue, ws_queue, metrics_dict=None,
    n=NUM_AI_WORKERS, frame_pool_handle=None,
):
    if n > 1:
        # Identity smoothing now lives in EventProcessor (single-process
        # observer), so the tracker no longer fragments across workers.
        # The remaining per-worker caches are read-mostly (FAISS DB,
        # name cache) or per-camera (motion-skip state) — fragmenting
        # them at worst costs a bit of extra inference, never
        # correctness. Still log at INFO so ops see the scale-out.
        print(
            f"[INFO] NUM_AI_WORKERS={n} — tracker smoothing lives in "
            f"EventProcessor and is unaffected. Per-worker motion-skip "
            f"state may fragment slightly (benign)."
        )
    workers = []
    for i in range(n):
        w = AIWorker(
            i, frame_queue, event_queue, ws_queue,
            metrics_dict=metrics_dict,
            frame_pool_handle=frame_pool_handle,
        )
        w.start()
        workers.append(w)
    return workers


def start_event_processor(event_queue, ws_queue, camera_roles=None):
    p = EventProcessor(event_queue, ws_queue, camera_roles)
    p.start()
    return p


def start_camera_system(test_mode: bool = False):
    """
    Spawn the full camera stack and return ``(processes, ws_queue)``.

    Intended for in-process callers (FastAPI lifespan). Wires the
    :class:`CameraRegistry` so subsequent CRUD calls from the REST API
    can spawn/stop workers without touching this function again.
    """
    global _shared_event_queue, _shared_ws_queue, _shared_stream_queues
    global _shared_stream_versions, _shared_viewer_counts, _shared_metrics
    global _shared_camera_roles
    global _manager, _frame_pool

    # Local import — registry imports manager indirectly via worker, so
    # importing it at module top would form a cycle during cold start.
    from backend.camera.registry import get_registry

    from backend.app.db.postgres import init_db
    init_db()
    # Standalone manager launches do not pass through FastAPI lifespan.
    camera_store.seed_from_legacy_json()

    # Load the PostgreSQL roster before spawning children. Child processes
    # consume an immutable worker config plus a shared live role map; they do
    # not query the database on the detection hot path.
    cams_at_boot = camera_store.load()
    logger.info("camera roster loaded from PostgreSQL — %d cameras", len(cams_at_boot))

    frame_queue = Queue(maxsize=40)
    event_queue = Queue(maxsize=100)
    ws_queue = Queue(maxsize=200)

    # Per-camera stream queues are now allocated by the registry as
    # workers are spawned. We start with an empty dict and the registry
    # mutates it in place — `get_stream_queue` reads from this same dict.
    stream_queues: dict = {}

    _manager = Manager()
    stream_versions = _manager.dict()
    viewer_counts = _manager.dict()
    camera_roles = _manager.dict({
        cam["camera_id"]: cam.get("role", "inside") for cam in cams_at_boot
    })

    metrics_dict = _manager.dict()
    camera_metrics.bind(metrics_dict)

    # SharedMemory pool for zero-copy frame transport (replaces pickling
    # ~1.5 MB numpy arrays through multiprocessing.Queue on every hop).
    _frame_pool = FrameBufferPool()
    frame_pool_handle = _frame_pool.handle

    _shared_event_queue = event_queue
    _shared_ws_queue = ws_queue
    _shared_stream_queues = stream_queues
    _shared_stream_versions = stream_versions
    _shared_viewer_counts = viewer_counts
    _shared_metrics = metrics_dict
    _shared_camera_roles = camera_roles

    event_processor = start_event_processor(event_queue, ws_queue, camera_roles)
    procs = [event_processor]

    if test_mode:
        from backend.app.db.postgres import SessionLocal
        from backend.app.db.models import Patient
        _seed_db = SessionLocal()
        try:
            if not _seed_db.query(Patient).filter(Patient.id == 1).first():
                _seed_db.add(Patient(id=1, mrn="TEST0001", name="Piyush"))
                _seed_db.commit()
        finally:
            _seed_db.close()

        from backend.camera.test_simulator import TestSimulator
        sim = TestSimulator(event_queue)
        sim.start()
        procs.append(sim)
        return procs, ws_queue

    # Wire the registry with all shared resources, then let it spawn
    # workers from the persistent PostgreSQL roster. Camera workers are owned
    # by the registry — its ``stop_all()`` is the only thing that
    # should join/terminate them. We deliberately do NOT add them to
    # ``procs`` so the lifespan shutdown loop can't double-close them.
    registry = get_registry()
    registry.init(
        frame_queue=frame_queue,
        ws_queue=ws_queue,
        stream_queues=stream_queues,
        stream_versions=stream_versions,
        viewer_counts=viewer_counts,
        metrics_dict=metrics_dict,
        camera_roles=camera_roles,
        frame_pool_handle=frame_pool_handle,
    )
    registry.start_all_from_store()

    procs += start_ai_workers(
        frame_queue, event_queue, ws_queue, metrics_dict, NUM_AI_WORKERS,
        frame_pool_handle=frame_pool_handle,
    )

    return procs, ws_queue


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    procs, _ = start_camera_system(test_mode=test_mode)

    try:
        for w in procs:
            w.join()
    except KeyboardInterrupt:
        print("[SHUTDOWN]")
