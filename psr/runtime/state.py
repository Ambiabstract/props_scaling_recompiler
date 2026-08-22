"""Project-scoped LocalAppData paths and an automatically released file lock."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from psr.cache import ProjectIdentity


_APPLICATION_DIRECTORY = "PropsScalingRecompiler"


class ProjectLockError(RuntimeError):
    """Raised when another PSR process already owns the project lock."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class ProjectStatePaths:
    """All persistent/transient runtime paths for one project identity."""

    root: Path
    manifest: Path
    lock: Path
    recovery_journal: Path
    logs: Path
    staging: Path
    failed_runs: Path

    def ensure_directories(self) -> None:
        for path in (self.root, self.logs, self.staging, self.failed_runs):
            path.mkdir(parents=True, exist_ok=True)


def build_project_state_paths(
    project: ProjectIdentity,
    *,
    local_appdata: Path | None = None,
) -> ProjectStatePaths:
    """Resolve the approved ``%LOCALAPPDATA%`` layout without writing it."""
    if local_appdata is None:
        raw = os.environ.get("LOCALAPPDATA")
        if not raw:
            raise ProjectLockError(
                "local_appdata_missing",
                "LOCALAPPDATA is not defined; PSR requires Windows 10/11 user state",
            )
        local_appdata = Path(raw)
    application_root = local_appdata.resolve() / _APPLICATION_DIRECTORY
    base = application_root / "projects"
    root = base / project.project_id
    # Crowbar 0.68 still uses the legacy Win32 path limit. Keeping operation
    # staging below the full 64-character project-id directory makes room for
    # original model subpaths and generated QC/SMD names. The full identity,
    # lock, manifest, and recovery journal remain in ``root``; staging roots
    # are unique and marker-protected, so the short routing key is not an
    # identity or ownership boundary.
    staging = application_root / "work" / project.project_id[:16]
    return ProjectStatePaths(
        root=root,
        manifest=root / "manifest.json",
        lock=root / "operation.lock",
        recovery_journal=root / "recovery.json",
        logs=root / "logs",
        staging=staging,
        failed_runs=root / "failed_runs",
    )


class ProjectLock:
    """One non-blocking OS lock shared by all maps of a project.

    The lock file remains as harmless metadata, while the byte-range/flock is
    released automatically by the OS if the process crashes.
    """

    def __init__(self, path: Path, *, map_identity: str) -> None:
        self.path = path.resolve()
        self.map_identity = map_identity
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        if self._stream is not None:
            raise ProjectLockError("project_lock_reentrant", str(self.path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = _open_lock_file(self.path)
        try:
            _lock_stream(stream)
        except OSError as exc:
            holder = _read_holder(stream)
            stream.close()
            detail = f"another PSR process owns {self.path}"
            if holder:
                detail += f" ({holder})"
            raise ProjectLockError("project_locked", detail) from exc

        metadata = {
            "pid": os.getpid(),
            "map_identity": self.map_identity,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "executable": str(Path(sys.executable).resolve()),
        }
        stream.seek(1)
        stream.truncate()
        stream.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            _unlock_stream(stream)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _open_lock_file(path: Path) -> BinaryIO:
    try:
        stream = path.open("r+b")
    except FileNotFoundError:
        try:
            stream = path.open("x+b")
        except FileExistsError:
            stream = path.open("r+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b" ")
        stream.flush()
    return stream


def _lock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # Allows isolated development tests without changing product scope.
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_holder(stream: BinaryIO) -> str:
    try:
        stream.seek(1)
        raw = stream.read().decode("utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    return ", ".join(
        f"{key}={value[key]}"
        for key in ("pid", "map_identity", "started_utc")
        if key in value
    )


__all__ = [
    "ProjectLock",
    "ProjectLockError",
    "ProjectStatePaths",
    "build_project_state_paths",
]
