"""Central console progress and heartbeat reporting for long synchronous stages."""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO


class ProgressReporter:
    """Print concise stage changes and periodic heartbeats from one owner."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.heartbeat_seconds = heartbeat_seconds
        self._stage = ""
        self._started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self, stage: str) -> None:
        self.finish()
        self._stage = stage
        self._started = time.perf_counter()
        self._write(f"[PROGRESS] {stage}")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat,
            name="psr-progress-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def finish(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=min(self.heartbeat_seconds, 0.25))
        self._thread = None

    def close(self) -> None:
        self.finish()

    def __enter__(self) -> "ProgressReporter":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            elapsed = time.perf_counter() - self._started
            self._write(f"[PROGRESS] {self._stage} — still working ({elapsed:.0f}s)")

    def _write(self, message: str) -> None:
        with self._lock:
            self.stream.write(message + "\n")
            self.stream.flush()


class NullProgressReporter:
    """No-op implementation used unless interactive UX output is requested."""

    def start(self, _stage: str) -> None:
        pass

    def finish(self) -> None:
        pass

    def close(self) -> None:
        pass


__all__ = ["NullProgressReporter", "ProgressReporter"]
