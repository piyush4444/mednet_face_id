import queue as _queue
import time
from multiprocessing import Process
from backend.camera.pipeline_runner import run_pipeline
from backend.camera import metrics as camera_metrics
from backend.config import ENABLE_LOGGING


# Micro-batching: drain up to N tasks per loop iteration and process
# them back-to-back. Sequential semantics are unchanged - this is not
# true GPU batching - but it eliminates the per-frame idle sleep and
# amortises queue-bookkeeping overhead across the batch. Keep small so
# a burst from one producer cannot stall other cameras for long.
AI_MAX_BATCH = 4
# Short blocking wait for the first task of a batch. Long enough to
# avoid a busy-loop on empty queues, short enough that shutdown
# signals are observed promptly.
AI_FIRST_GET_TIMEOUT_S = 0.05


def _put_drop_oldest(q, payload):
    """
    Non-blocking enqueue: if full, drop the oldest entry and retry once.

    Producers must never block on full queues - doing so propagates
    latency all the way back to the camera grabber. A dropped detection
    event is always preferable to a stalled pipeline.
    """
    if q is None:
        return False
    try:
        q.put_nowait(payload)
        return True
    except Exception:
        # Full - drop oldest, try again.
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(payload)
            return True
        except Exception:
            return False


class AIWorker(Process):
    def __init__(
        self, worker_id, frame_queue, event_queue, ws_queue=None,
        metrics_dict=None, frame_pool_handle=None,
    ):
        super().__init__()
        self.worker_id = worker_id
        self.frame_queue = frame_queue
        self.event_queue = event_queue
        self.ws_queue = ws_queue
        self.metrics_dict = metrics_dict
        self.frame_pool_handle = frame_pool_handle

    def _broadcast(self, payload):
        _put_drop_oldest(self.ws_queue, payload)

    def run(self):
        print(f"[AI-{self.worker_id}] started")

        try:
            self._loop()
        except KeyboardInterrupt:
            print(f"[AI-{self.worker_id}] stopped")

    def _loop(self):
        # Bind metrics in this child process and spin up a collector.
        if self.metrics_dict is not None:
            camera_metrics.bind(self.metrics_dict)
        metrics = camera_metrics.Collector("ai")

        # Attach to the SharedMemory pool so we can reconstruct frames
        # as zero-copy numpy views instead of unpickling them.
        frame_pool = (
            self.frame_pool_handle.attach()
            if self.frame_pool_handle is not None else None
        )

        while True:
            # Publish current queue depth once per batch (cheap signal
            # for ops dashboards; avoids a qsize() call per frame).
            try:
                metrics.gauge("frame_queue_depth", self.frame_queue.qsize())
            except Exception:
                pass

            batch = self._drain_batch()
            if not batch:
                continue

            metrics.observe("batch_size", len(batch))

            for task in batch:
                self._process_one(task, frame_pool, metrics)

    def _drain_batch(self) -> list:
        """Block briefly for the first task, then non-blocking drain up
        to AI_MAX_BATCH-1 more. Returns [] on timeout so the outer loop
        keeps its cancellation responsiveness."""
        try:
            first = self.frame_queue.get(timeout=AI_FIRST_GET_TIMEOUT_S)
        except _queue.Empty:
            return []

        batch = [first]
        while len(batch) < AI_MAX_BATCH:
            try:
                batch.append(self.frame_queue.get_nowait())
            except _queue.Empty:
                break
        return batch

    def _process_one(self, task, frame_pool, metrics):
        """Run the pipeline on a single task and always release its slot.

        Isolated per-task so an exception on one frame cannot skip the
        slot release or prevent the rest of the batch from running."""
        # Track the slot so we can return it to the free-list under
        # every exit path (success, pipeline exception, unexpected
        # error). Leaking slots silently drains pool capacity.
        slot_to_release = (
            task["slot"]
            if frame_pool is not None and isinstance(task, dict)
            and "slot" in task
            else None
        )

        try:
            # Resolve the frame either from a shared-memory slot (fast
            # path) or from an inline numpy array (legacy fallback).
            if slot_to_release is not None:
                frame = frame_pool.view(
                    task["slot"], task["shape"], task["dtype"],
                )
            else:
                frame = task.get("frame")

            t0 = time.perf_counter()
            result = run_pipeline(frame, task["camera_id"])
            inference_ms = (time.perf_counter() - t0) * 1000.0
            metrics.observe("inference_ms", inference_ms)
            metrics.incr("frames_processed")

            if result and result.get("faces"):
                faces = result["faces"]
                metrics.incr("detections", len(faces))
                if ENABLE_LOGGING:
                    print(f"[AI-{self.worker_id}] Faces detected:", len(faces))

                _put_drop_oldest(self.event_queue, {
                    "camera_id": task["camera_id"],
                    "floor": task["floor"],
                    "type": task["type"],
                    "timestamp": time.time(),
                    "faces": faces,
                })

                # DETECTION broadcast has moved to EventProcessor
                # (owns the tracker; emits smoothed, throttled events).

        except Exception as e:
            print(f"[AI-{self.worker_id}] error:", e)
        finally:
            # Always return the slot - leaking slots silently drains
            # pool capacity and eventually triggers shm_pool_exhausted.
            if slot_to_release is not None and frame_pool is not None:
                frame_pool.release_slot(slot_to_release)
