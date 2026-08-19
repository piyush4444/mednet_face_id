import cv2
import threading
import time


# Exponential backoff bounds for reconnect attempts.
# Sequence: 2s → 4s → 8s → 16s → 30s (capped) → 30s → ...
# Keeps the retry hammer away from a flapping camera/switch while still
# being quick enough to recover when the camera comes back.
_RETRY_INITIAL_S = 2.0
_RETRY_MAX_S = 30.0
_RETRY_FACTOR = 2.0


class FrameGrabber:
    """Background thread that continuously grabs frames from camera."""
    def __init__(self, cam):
        self.cam = cam
        self.cap = None
        self.frame = None
        self.frame_ts = 0  # Timestamp of current frame
        self.running = False
        self.reconnect_flag = False
        # Current backoff delay in seconds — doubles on consecutive
        # failed opens, resets to _RETRY_INITIAL_S on the first successful
        # frame after a reconnect.
        self._retry_delay = _RETRY_INITIAL_S

    def open(self):
        """Open camera connection based on type."""
        cam_id = self.cam["camera_id"]
        try:
            if self.cam["type"] == "usb":
                print(f"[GRABBER] {cam_id} opening USB index={self.cam['source']}")
                self.cap = cv2.VideoCapture(int(self.cam["source"]), cv2.CAP_DSHOW)
            else:  # rtsp
                # The "?stimeout=..." query string is part of the URL, not
                # an ffmpeg option — ffmpeg ignores it. And setting
                # CAP_PROP_OPEN_TIMEOUT_MSEC *after* construction is too
                # late: the connect attempt has already happened (or hung).
                # Pass timeouts via the params overload so an unreachable
                # camera fails fast instead of blocking the worker forever.
                rtsp_url = self.cam["source"]
                print(f"[GRABBER] {cam_id} opening RTSP {rtsp_url}")
                params = [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
                ]
                try:
                    self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG, params)
                except TypeError:
                    # Older OpenCV builds don't accept the params overload.
                    self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    self.cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if self.cap is None or not self.cap.isOpened():
                print(f"[GRABBER ERROR] {cam_id} open failed (cap not opened)")
                self.cap = None
                return

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            print(f"[GRABBER] {cam_id} opened OK")
        except Exception as e:
            print(f"[GRABBER ERROR] {cam_id} open failed: {e}")
            self.cap = None

    def start(self):
        """Start the frame grabber thread."""
        self.open()
        self.running = True
        threading.Thread(target=self.update, daemon=True).start()
        print(f"[GRABBER] {self.cam['camera_id']} started")

    def _bump_backoff(self):
        """Double the retry delay, capped at _RETRY_MAX_S."""
        self._retry_delay = min(self._retry_delay * _RETRY_FACTOR, _RETRY_MAX_S)

    def _reset_backoff(self):
        """Reset to the initial delay after a successful connection."""
        self._retry_delay = _RETRY_INITIAL_S

    def update(self):
        """Background loop: continuously grab frames."""
        fail_count = 0

        while self.running:
            # Check if camera is dead — wait `_retry_delay` then reopen.
            # Delay doubles on each consecutive failed open so we stop
            # hammering a down camera / switch.
            if self.cap is None or not self.cap.isOpened():
                print(
                    f"[GRABBER] {self.cam['camera_id']} reopening "
                    f"(retry in {self._retry_delay:.1f}s)..."
                )
                self.frame = None
                self.frame_ts = 0
                time.sleep(self._retry_delay)
                self.open()
                fail_count = 0
                if self.cap is None or not self.cap.isOpened():
                    self._bump_backoff()
                continue

            ret, frame = self.cap.read()

            if not ret:
                fail_count += 1

                # RTSP: reconnect after 3 failures
                if self.cam["type"] == "rtsp" and fail_count >= 3:
                    print(
                        f"[GRABBER RECONNECT] {self.cam['camera_id']} "
                        f"(retry in {self._retry_delay:.1f}s)"
                    )
                    self.reconnect_flag = True
                    try:
                        self.cap.release()
                    except:
                        pass
                    self.frame = None
                    self.frame_ts = 0
                    time.sleep(self._retry_delay)
                    self.open()
                    fail_count = 0
                    if self.cap is None or not self.cap.isOpened():
                        self._bump_backoff()

                continue

            # Got a valid frame — reset retry delay so the NEXT failure
            # starts at _RETRY_INITIAL_S again.
            if self._retry_delay != _RETRY_INITIAL_S:
                self._reset_backoff()
            fail_count = 0
            self.frame = frame
            self.frame_ts = time.time()

    def get_frame(self):
        """Get latest frame with timestamp (non-blocking)."""
        return self.frame, self.frame_ts

    def has_reconnected(self):
        """Check if reconnection happened. Clears flag on read."""
        if self.reconnect_flag:
            self.reconnect_flag = False
            return True
        return False

    def stop(self):
        """Stop the grabber."""
        self.running = False
        if self.cap:
            try:
                self.cap.release()
            except:
                pass
