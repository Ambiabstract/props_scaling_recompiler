"""Normalised, read-only VMT inspection backed by :mod:`srctools.vmt`."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from srctools.keyvalues import Keyvalues
from srctools.vmt import Material

from .searchpaths import (
    AssetProvenance,
    OrderedAssetFileSystem,
    ResolvedAsset,
    normalize_logical_path,
)


ColorParameter = Literal["$color", "$color2"]


class SourceMaterialInspectionError(RuntimeError):
    """A categorised failure to resolve or semantically inspect a VMT."""

    def __init__(self, code: str, logical_path: str, detail: str) -> None:
        self.code = code
        self.logical_path = logical_path
        self.detail = detail
        super().__init__(f"{code}: {logical_path}: {detail}")


@dataclass(frozen=True, slots=True)
class SourceMaterialFileMetadata:
    """Content identity and provenance for one VMT in a Patch graph."""

    logical_path: str
    size: int
    sha256: str
    provenance: AssetProvenance


@dataclass(frozen=True, slots=True)
class MaterialBlockMetadata:
    """Immutable semantic form of a VMT block or proxy node."""

    name: str
    value: str | None
    children: tuple["MaterialBlockMetadata", ...] = ()


@dataclass(frozen=True, slots=True)
class SourceMaterialMetadata:
    """Deterministic semantic metadata for a source VMT and its Patch graph."""

    logical_material_path: str
    provenance: AssetProvenance
    size: int
    sha256: str
    source_shader: str
    effective_shader: str
    source_parameters: tuple[tuple[str, str], ...]
    parameters: tuple[tuple[str, str], ...]
    source_blocks: tuple[MaterialBlockMetadata, ...]
    blocks: tuple[MaterialBlockMetadata, ...]
    source_proxies: tuple[MaterialBlockMetadata, ...]
    proxies: tuple[MaterialBlockMetadata, ...]
    dependencies: tuple[SourceMaterialFileMetadata, ...]
    dependency_fingerprint: str

    @property
    def is_patch(self) -> bool:
        return self.source_shader.casefold() == "patch"


def inspect_source_material(
    filesystem: OrderedAssetFileSystem,
    logical_material_path: str,
) -> SourceMaterialMetadata:
    """Resolve and inspect one original VMT without extracting or modifying it."""
    try:
        logical_path = normalize_logical_path(logical_material_path)
    except ValueError as exc:
        raise SourceMaterialInspectionError(
            "invalid_material_path",
            logical_material_path,
            str(exc),
        ) from exc
    _validate_source_material_path(logical_path)

    try:
        resolved = filesystem.resolve(logical_path)
    except FileNotFoundError as exc:
        raise SourceMaterialInspectionError(
            "material_not_found",
            logical_path,
            "no exact match in ordered SearchPaths",
        ) from exc

    source_bytes = resolved.read_bytes()
    try:
        source_text = _decode_material(source_bytes)
        source = Material.parse(source_text.splitlines(keepends=True), logical_path)
    except Exception as exc:
        raise SourceMaterialInspectionError(
            "invalid_vmt",
            logical_path,
            f"{type(exc).__name__}: {exc}",
        ) from exc

    dependency_paths: list[str] = []

    def record_dependency(path: str) -> None:
        normalized = normalize_logical_path(path)
        if normalized not in dependency_paths:
            dependency_paths.append(normalized)

    try:
        effective = source.apply_patches(
            filesystem.chain,
            parent_func=record_dependency,
        )
    except Exception as exc:
        raise SourceMaterialInspectionError(
            "invalid_vmt_patch",
            logical_path,
            f"{type(exc).__name__}: {exc}",
        ) from exc

    source_file = _fingerprint_material_file(resolved, source_bytes)
    dependencies = tuple(
        _fingerprint_material_file(filesystem.resolve(path))
        for path in dependency_paths
    )
    return SourceMaterialMetadata(
        logical_material_path=logical_path,
        provenance=resolved.provenance,
        size=source_file.size,
        sha256=source_file.sha256,
        source_shader=source.shader,
        effective_shader=effective.shader,
        source_parameters=_normalise_parameters(source),
        parameters=_normalise_parameters(effective),
        source_blocks=tuple(_normalise_block(block) for block in source.blocks),
        blocks=tuple(_normalise_block(block) for block in effective.blocks),
        source_proxies=tuple(_normalise_block(proxy) for proxy in source.proxies),
        proxies=tuple(_normalise_block(proxy) for proxy in effective.proxies),
        dependencies=dependencies,
        dependency_fingerprint=_dependency_fingerprint(source_file, dependencies),
    )


def select_color_parameter(metadata: SourceMaterialMetadata) -> ColorParameter | None:
    """Choose a conservative fixed-tint parameter for SDK 2013 model shaders.

    Existing color keys are preserved.  For the two confirmed generic model
    shaders, ``$color2`` is the default base-texture tint.  Other shaders stay
    unsupported until an SDK regression proves their tint semantics.
    """
    parameters = {name for name, _value in metadata.parameters}
    if "$color2" in parameters:
        return "$color2"
    if "$color" in parameters:
        return "$color"
    if metadata.effective_shader.casefold() in {"vertexlitgeneric", "unlitgeneric"}:
        return "$color2"
    return None


def colored_material_path(
    logical_source_material: str,
    color: tuple[int, int, int],
) -> str:
    """Return the collision-safe managed VMT path for a source/RGB identity."""
    source = normalize_logical_path(logical_source_material)
    _validate_source_material_path(source)
    if len(color) != 3 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in color):
        raise ValueError(f"RGB channels must be integers within 0..255, got {color!r}")

    source_path = PurePosixPath(source)
    model_prefix = PurePosixPath("materials/models")
    try:
        relative = source_path.relative_to(model_prefix)
    except ValueError:
        material_relative = source_path.relative_to("materials")
        relative = PurePosixPath("_material_root", material_relative)

    suffix = "_col_" + "_".join(f"{channel:03d}" for channel in color) + ".vmt"
    filename = relative.with_suffix("").name + suffix
    destination = PurePosixPath("materials/models/psr_scaled", relative.parent, filename)
    return normalize_logical_path(str(destination))


def _validate_source_material_path(logical_path: str) -> None:
    if not logical_path.startswith("materials/") or not logical_path.endswith(".vmt"):
        raise SourceMaterialInspectionError(
            "invalid_material_path",
            logical_path,
            "source material must be a logical materials/**/*.vmt path",
        )
    if logical_path.startswith("materials/models/psr_scaled/"):
        raise SourceMaterialInspectionError(
            "managed_source_material",
            logical_path,
            "PSR managed output cannot be used as an original material",
        )


def _decode_material(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _fingerprint_material_file(
    resolved: ResolvedAsset,
    data: bytes | None = None,
) -> SourceMaterialFileMetadata:
    if data is None:
        data = resolved.read_bytes()
    return SourceMaterialFileMetadata(
        logical_path=resolved.provenance.logical_path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        provenance=resolved.provenance,
    )


def _dependency_fingerprint(
    source: SourceMaterialFileMetadata,
    dependencies: tuple[SourceMaterialFileMetadata, ...],
) -> str:
    digest = hashlib.sha256()
    for item in (source, *dependencies):
        digest.update(item.logical_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(item.sha256))
        digest.update(b"\0")
    return digest.hexdigest()


def _normalise_parameters(material: Material) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        ((name.casefold(), value) for name, value in material.items()),
        key=lambda item: item[0],
    ))


def _normalise_block(block: Keyvalues) -> MaterialBlockMetadata:
    if block.has_children():
        return MaterialBlockMetadata(
            name=block.real_name,
            value=None,
            children=tuple(_normalise_block(child) for child in block),
        )
    return MaterialBlockMetadata(name=block.real_name, value=block.value)


__all__ = [
    "ColorParameter",
    "MaterialBlockMetadata",
    "SourceMaterialFileMetadata",
    "SourceMaterialInspectionError",
    "SourceMaterialMetadata",
    "colored_material_path",
    "inspect_source_material",
    "select_color_parameter",
]
