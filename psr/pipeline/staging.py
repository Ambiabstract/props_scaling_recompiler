"""Operation-scoped staging lifecycle for source and generated artifacts."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from psr.assets import OrderedAssetFileSystem, SourceAssetMetadata

from .qc import QCOperationPlan


_MARKER_NAME = ".psr-staging"


class StagingError(RuntimeError):
    """A categorised failure confined to a PSR-owned staging workspace."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class StagedFile:
    relative_path: str
    physical_path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StagedSourceModel:
    logical_model_path: str
    physical_model_path: Path
    files: tuple[StagedFile, ...]


class StagingWorkspace:
    """A unique temporary root which can only clean up its own marked tree."""

    def __init__(self, parent: Path, root: Path, *, preserve: bool) -> None:
        self.parent = parent
        self.root = root
        self.preserve = preserve
        self._closed = False

    @classmethod
    def create(
        cls,
        parent: Path,
        *,
        operation_identity: str,
        preserve: bool = False,
    ) -> StagingWorkspace:
        if not operation_identity.strip():
            raise ValueError("operation_identity must not be empty")
        parent = parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(operation_identity.encode("utf-8")).hexdigest()[:12]
        root = Path(tempfile.mkdtemp(prefix=f"psr-{identity}-", dir=parent)).resolve()
        (root / _MARKER_NAME).write_bytes(operation_identity.encode("utf-8"))
        for name in ("source", "decompiled", "qc", "game"):
            (root / name).mkdir()
        return cls(parent, root, preserve=preserve)

    @property
    def is_open(self) -> bool:
        return not self._closed

    def path(self, relative_path: str) -> Path:
        if self._closed:
            raise StagingError("staging_closed", "workspace is already closed")
        relative = _validate_relative_path(relative_path)
        physical = self.root.joinpath(*relative.parts).resolve()
        if physical != self.root and self.root not in physical.parents:
            raise StagingError("staging_path_escape", relative_path)
        return physical

    def write_bytes(self, relative_path: str, content: bytes) -> StagedFile:
        physical = self.path(relative_path)
        physical.parent.mkdir(parents=True, exist_ok=True)
        if physical.exists():
            existing = physical.read_bytes()
            if existing != content:
                raise StagingError(
                    "staging_content_conflict",
                    f"different content already exists at {relative_path}",
                )
        else:
            physical.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        return StagedFile(
            relative_path=PurePosixPath(relative_path).as_posix(),
            physical_path=physical,
            size=len(content),
            sha256=digest,
        )

    def cleanup(self) -> None:
        if self._closed:
            return
        marker = self.root / _MARKER_NAME
        if self.root.parent != self.parent or not marker.is_file():
            raise StagingError(
                "staging_cleanup_refused",
                f"unmarked or unexpected staging root: {self.root}",
            )
        shutil.rmtree(self.root)
        self._closed = True

    def __enter__(self) -> StagingWorkspace:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.preserve:
            self.cleanup()


def stage_source_model(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    source: SourceAssetMetadata,
) -> StagedSourceModel:
    """Materialise exactly the source files fingerprinted during discovery."""
    staged: list[StagedFile] = []
    model_path: Path | None = None
    for expected in source.files:
        try:
            resolved = filesystem.resolve(expected.logical_path)
        except FileNotFoundError as exc:
            raise StagingError(
                "staging_source_missing",
                f"planned source disappeared: {expected.logical_path}",
            ) from exc
        if resolved.provenance != expected.provenance:
            raise StagingError(
                "staging_source_provenance_changed",
                f"resolution changed for {expected.logical_path}",
            )
        content = resolved.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != expected.size or digest != expected.sha256:
            raise StagingError(
                "staging_source_content_changed",
                f"content changed after planning: {expected.logical_path}",
            )
        item = workspace.write_bytes(f"source/{expected.logical_path}", content)
        staged.append(item)
        if expected.logical_path == source.logical_model_path:
            model_path = item.physical_path
    if model_path is None:
        raise StagingError(
            "staging_source_model_unlisted",
            f"source metadata omits {source.logical_model_path}",
        )
    return StagedSourceModel(source.logical_model_path, model_path, tuple(staged))


def stage_qc_operation(
    workspace: StagingWorkspace,
    plan: QCOperationPlan,
) -> tuple[StagedFile, ...]:
    """Write a valid in-memory QC plan under the workspace QC namespace."""
    if not plan.is_valid:
        raise StagingError("staging_qc_plan_invalid", plan.map_identity)
    staged: list[StagedFile] = []
    for artifact in (*plan.references, *plan.variants):
        digest = hashlib.sha256(artifact.content).hexdigest()
        if digest != artifact.output_qc_sha256:
            raise StagingError(
                "staging_qc_hash_mismatch",
                artifact.staging_relative_path,
            )
        staged.append(workspace.write_bytes(
            f"qc/{artifact.staging_relative_path}",
            artifact.content,
        ))
    return tuple(staged)


def _validate_relative_path(value: str) -> PurePosixPath:
    normalised = value.replace("\\", "/")
    path = PurePosixPath(normalised)
    if (
        not normalised
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise StagingError("staging_path_invalid", value)
    return path


__all__ = [
    "StagedFile",
    "StagedSourceModel",
    "StagingError",
    "StagingWorkspace",
    "stage_qc_operation",
    "stage_source_model",
]
