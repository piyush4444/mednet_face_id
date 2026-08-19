import time
from multiprocessing import Process

import cv2

import backend.config as config
from backend.camera.frame_grabber import FrameGrabber
from backend.camera import metrics as camera_metrics


TARGET_FPS = 6
FRAME_WIDTH = 640
FRAME_HEIGHT = 480


def _resize_keep_ratio(frame, max_dim):
    """
    Downscale `frame` so its longest edge is <= max_dim, preserving
    aspect ratio. No-op if already small enough. Returns the same array
    when resize is disabled — no wasted copy.
    """
    if frame is None or max_dim <= 0:
        return frame
    h, w = frame.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return frame
    scale = max_dim / longest
    new_w, new_h = int(w * scale), int(h * scale)
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


class CameraWorker(Process):
    def __init__(
        self,
        camera_config,
        frame_queue,
        stream_queue=None,
        ws_queue=None,
        stream_versions=None,
        metrics_dict=None,
        frame_pool_handle=None,
        viewer_counts=None,
    ):
        super().__init__()
        self.camera_config = camera_config
        self.camera_id = camera_config["camera_id"]
        self.source = camera_config["source"]
        self.floor = camera_config["floor"]
        self.stream_type = camera_config["type"]
        self.frame_queue = frame_queue
        self.stream_queue = stream_queue
        self.ws_queue = ws_queue
        self.stream_versions = stream_versions
        self.metrics_dict = metrics_dict
        self.frame_pool_handle = frame_pool_handle
        self.viewer_counts = viewer_counts
        self.running = True

    def run(self):
        try:
            self._run()
        except KeyboardInterrupt:
            print(f"[STOPPED] {self.camera_id} (interrupt)")

    def _run(self):
        # Skip inactive cameras
        if not self.camera_config.get("active", True):
            print(f"[SKIP] {self.camera_id} inactive")
            return

        # Child-process metrics wiring — each worker binds its own view
        # of the shared dict and creates a namespaced collector.
        if self.metrics_dict is not None:
            camera_metrics.bind(self.metrics_dict)
        metrics = camera_metrics.Collector(f"camera.{self.camera_id}")

        # Attach to the SharedMemory frame pool once per process.
        frame_pool = (
            self.frame_pool_handle.attach()
            if self.frame_pool_handle is not None else None
        )

        grabber = FrameGrabber(self.camera_config)
        grabber.start()

        print(f"[STARTED] {self.camera_id}")

        last_time = 0.0
        frame_interval = 1.0 / TARGET_FPS
        last_reconnect_time = 0.0
        last_frame_time = time.time()  # Track when we last got a valid frame
        disconnect_sent = False        # Only send one CAMERA_DISCONNECTED per gap

        try:
            while self.running:
                # Check for reconnection and mark it
                if grabber.has_reconnected():
                    last_reconnect_time = time.time()
                    disconnect_sent = False
                    print(f"[RECONNECTED DETECTED] {self.camera_id}")

                # FPS control
                now = time.time()
                if now - last_time < frame_interval:
                    time.sleep(0.001)
                    continue
                last_time = now

                # Get frame from grabber
                frame, frame_ts = grabber.get_frame()

                if frame is None:
                    # Detect disconnect: no frame for 3+ seconds
                    if not disconnect_sent and now - last_frame_time > 3.0:
                        disconnect_sent = True
                        if self.ws_queue is not None:
                            try:
                                self.ws_queue.put_nowait({
                                    "type": "CAMERA_DISCONNECTED",
                                    "camera_id": self.camera_id,
                                })
                                print(f"[CAMERA DISCONNECTED] {self.camera_id}")
                            except Exception:
                                pass
                    time.sleep(0.001)
                    continue

                # Got a valid frame — reset disconnect tracking
                last_frame_time = now
                disconnect_sent = False

                # Pre-inference downscale — aspect-preserving, applied ONCE
                # here so every downstream stage (frame_queue, stream_queue,
                # AI worker, MJPEG endpoint) sees the smaller frame.
                if getattr(config, "RESIZE_FRAME", False):
                    frame = _resize_keep_ratio(
                        frame, getattr(config, "PROCESS_MAX_DIM", 960),
                    )

                # Discard stale frames (prevent processing frozen frames after reconnection)
                if now - frame_ts > 2.0:
                    print(f"[STALE FRAME] {self.camera_id} age={now - frame_ts:.2f}s")
                    continue

                # Only send reconnection event AFTER getting a fresh frame post-reconnection
                if last_reconnect_time > 0 and frame_ts > last_reconnect_time:
                    # Increment shared stream version (visible to stream endpoint via Manager dict)
                    new_version = 0
                    if self.stream_versions is not None:
                        new_version = self.stream_versions.get(self.camera_id, 0) + 1
                        self.stream_versions[self.camera_id] = new_version
                    print(f"[STREAM VERSION] {self.camera_id} → v{new_version}")

                    if self.stream_queue is not None:
                        try:
                            while not self.stream_queue.empty():
                                self.stream_queue.get_nowait()
                        except Exception:
                            pass

                    if self.ws_queue is not None:
                        try:
                            self.ws_queue.put_nowait({
                                "type": "CAMERA_RECONNECTED",
                                "camera_id": self.camera_id,
                                "version": new_version
                            })
                            print(f"[SENT RECONNECT EVENT] {self.camera_id} v{new_version}")
                        except Exception as e:
                            print(f"[EVENT SEND ERROR] {self.camera_id}: {e}")
                    last_reconnect_time = 0.0  # Clear so we only send once

                # Queue frame for processing — drop-oldest, never block.
                #
                # Frame payload path:
                #   - With shared-memory pool: acquire a free slot, write
                #     frame bytes, enqueue only the slot index + shape.
                #     Slots are released by the consumer after processing
                #     and by us here if we drop a payload (queue full).
                #   - Fallback (pool missing): enqueue the numpy array
                #     directly, same as before.
                #
                # Drop-oldest with slot release: when the queue is full we
                # pop the oldest payload AND release its slot back to the
                # pool, otherwise the pool slowly leaks capacity under
                # sustained overload.
                if self.frame_queue.full():
                    try:
                        dropped_payload = self.frame_queue.get_nowait()
                        metrics.incr("frame_queue_drops")
                        if (
                            frame_pool is not None
                            and isinstance(dropped_payload, dict)
                            and "slot" in dropped_payload
                        ):
                            frame_pool.release_slot(dropped_payload["slot"])
                    except Exception:
                        pass

                payload = None
                slot = None
                if frame_pool is not None:
                    slot = frame_pool.acquire_slot()
                    if slot is None:
                        # All slots held by in-flight frames — consumer
                        # is stalled. Drop this frame rather than block.
                        metrics.incr("shm_pool_exhausted")
                    else:
                        try:
                            frame_pool.write(slot, frame)
                            payload = {
                                "camera_id": self.camera_id,
                                "floor": self.floor,
                                "type": self.stream_type,
                                "slot": slot,
                                "shape": tuple(frame.shape),
                                "dtype": str(frame.dtype),
                            }
                        except Exception as exc:
                            print(f"[SHM WRITE ERROR] {self.camera_id}: {exc}")
                            frame_pool.release_slot(slot)
                            slot = None
                else:
                    payload = {
                        "camera_id": self.camera_id,
                        "floor": self.floor,
                        "type": self.stream_type,
                        "frame": frame,
                    }

                if payload is not None:
                    try:
                        self.frame_queue.put_nowait(payload)
                        metrics.incr("enqueued")
                    except Exception:
                        # Enqueue lost a race with another producer;
                        # return the slot so it isn't leaked.
                        metrics.incr("frame_queue_drops")
                        if frame_pool is not None and slot is not None:
                            frame_pool.release_slot(slot)

                # Stream queue for live preview.
                #
                # Skip the push entirely when no MJPEG client is watching
                # this camera — the put would otherwise pickle a ~1.5 MB
                # numpy array across the process boundary on every
                # capture, only to be dropped unread. Detection is
                # unaffected: it consumes from `frame_queue`, not here.
                #
                # Failure-open: if the shared dict is missing or the
                # lookup raises, fall back to the previous behaviour
                # (always push) so a dict glitch can never blank the
                # stream while a viewer is connected.
                if self.stream_queue is not None:
                    has_viewers = True
                    if self.viewer_counts is not None:
                        try:
                            has_viewers = self.viewer_counts.get(self.camera_id, 0) > 0
                        except Exception:
                            has_viewers = True

                    if not has_viewers:
                        metrics.incr("stream_skip_no_viewers")
                    else:
                        if self.stream_queue.full():
                            metrics.incr("stream_queue_drops")
                            try:
                                self.stream_queue.get_nowait()
                            except Exception:
                                pass
                        try:
                            self.stream_queue.put_nowait(frame)
                        except Exception:
                            metrics.incr("stream_queue_drops")

        finally:
            grabber.stop()
            if frame_pool is not None:
                frame_pool.close()
            print(f"[STOPPED] {self.camera_id}")
