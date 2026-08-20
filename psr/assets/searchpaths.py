"""Ordered GameInfo SearchPath resolution built on top of :mod:`srctools`.

The high-level ``srctools.game.Game`` helper intentionally implements more
engine policy than PSR needs and groups filesystem types together.  PSR keeps
the source order itself, then mounts one ``srctools`` filesystem per expanded
entry so the first exact logical-path match always wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from srctools.filesys import FileSystemChain, RawFileSystem, VPKFileSystem
from srctools.keyvalues import Keyvalues


_GAMEINFO_TOKEN = "|gameinfo_path|"
_ENGINE_TOKEN = "|all_source_engine_paths|"
_NUMBERED_VPK = re.compile(r"_[0-9]{3}\.vpk$", re.IGNORECASE)


class SearchPathParseError(ValueError):
    """Raised when GameInfo does not contain a usable SearchPaths block."""


@dataclass(frozen=True, slots=True)
class SearchPathSpec:
    """One unexpanded leaf from GameInfo's SearchPaths block."""

    ordinal: int
    path_id: str
    raw_value: str
    line_number: int


@dataclass(frozen=True, slots=True)
class MountedSearchPath:
    """One concrete folder or directory VPK mounted in source order."""

    mount_index: int
    source_ordinal: int
    expansion_index: int
    path_id: str
    raw_value: str
    kind: str
    container_path: Path


@dataclass(frozen=True, slots=True)
class SearchPathDiagnostic:
    """A non-fatal reason why a SearchPath entry produced no mount."""

    source_ordinal: int
    path_id: str
    raw_value: str
    reason: str
    candidate_path: Path | None = None


@dataclass(frozen=True, slots=True)
class SearchPathPlan:
    """Deterministic, side-effect-free result of expanding SearchPaths."""

    specs: tuple[SearchPathSpec, ...]
    mounts: tuple[MountedSearchPath, ...]
    diagnostics: tuple[SearchPathDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class AssetProvenance:
    """The precise SearchPath source that won resolution for an asset."""

    logical_path: str
    mount_index: int
    source_ordinal: int
    expansion_index: int
    path_id: str
    raw_value: str
    kind: str
    container_path: Path


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """A resolved srctools file together with stable PSR provenance."""

    provenance: AssetProvenance
    file: Any = field(repr=False, compare=False)

    def open_bin(self) -> BinaryIO:
        """Open the resolved asset without extracting it to a temporary file."""
        return self.file.open_bin()

    def read_bytes(self) -> bytes:
        """Read the complete resolved asset."""
        with self.open_bin() as stream:
            return stream.read()


def parse_gameinfo_search_paths(
    gameinfo_path: Path,
    *,
    encoding: str = "utf-8-sig",
) -> tuple[SearchPathSpec, ...]:
    """Parse ordered SearchPaths leaves from a GameInfo file."""
    text = gameinfo_path.read_text(encoding=encoding)
    return parse_search_paths_text(text, filename=str(gameinfo_path))


def parse_search_paths_text(
    text: str,
    *,
    filename: str = "<GameInfo>",
) -> tuple[SearchPathSpec, ...]:
    """Parse SearchPaths while preserving duplicate keys and source order."""
    try:
        root = Keyvalues.parse(text, filename, allow_escapes=False)
        gameinfo = root.find_key("GameInfo")
        filesystem = gameinfo.find_key("FileSystem")
        search_paths = filesystem.find_key("SearchPaths")
    except (KeyError, ValueError) as exc:
        raise SearchPathParseError(
            f"{filename} does not contain a valid GameInfo/FileSystem/SearchPaths block"
        ) from exc

    if not search_paths.has_children():
        raise SearchPathParseError(f"{filename} SearchPaths must be a block")

    specs: list[SearchPathSpec] = []
    for ordinal, prop in enumerate(search_paths):
        if prop.has_children():
            raise SearchPathParseError(
                f"{filename}:{prop.line_num}: nested block {prop.real_name!r} "
                "is not a SearchPath leaf"
            )
        specs.append(SearchPathSpec(
            ordinal=ordinal,
            path_id=prop.real_name,
            raw_value=prop.value,
            line_number=prop.line_num,
        ))
    return tuple(specs)


def plan_search_paths(
    specs: Iterable[SearchPathSpec],
    *,
    gameinfo_dir: Path,
    engine_root: Path | None,
) -> SearchPathPlan:
    """Expand SearchPaths into ordered concrete folder/VPK mounts.

    Missing paths are recorded as diagnostics instead of failing globally.
    A requested asset which cannot be found in any valid mount is diagnosed by
    the caller at resolution time.
    """
    spec_tuple = tuple(specs)
    mounts: list[MountedSearchPath] = []
    diagnostics: list[SearchPathDiagnostic] = []
    gameinfo_dir = gameinfo_dir.resolve()
    engine_root = engine_root.resolve() if engine_root is not None else None

    for spec in spec_tuple:
        base_path, error = _expand_base_path(
            spec.raw_value,
            gameinfo_dir=gameinfo_dir,
            engine_root=engine_root,
        )
        if error is not None:
            diagnostics.append(_diagnostic(spec, error))
            continue
        assert base_path is not None

        candidates, error = _expand_wildcard(base_path)
        if error is not None:
            diagnostics.append(_diagnostic(spec, error, base_path))
            continue

        mounted_for_spec = 0
        for expansion_index, candidate in enumerate(candidates):
            mount_kind, mount_path, reason = _classify_mount(candidate)
            if reason is not None:
                diagnostics.append(_diagnostic(spec, reason, candidate))
                continue
            assert mount_kind is not None and mount_path is not None
            mounts.append(MountedSearchPath(
                mount_index=len(mounts),
                source_ordinal=spec.ordinal,
                expansion_index=expansion_index,
                path_id=spec.path_id,
                raw_value=spec.raw_value,
                kind=mount_kind,
                container_path=mount_path.resolve(),
            ))
            mounted_for_spec += 1

        if not candidates:
            diagnostics.append(_diagnostic(spec, "wildcard_no_matches", base_path))
        elif mounted_for_spec == 0 and not any(
            diagnostic.source_ordinal == spec.ordinal
            for diagnostic in diagnostics
        ):
            diagnostics.append(_diagnostic(spec, "no_mountable_candidates", base_path))

    return SearchPathPlan(spec_tuple, tuple(mounts), tuple(diagnostics))


class OrderedAssetFileSystem:
    """Resolve exact logical paths using a manually ordered srctools chain."""

    def __init__(self, mounts: Iterable[MountedSearchPath]) -> None:
        self.mounts = tuple(mounts)
        self.chain = FileSystemChain()
        self._mount_by_system_id: dict[int, MountedSearchPath] = {}

        for mount in self.mounts:
            if mount.kind == "folder":
                system = RawFileSystem(mount.container_path)
            elif mount.kind == "vpk":
                system = VPKFileSystem(mount.container_path)
            else:
                raise ValueError(f"Unknown SearchPath mount kind: {mount.kind!r}")
            self.chain.add_sys(system)
            self._mount_by_system_id[id(system)] = mount

    def resolve(self, logical_path: str) -> ResolvedAsset:
        """Return the first exact match and stop searching immediately."""
        normalized = normalize_logical_path(logical_path)
        file = self.chain[normalized]
        system = self.chain.get_system(file)
        mount = self._mount_by_system_id[id(system)]
        return ResolvedAsset(
            provenance=AssetProvenance(
                logical_path=normalized,
                mount_index=mount.mount_index,
                source_ordinal=mount.source_ordinal,
                expansion_index=mount.expansion_index,
                path_id=mount.path_id,
                raw_value=mount.raw_value,
                kind=mount.kind,
                container_path=mount.container_path,
            ),
            file=file,
        )


def normalize_logical_path(logical_path: str) -> str:
    """Canonicalise a Hammer asset path without permitting path traversal."""
    value = logical_path.strip().replace("\\", "/")
    parts: list[str] = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError(f"Logical asset path escapes its root: {logical_path!r}")
        if not parts and ":" in part:
            raise ValueError(f"Logical asset path must not be absolute: {logical_path!r}")
        parts.append(part.casefold())
    if not parts:
        raise ValueError("Logical asset path is empty")
    return "/".join(parts)


def _expand_base_path(
    raw_value: str,
    *,
    gameinfo_dir: Path,
    engine_root: Path | None,
) -> tuple[Path | None, str | None]:
    value = raw_value.strip().replace("\\", "/")
    folded = value.casefold()
    if folded.startswith(_GAMEINFO_TOKEN):
        remainder = value[len(_GAMEINFO_TOKEN):].lstrip("/")
        return gameinfo_dir / remainder, None
    if folded.startswith(_ENGINE_TOKEN):
        if engine_root is None:
            return None, "all_source_engine_paths_without_engine_root"
        remainder = value[len(_ENGINE_TOKEN):].lstrip("/")
        return engine_root / remainder, None
    if "|" in value:
        return None, "unsupported_searchpath_token"

    path = Path(value)
    if path.is_absolute():
        return path, None
    if engine_root is None:
        return None, "relative_path_without_engine_root"
    return engine_root / path, None


def _expand_wildcard(path: Path) -> tuple[tuple[Path, ...], str | None]:
    path_text = str(path)
    if "*" not in path_text and "?" not in path_text:
        return (path,), None
    parent = path.parent
    pattern = path.name
    if "*" in str(parent) or "?" in str(parent):
        return (), "wildcard_only_supported_in_final_component"
    if not parent.is_dir():
        return (), "wildcard_root_missing"
    matches = sorted(
        parent.glob(pattern),
        key=lambda item: (item.name.casefold(), item.name),
    )
    return tuple(matches), None


def _classify_mount(path: Path) -> tuple[str | None, Path | None, str | None]:
    if path.is_dir():
        return "folder", path, None

    vpk_path = _resolve_vpk_path(path)
    if vpk_path is not None:
        if _NUMBERED_VPK.search(vpk_path.name):
            return None, None, "numbered_vpk_chunk"
        return "vpk", vpk_path, None

    if path.exists():
        return None, None, "unsupported_searchpath_file"
    return None, None, "searchpath_missing"


def _resolve_vpk_path(path: Path) -> Path | None:
    candidates: list[Path] = []
    if path.suffix.casefold() == ".vpk":
        candidates.append(path)
        if not path.name.casefold().endswith("_dir.vpk"):
            candidates.append(path.with_name(path.stem + "_dir.vpk"))
    elif not path.suffix:
        candidates.append(path.with_suffix(".vpk"))
        candidates.append(path.with_name(path.name + "_dir.vpk"))
    else:
        return None

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _diagnostic(
    spec: SearchPathSpec,
    reason: str,
    candidate_path: Path | None = None,
) -> SearchPathDiagnostic:
    return SearchPathDiagnostic(
        source_ordinal=spec.ordinal,
        path_id=spec.path_id,
        raw_value=spec.raw_value,
        reason=reason,
        candidate_path=candidate_path,
    )


__all__ = [
    "AssetProvenance",
    "MountedSearchPath",
    "OrderedAssetFileSystem",
    "ResolvedAsset",
    "SearchPathDiagnostic",
    "SearchPathParseError",
    "SearchPathPlan",
    "SearchPathSpec",
    "normalize_logical_path",
    "parse_gameinfo_search_paths",
    "parse_search_paths_text",
    "plan_search_paths",
]
