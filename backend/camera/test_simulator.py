"""
Test simulator — must be spawned as a child of manager.py so it
shares the event_queue (passed in via constructor).

Run with:
    python -m backend.camera.manager --test
"""

import time
from multiprocessing import Process


class TestSimulator(Process):
    def __init__(self, event_queue):
        super().__init__()
        self.event_queue = event_queue

    def send_event(self, camera_type, user_id, name):
        event = {
            "camera_id": "cam_test",
            "floor": "floor_1",
            "type": camera_type,
            "timestamp": time.time(),
            "faces": [
                {
                    "user_id": user_id,
                    "identity": name,
                    "confidence": 0.9,
                }
            ],
        }
        self.event_queue.put(event)
        print(f"[TEST] Sent {camera_type} for {name}")

    def run(self):
        time.sleep(2)

        # ENTRY
        self.send_event("ENTRY", 1, "Piyush")
        time.sleep(1)

        # duplicate (should be ignored due to cooldown)
        self.send_event("ENTRY", 1, "Piyush")
        time.sleep(6)

        # update (after cooldown)
        self.send_event("ENTRY", 1, "Piyush")
        time.sleep(1)

        # EXIT
        self.send_event("EXIT", 1, "Piyush")
