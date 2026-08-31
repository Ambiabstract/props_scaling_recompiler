"""Central console progress and heartbeat reporting for long synchronous stages."""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO


class ProgressReporter:
    """Print stage changes plus live completed/total progress and ETA."""

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
        self._completed = 0
        self._total: int | None = None
        self._unit = "items"
        self._detail = ""
        self._done_emitted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(
        self,
        stage: str,
        *,
        total: int | None = None,
        unit: str = "items",
    ) -> None:
        self.finish()
        if total is not None and total < 0:
            raise ValueError("progress total cannot be negative")
        with self._lock:
            self._stage = stage
            self._started = time.perf_counter()
            self._completed = 0
            self._total = total
            self._unit = unit
            self._detail = ""
            self._done_emitted = False
        self._write(self._message())
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
        with self._lock:
            completed = self._completed
            total = self._total
            done_emitted = self._done_emitted
            if total is not None and completed >= total and not done_emitted:
                self._done_emitted = True
        if total is not None and completed >= total and not done_emitted:
            self._write(self._message(done=True))

    def update(
        self,
        completed: int,
        *,
        total: int | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            effective_total = self._total if total is None else total
            if completed < 0 or (effective_total is not None and completed > effective_total):
                raise ValueError("progress completed count is outside its total")
            changed = completed != self._completed or effective_total != self._total
            self._completed = completed
            self._total = effective_total
            if detail is not None:
                self._detail = " ".join(detail.split())
            done = effective_total is not None and completed >= effective_total
            if changed and done:
                self._done_emitted = True
        if changed:
            self._write(self._message(done=done))

    def close(self) -> None:
        self.finish()

    def __enter__(self) -> "ProgressReporter":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            self._write(self._message())

    def _message(self, *, done: bool = False) -> str:
        with self._lock:
            stage = self._stage
            started = self._started
            completed = self._completed
            total = self._total
            unit = self._unit
            detail = self._detail
        elapsed = max(0.0, time.perf_counter() - started)
        parts = [f"[PROGRESS] {stage}"]
        if total is not None:
            percent = 100 if total == 0 else int(completed * 100 / total)
            parts.append(f"{completed}/{total} {unit} ({percent}%)")
            if done:
                parts.append("done")
            elif completed > 0 and completed < total:
                eta = elapsed * (total - completed) / completed
                parts.append(f"ETA {_format_duration(eta)}")
            elif completed == 0 and total > 0:
                parts.append("ETA calculating")
        if detail:
            parts.append(f"current: {detail}")
        parts.append(f"elapsed {_format_duration(elapsed)}")
        return " — ".join(parts)

    def _write(self, message: str) -> None:
        with self._lock:
            self.stream.write(message + "\n")
            self.stream.flush()


class NullProgressReporter:
    """No-op implementation used unless interactive UX output is requested."""

    def start(
        self,
        _stage: str,
        *,
        total: int | None = None,
        unit: str = "items",
    ) -> None:
        pass

    def update(
        self,
        _completed: int,
        *,
        total: int | None = None,
        detail: str | None = None,
    ) -> None:
        pass

    def finish(self) -> None:
        pass

    def close(self) -> None:
        pass


def _format_duration(seconds: float) -> str:
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


__all__ = ["NullProgressReporter", "ProgressReporter"]
