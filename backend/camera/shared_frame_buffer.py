"""
shared_frame_buffer.py — Zero-copy frame transport between CameraWorker
and AIWorker processes.

Background
----------
Previously CameraWorker pushed full numpy BGR frames into a
multiprocessing.Queue. Under the hood, Python pickled each array, sent it
through a pipe, and the consumer unpickled it. At ~1.5 MB per 960-px frame
and 4 cameras × 6 FPS, that is ~36 MB/s of pure serialization churn —
pure CPU waste, and the main cause of AI-pipeline latency.

Design
------
A pool of N fixed-size SharedMemory blocks plus a **free-list** of
unused slot indices, shared across processes via multiprocessing.Queue.

    CameraWorker.run():
        slot = pool.acquire_slot()       # pops a free index; None if exhausted
        pool.write(slot, frame)          # memcpy into shared buffer
        frame_queue.put({..., "slot": slot, "shape": frame.shape,
                                "dtype": str(frame.dtype)})

    AIWorker.run():
        task = frame_queue.get()
        try:
            frame = pool.view(task["slot"], task["shape"], task["dtype"])
            run_pipeline(frame, ...)      # zero-copy numpy view
        finally:
            pool.release_slot(task["slot"])

Why a free-list and not round-robin
-----------------------------------
Round-robin depends on timing: "consumer finishes slot X before producer
wraps back to X". If the pipeline stalls (GPU OOM, model reload, GC
pause, log back-pressure), the producer can overwrite a slot the
consumer is still reading — silent frame corruption. A free-list makes
the invariant structural: a slot is only reachable to a writer after
it's been explicitly released, so correctness no longer hinges on rate
assumptions.

Sizing
------
Steady-state slot demand = frame_queue.maxsize (in-flight frames) +
num_cameras (one in each producer between acquire and enqueue) +
num_ai_workers (one being processed per worker). With maxsize=40,
4 cameras, 1 worker → 45. We allocate 64 with ~40% headroom.

Each slot holds the worst-case frame after downscale. At
PROCESS_MAX_DIM the worst square frame is D×D×3 bytes; we pad ×1.15 for
safety so padded/custom resolutions don't blow up.

Failure modes
-------------
- All slots held (rare): acquire_slot returns None, producer drops the
  frame. Surfaces as an "shm_pool_exhausted" metric spike.
- Producer crash between acquire and enqueue: slot is leaked until the
  owning pool is destroyed. With 64 slots and infrequent crashes,
  acceptable; fully correct recovery would need reference-counted slots
  (future work).
- Consumer crash after get but before release: likewise leaked until
  teardown.

Cleanup
-------
The master process (manager.start_camera_system) owns the SharedMemory
blocks and is responsible for `unlink()`-ing them on shutdown. A
best-effort atexit handler runs ``destroy()`` if the process exits
without the manager calling it explicitly.
"""

from __future__ import annotations

import atexit
import os
import queue as _queue
import uuid
from multiprocessing import Queue as MPQueue
from multiprocessing import shared_memory

import numpy as np


def _default_slot_bytes() -> int:
    """Derive slot size from config.PROCESS_MAX_DIM so raising the max
    resolution doesn't silently blow past a hardcoded ceiling."""
    try:
        import backend.config as config

        max_dim = int(getattr(config, "PROCESS_MAX_DIM", 960))
    except Exception:
        max_dim = 960
    # Worst-case square BGR frame × 1.15 safety for non-square/custom sizes.
    return int(max_dim * max_dim * 3 * 1.15)


DEFAULT_NUM_SLOTS = 64


class FrameBufferHandle:
    """
    Picklable descriptor for a FrameBufferPool. Child processes receive
    this via Process args and call ``.attach()`` to get a per-process
    view into the same shared-memory blocks.
    """

    __slots__ = ("names", "slot_bytes", "num_slots", "free_slots")

    def __init__(self, names, slot_bytes: int, free_slots: MPQueue):
        self.names = list(names)
        self.slot_bytes = slot_bytes
        self.num_slots = len(names)
        self.free_slots = free_slots  # multiprocessing.Queue — shared

    def attach(self) -> "FrameBufferAttach":
        return FrameBufferAttach(self)


class FrameBufferAttach:
    """
    Per-process attachment to the shared-memory pool. One instance lives
    inside each camera / AI worker. Holds open handles to every slot so
    reads and writes are straight memcpys against a pre-mapped region.
    """

    def __init__(self, handle: FrameBufferHandle):
        self._handle = handle
        self._blocks = [
            shared_memory.SharedMemory(name=n) for n in handle.names
        ]
        self._slot_bytes = handle.slot_bytes
        self._free_slots = handle.free_slots

    def acquire_slot(self) -> int | None:
        """Pop a free slot index, or return None if the pool is exhausted.

        Non-blocking: producers never wait on the pool. If all slots are
        held, the caller must drop the frame. This is the structural
        safety property — a slot is only reachable to a writer after
        it's been released."""
        try:
            return self._free_slots.get_nowait()
        except _queue.Empty:
            return None

    def release_slot(self, slot: int) -> None:
        """Return a slot to the free list. Must be called exactly once
        per successful acquire_slot, or the pool leaks capacity."""
        try:
            self._free_slots.put_nowait(slot)
        except _queue.Full:
            # Bounded-size queue matches num_slots, so Full implies a
            # double-release bug. Swallow to avoid a derivative crash;
            # log would be nice but this module is import-clean.
            pass

    def write(self, slot: int, frame: np.ndarray) -> None:
        """Copy ``frame`` bytes into the slot's shared buffer.

        Uses a numpy view over the shared memory as the destination, so
        this is a single memcpy — no intermediate allocation."""
        nbytes = frame.nbytes
        if nbytes > self._slot_bytes:
            raise ValueError(
                f"frame of {nbytes} bytes exceeds slot capacity "
                f"{self._slot_bytes}; raise DEFAULT_SLOT_BYTES or "
                f"lower PROCESS_MAX_DIM"
            )
        dst = np.ndarray(
            frame.shape, dtype=frame.dtype, buffer=self._blocks[slot].buf,
        )
        np.copyto(dst, frame)

    def view(self, slot: int, shape, dtype) -> np.ndarray:
        """Return a zero-copy numpy view into the slot's shared buffer.

        The caller must finish using the view before release_slot(slot).
        With a free-list, that invariant holds structurally: the slot
        index is not returned to any producer until the consumer calls
        release_slot."""
        if isinstance(dtype, str):
            dtype = np.dtype(dtype)
        return np.ndarray(
            tuple(shape), dtype=dtype, buffer=self._blocks[slot].buf,
        )

    def close(self) -> None:
        for shm in self._blocks:
            try:
                shm.close()
            except Exception:
                pass


class FrameBufferPool:
    """Owner of the SharedMemory blocks. Created once in the main process."""

    def __init__(
        self,
        num_slots: int = DEFAULT_NUM_SLOTS,
        slot_bytes: int | None = None,
    ):
        if slot_bytes is None:
            slot_bytes = _default_slot_bytes()

        # UUID fragment prevents collisions if a prior run crashed
        # without unlinking and the OS reused our PID.
        self._tag = f"{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self._blocks: list[shared_memory.SharedMemory] = []
        for i in range(num_slots):
            name = f"synora_frame_{self._tag}_{i}"
            shm = shared_memory.SharedMemory(
                create=True, size=slot_bytes, name=name,
            )
            self._blocks.append(shm)

        self._free_slots: MPQueue = MPQueue(maxsize=num_slots)
        for i in range(num_slots):
            self._free_slots.put_nowait(i)

        self._slot_bytes = slot_bytes
        self._num_slots = num_slots
        self._destroyed = False

        # Best-effort safety net — if the owning process exits without
        # the manager calling destroy() (unclean shutdown), still unlink
        # the SHM blocks so they don't linger in /dev/shm / named namespace.
        atexit.register(self._atexit_destroy)

    @property
    def handle(self) -> FrameBufferHandle:
        return FrameBufferHandle(
            [b.name for b in self._blocks], self._slot_bytes, self._free_slots,
        )

    @property
    def free_count(self) -> int:
        """Approximate number of free slots (best-effort on some platforms)."""
        try:
            return self._free_slots.qsize()
        except (NotImplementedError, OSError):
            return -1

    def destroy(self) -> None:
        """Close and unlink every block. Call from the owning process on
        shutdown — after child processes have been joined."""
        if self._destroyed:
            return
        self._destroyed = True
        for shm in self._blocks:
            try:
                shm.close()
            except Exception:
                pass
            try:
                shm.unlink()
            except Exception:
                pass
        self._blocks.clear()

    def _atexit_destroy(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass
