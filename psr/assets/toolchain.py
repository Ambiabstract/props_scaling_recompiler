"""Narrow subprocess adapters for the Source SDK 2013 SP model toolchain.

The adapters deliberately do not decide what should be generated or commit any
outputs.  They execute one already-planned operation inside caller-owned
staging directories and return byte-preserving process output for diagnostics.
"""

from __future__ import annotations

import hashlib
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .searchpaths import normalize_logical_path


_STATIC_PROP_FLAG = 0x10
_BONE_COUNT_OFFSET = 156
_SUPPORTED_MDL_VERSIONS = range(44, 50)
_DEFAULT_MODEL_EXTENSIONS = (
    ".mdl",
    ".vvd",
    ".dx80.vtx",
    ".dx90.vtx",
    ".sw.vtx",
)
_ALLOWED_MODEL_EXTENSIONS = frozenset((*_DEFAULT_MODEL_EXTENSIONS, ".phy"))


class ToolExecutionError(RuntimeError):
    """A categorised failure to start or complete an external tool."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        invocation: ToolInvocation | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.invocation = invocation
        super().__init__(f"{code}: {detail}")


class CompiledModelValidationError(RuntimeError):
    """A categorised failure in staged compiler output validation."""

    def __init__(self, code: str, logical_path: str, detail: str) -> None:
        self.code = code
        self.logical_path = logical_path
        self.detail = detail
        super().__init__(f"{code}: {logical_path}: {detail}")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Exact argv and captured result for one process invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CrowbarDecompileResult:
    """A successful, unambiguous Crowbar decompile in an isolated directory."""

    invocation: ToolInvocation
    output_directory: Path
    qc_path: Path
    relative_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledFileMetadata:
    """Identity of one validated file emitted by StudioMDL."""

    logical_path: str
    physical_path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompiledModelValidation:
    """Validated staged model identity and its required companions."""

    logical_model_path: str
    internal_model_name: str
    mdl_version: int
    is_static_prop: bool
    files: tuple[CompiledFileMetadata, ...]


def run_crowbar_decompile(
    command: Sequence[str | Path],
    *,
    model_path: Path,
    output_directory: Path,
    timeout_seconds: float = 300.0,
) -> CrowbarDecompileResult:
    """Run Crowbar with argument-list semantics and locate its single QC."""
    model_path = model_path.resolve()
    output_directory = output_directory.resolve()
    if not model_path.is_file():
        raise ToolExecutionError("crowbar_model_missing", str(model_path))
    if output_directory.exists():
        if not output_directory.is_dir():
            raise ToolExecutionError(
                "crowbar_output_not_directory",
                f"decompile output path is not a directory: {output_directory}",
            )
        if any(output_directory.iterdir()):
            raise ToolExecutionError(
                "crowbar_output_not_empty",
                f"decompile output directory is not empty: {output_directory}",
            )
    output_directory.mkdir(parents=True, exist_ok=True)

    argv = _normalise_command(command) + (
        "-p",
        str(model_path),
        "-o",
        str(output_directory),
    )
    invocation = _run(argv, cwd=output_directory, timeout_seconds=timeout_seconds)
    if invocation.returncode != 0:
        raise ToolExecutionError(
            "crowbar_failed",
            f"Crowbar exited with code {invocation.returncode}",
            invocation=invocation,
        )

    files = tuple(sorted(
        (
            path.relative_to(output_directory).as_posix()
            for path in output_directory.rglob("*")
            if path.is_file()
        ),
        key=lambda value: (value.casefold(), value),
    ))
    qcs = tuple(path for path in files if path.casefold().endswith(".qc"))
    if not qcs:
        raise ToolExecutionError(
            "crowbar_qc_missing",
            "Crowbar succeeded but emitted no QC",
            invocation=invocation,
        )
    if len(qcs) != 1:
        raise ToolExecutionError(
            "crowbar_qc_ambiguous",
            f"Crowbar emitted {len(qcs)} QC files: {qcs!r}",
            invocation=invocation,
        )
    return CrowbarDecompileResult(
        invocation=invocation,
        output_directory=output_directory,
        qc_path=output_directory.joinpath(*PurePosixPath(qcs[0]).parts),
        relative_files=files,
    )


def run_studiomdl_compile(
    command: Sequence[str | Path],
    *,
    game_directory: Path,
    qc_path: Path,
    timeout_seconds: float = 300.0,
) -> ToolInvocation:
    """Compile one staged QC without treating log text as proof of success."""
    game_directory = game_directory.resolve()
    qc_path = qc_path.resolve()
    if not game_directory.is_dir():
        raise ToolExecutionError("studiomdl_game_missing", str(game_directory))
    if not qc_path.is_file():
        raise ToolExecutionError("studiomdl_qc_missing", str(qc_path))
    argv = _normalise_command(command) + (
        "-game",
        str(game_directory),
        "-nop4",
        "-verbose",
        str(qc_path),
    )
    invocation = _run(argv, cwd=qc_path.parent, timeout_seconds=timeout_seconds)
    if invocation.returncode != 0:
        raise ToolExecutionError(
            "studiomdl_failed",
            f"StudioMDL exited with code {invocation.returncode}",
            invocation=invocation,
        )
    return invocation


def validate_compiled_model(
    game_directory: Path,
    logical_model_path: str,
    *,
    requires_physics: bool,
    requires_static_conversion: bool = False,
    required_extensions: Sequence[str] = _DEFAULT_MODEL_EXTENSIONS,
) -> CompiledModelValidation:
    """Validate a staged managed model and every explicitly expected file."""
    try:
        logical_path = normalize_logical_path(logical_model_path)
    except ValueError as exc:
        raise CompiledModelValidationError(
            "compiled_model_path_invalid", logical_model_path, str(exc)
        ) from exc
    if not logical_path.startswith("models/psr_scaled/") or not logical_path.endswith(".mdl"):
        raise CompiledModelValidationError(
            "compiled_model_path_unmanaged",
            logical_path,
            "compiled output must be a managed models/psr_scaled/**/*.mdl path",
        )

    extensions = _normalise_extensions(required_extensions, requires_physics)
    base = PurePosixPath(logical_path)
    metadata: list[CompiledFileMetadata] = []
    mdl_header: bytes | None = None
    for extension in extensions:
        companion = str(base.with_suffix(extension))
        physical = game_directory.resolve().joinpath(*PurePosixPath(companion).parts)
        if not physical.is_file():
            raise CompiledModelValidationError(
                "compiled_companion_missing",
                logical_path,
                f"required output is missing: {companion}",
            )
        size = physical.stat().st_size
        if size == 0:
            raise CompiledModelValidationError(
                "compiled_companion_empty",
                logical_path,
                f"required output is empty: {companion}",
            )
        if extension == ".mdl":
            with physical.open("rb") as stream:
                mdl_header = stream.read(_BONE_COUNT_OFFSET + 4)
        metadata.append(CompiledFileMetadata(
            logical_path=companion,
            physical_path=physical,
            size=size,
            sha256=_file_sha256(physical),
        ))

    assert mdl_header is not None
    version, internal_name, is_static = _inspect_compiled_mdl_header(
        mdl_header,
        logical_path,
    )
    expected_name = logical_path.removeprefix("models/")
    try:
        # studiohdr_t::name is a 64-byte C string. StudioMDL therefore keeps
        # at most 63 ASCII bytes even though it still emits the output under
        # the complete $modelname filesystem path.
        expected_header_name = expected_name.encode("ascii")[:63].decode("ascii")
    except UnicodeEncodeError as exc:
        raise CompiledModelValidationError(
            "compiled_modelname_encoding",
            logical_path,
            "expected MDL header model name is not ASCII",
        ) from exc
    if _normalise_internal_name(internal_name) != expected_header_name.casefold():
        raise CompiledModelValidationError(
            "compiled_modelname_mismatch",
            logical_path,
            (
                f"MDL header names {internal_name!r}, expected StudioMDL-representable "
                f"name {expected_header_name!r} for {expected_name!r}"
            ),
        )
    if not is_static:
        raise CompiledModelValidationError(
            "compiled_model_not_static",
            logical_path,
            "generated MDL does not have the static-prop flag",
        )
    if requires_static_conversion:
        _validate_static_conversion_bones(mdl_header, logical_path)
    return CompiledModelValidation(
        logical_model_path=logical_path,
        internal_model_name=internal_name,
        mdl_version=version,
        is_static_prop=is_static,
        files=tuple(metadata),
    )


def _run(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> ToolInvocation:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        invocation = ToolInvocation(
            argv,
            -1,
            exc.stdout or b"",
            exc.stderr or b"",
        )
        raise ToolExecutionError(
            "tool_timeout",
            f"tool exceeded {timeout_seconds:g} seconds",
            invocation=invocation,
        ) from exc
    except OSError as exc:
        raise ToolExecutionError(
            "tool_start_failed",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    return ToolInvocation(tuple(argv), result.returncode, result.stdout, result.stderr)


def _normalise_command(command: Sequence[str | Path]) -> tuple[str, ...]:
    argv = tuple(str(item) for item in command)
    if not argv or not argv[0]:
        raise ValueError("tool command must not be empty")
    executable = Path(argv[0])
    if (executable.is_absolute() or executable.parent != Path(".")) and not executable.is_file():
        raise ToolExecutionError("tool_executable_missing", argv[0])
    return argv


def _normalise_extensions(
    required_extensions: Sequence[str],
    requires_physics: bool,
) -> tuple[str, ...]:
    result: list[str] = []
    for raw in required_extensions:
        extension = raw.casefold()
        if extension not in _ALLOWED_MODEL_EXTENSIONS:
            raise ValueError(f"invalid companion extension: {raw!r}")
        if extension not in result:
            result.append(extension)
    if ".mdl" not in result:
        result.insert(0, ".mdl")
    if requires_physics and ".phy" not in result:
        result.append(".phy")
    return tuple(result)


def _inspect_compiled_mdl_header(data: bytes, logical_path: str) -> tuple[int, str, bool]:
    if len(data) < 156:
        raise CompiledModelValidationError(
            "compiled_mdl_truncated", logical_path, f"MDL is only {len(data)} bytes"
        )
    if data[:4] != b"IDST":
        raise CompiledModelValidationError(
            "compiled_mdl_signature", logical_path, "MDL signature is not IDST"
        )
    version = struct.unpack_from("<i", data, 4)[0]
    if version not in _SUPPORTED_MDL_VERSIONS:
        raise CompiledModelValidationError(
            "compiled_mdl_version",
            logical_path,
            f"unsupported MDL version {version}",
        )
    raw_name = data[12:76].split(b"\0", 1)[0]
    try:
        internal_name = raw_name.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CompiledModelValidationError(
            "compiled_modelname_encoding",
            logical_path,
            "MDL header model name is not ASCII",
        ) from exc
    flags = struct.unpack_from("<I", data, 152)[0]
    return version, internal_name, bool(flags & _STATIC_PROP_FLAG)


def _validate_static_conversion_bones(data: bytes, logical_path: str) -> None:
    if len(data) < _BONE_COUNT_OFFSET + 4:
        raise CompiledModelValidationError(
            "compiled_mdl_truncated",
            logical_path,
            "MDL header does not contain numbones",
        )
    bone_count = struct.unpack_from("<i", data, _BONE_COUNT_OFFSET)[0]
    if bone_count != 1:
        raise CompiledModelValidationError(
            "compiled_static_conversion_bones",
            logical_path,
            (
                "dynamic-to-static output must contain exactly one render bone at "
                f"index 0, found {bone_count} bones"
            ),
        )


def _normalise_internal_name(value: str) -> str:
    return value.replace("\\", "/").lstrip("/").casefold()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CompiledFileMetadata",
    "CompiledModelValidation",
    "CompiledModelValidationError",
    "CrowbarDecompileResult",
    "ToolExecutionError",
    "ToolInvocation",
    "run_crowbar_decompile",
    "run_studiomdl_compile",
    "validate_compiled_model",
]
