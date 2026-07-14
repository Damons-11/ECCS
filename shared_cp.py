import threading

class SharedChargePoint:
    def __init__(self):
        self._cp = None
        self._lock = threading.Lock()

    def set(self, cp):
        with self._lock:
            self._cp = cp

    def get(self):
        with self._lock:
            return self._cp

shared_cp = SharedChargePoint()
