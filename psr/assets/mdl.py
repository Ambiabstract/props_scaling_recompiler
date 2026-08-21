"""Normalised source-model inspection backed by :mod:`srctools.mdl`."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from srctools.mdl import MDL_EXTS, Flags, Model

from .searchpaths import (
    AssetProvenance,
    OrderedAssetFileSystem,
    ResolvedAsset,
    normalize_logical_path,
)


_TEXTURE_STRUCT_SIZE = 64
_BODYPART_STRUCT_SIZE = 16
_MODEL_STRUCT_SIZE = 148
_MESH_STRUCT_SIZE = 116
_BONE_COUNT_OFFSET = 156


class SourceAssetInspectionError(RuntimeError):
    """A categorised failure to resolve or inspect an original model."""

    def __init__(self, code: str, logical_path: str, detail: str) -> None:
        self.code = code
        self.logical_path = logical_path
        self.detail = detail
        super().__init__(f"{code}: {logical_path}: {detail}")


@dataclass(frozen=True, slots=True)
class SourceFileMetadata:
    """Content identity and provenance for one model or companion file."""

    logical_path: str
    size: int
    sha256: str
    provenance: AssetProvenance


@dataclass(frozen=True, slots=True)
class MaterialReferenceMetadata:
    """One unique MDL material name and its first resolved VMT, if present."""

    material_name: str
    logical_path: str | None
    provenance: AssetProvenance | None


@dataclass(frozen=True, slots=True)
class SourceAssetMetadata:
    """Immutable, deterministic metadata discovered for one original MDL."""

    logical_model_path: str
    model_provenance: AssetProvenance
    internal_model_name: str
    mdl_version: int
    mdl_header_checksum: str
    mdl_flags: int
    is_static_prop: bool
    bone_count: int
    surface_property: str
    total_vertices: int
    cdmaterials: tuple[str, ...]
    skin_families: tuple[tuple[str, ...], ...]
    material_names: tuple[str, ...]
    materials: tuple[MaterialReferenceMetadata, ...]
    files: tuple[SourceFileMetadata, ...]

    @property
    def has_physics(self) -> bool:
        """Whether a PHY companion was resolved through SearchPaths."""
        return any(path.logical_path.endswith(".phy") for path in self.files)


def inspect_source_model(
    filesystem: OrderedAssetFileSystem,
    logical_model_path: str,
) -> SourceAssetMetadata:
    """Resolve and inspect an original model without extracting or modifying it."""
    try:
        logical_path = normalize_logical_path(logical_model_path)
    except ValueError as exc:
        raise SourceAssetInspectionError(
            "invalid_model_path",
            logical_model_path,
            str(exc),
        ) from exc
    _validate_source_model_path(logical_path)

    try:
        resolved_model = filesystem.resolve(logical_path)
    except FileNotFoundError as exc:
        raise SourceAssetInspectionError(
            "model_not_found",
            logical_path,
            "no exact match in ordered SearchPaths",
        ) from exc

    mdl_bytes = resolved_model.read_bytes()
    try:
        model = Model(filesystem.chain, resolved_model.file)
        bone_count = _read_bone_count(mdl_bytes)
        stable_skins = _read_stable_skin_families(mdl_bytes)
        _validate_srctools_skins(model, stable_skins)
    except Exception as exc:
        raise SourceAssetInspectionError(
            "invalid_mdl",
            logical_path,
            f"{type(exc).__name__}: {exc}",
        ) from exc

    cdmaterials = _normalise_cdmaterials(model.cdmaterials)
    skin_families = tuple(
        tuple(_normalise_material_name(material) for material in family)
        for family in stable_skins
    )
    material_names = _unique_in_order(
        material
        for family in skin_families
        for material in family
    )

    files = _inspect_model_files(
        filesystem,
        logical_path,
        resolved_model,
        mdl_bytes,
    )
    materials = tuple(
        _resolve_material(filesystem, material_name, cdmaterials)
        for material_name in material_names
    )

    return SourceAssetMetadata(
        logical_model_path=logical_path,
        model_provenance=resolved_model.provenance,
        internal_model_name=model.name.replace("\\", "/"),
        mdl_version=model.version,
        mdl_header_checksum=model.checksum.hex(),
        mdl_flags=model.flags.value,
        is_static_prop=bool(model.flags & Flags.static_prop),
        bone_count=bone_count,
        surface_property=model.surfaceprop,
        total_vertices=model.total_verts,
        cdmaterials=cdmaterials,
        skin_families=skin_families,
        material_names=material_names,
        materials=materials,
        files=files,
    )


def _read_bone_count(mdl_bytes: bytes) -> int:
    """Read the studiohdr_t bone count used by Source scaling policy."""
    try:
        bone_count = struct.unpack_from("<i", mdl_bytes, _BONE_COUNT_OFFSET)[0]
    except struct.error as exc:
        raise ValueError("MDL header is truncated before numbones") from exc
    if bone_count < 0:
        raise ValueError(f"MDL numbones is negative: {bone_count}")
    return bone_count


def _validate_source_model_path(logical_path: str) -> None:
    if not logical_path.startswith("models/") or not logical_path.endswith(".mdl"):
        raise SourceAssetInspectionError(
            "invalid_model_path",
            logical_path,
            "source model must be a logical models/**/*.mdl path",
        )
    if logical_path.startswith("models/psr_scaled/"):
        raise SourceAssetInspectionError(
            "managed_source_asset",
            logical_path,
            "PSR managed output cannot be used as an original asset",
        )


def _inspect_model_files(
    filesystem: OrderedAssetFileSystem,
    logical_model_path: str,
    resolved_model: ResolvedAsset,
    mdl_bytes: bytes,
) -> tuple[SourceFileMetadata, ...]:
    files = [_fingerprint_file(resolved_model, data=mdl_bytes)]
    model_path = PurePosixPath(logical_model_path)
    for extension in MDL_EXTS:
        if extension == ".mdl":
            continue
        companion_path = str(model_path.with_suffix(extension))
        try:
            resolved = filesystem.resolve(companion_path)
        except FileNotFoundError:
            continue
        files.append(_fingerprint_file(resolved))
    return tuple(files)


def _fingerprint_file(
    resolved: ResolvedAsset,
    *,
    data: bytes | None = None,
) -> SourceFileMetadata:
    digest = hashlib.sha256()
    size = 0
    if data is not None:
        digest.update(data)
        size = len(data)
    else:
        with resolved.open_bin() as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    return SourceFileMetadata(
        logical_path=resolved.provenance.logical_path,
        size=size,
        sha256=digest.hexdigest(),
        provenance=resolved.provenance,
    )


def _resolve_material(
    filesystem: OrderedAssetFileSystem,
    material_name: str,
    cdmaterials: tuple[str, ...],
) -> MaterialReferenceMetadata:
    checked: set[str] = set()
    for cdmaterial in cdmaterials:
        candidate = str(
            PurePosixPath("materials", cdmaterial, material_name).with_suffix(".vmt")
        )
        candidate = normalize_logical_path(candidate)
        if candidate in checked:
            continue
        checked.add(candidate)
        try:
            resolved = filesystem.resolve(candidate)
        except FileNotFoundError:
            continue
        return MaterialReferenceMetadata(
            material_name=material_name,
            logical_path=resolved.provenance.logical_path,
            provenance=resolved.provenance,
        )
    return MaterialReferenceMetadata(material_name, None, None)


def _normalise_cdmaterials(values: list[str]) -> tuple[str, ...]:
    normalised: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip().replace("\\", "/").lstrip("/").casefold()
        if item and not item.endswith("/"):
            item += "/"
        if item not in seen:
            seen.add(item)
            normalised.append(item)
    return tuple(normalised)


def _normalise_material_name(value: str) -> str:
    item = value.strip().replace("\\", "/").lstrip("/").casefold()
    if item.endswith(".vmt"):
        item = item[:-4]
    return item


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _read_stable_skin_families(data: bytes) -> tuple[tuple[str, ...], ...]:
    """Read skin slots in numeric mesh-material order.

    ``srctools.mdl.Model`` 2.7.0 culls unused slots by iterating a set. PSR
    reparses only the necessary offsets and sorts the numeric slot indexes so
    set iteration can never become part of the generated skin layout format.
    """
    if len(data) < 392:
        raise ValueError("MDL header is truncated")
    (
        texture_count,
        texture_offset,
        _cdmaterial_count,
        _cdmaterial_offset,
        skinref_count,
        skin_count,
        skin_offset,
        bodypart_count,
        bodypart_offset,
    ) = _unpack_from("<9i", data, 204)
    for name, count in [
        ("texture", texture_count),
        ("skin reference", skinref_count),
        ("skin family", skin_count),
        ("bodypart", bodypart_count),
    ]:
        if count < 0:
            raise ValueError(f"negative {name} count: {count}")

    textures: list[str] = []
    for index in range(texture_count):
        entry_offset = texture_offset + index * _TEXTURE_STRUCT_SIZE
        (name_offset,) = _unpack_from("<i", data, entry_offset)
        textures.append(_read_ascii_cstring(data, entry_offset + name_offset))

    raw_skin_indices: list[int] = []
    for index in range(skinref_count * skin_count):
        (texture_index,) = _unpack_from("<H", data, skin_offset + index * 2)
        if texture_index >= texture_count:
            raise ValueError(
                f"skin texture index {texture_index} is outside {texture_count} textures"
            )
        raw_skin_indices.append(texture_index)

    used_slots: set[int] = set()
    for bodypart_index in range(bodypart_count):
        bodypart_start = bodypart_offset + bodypart_index * _BODYPART_STRUCT_SIZE
        _, model_count, _, model_offset = _unpack_from("<4i", data, bodypart_start)
        if model_count < 0:
            raise ValueError(f"negative model count: {model_count}")
        for model_index in range(model_count):
            model_start = bodypart_start + model_offset + model_index * _MODEL_STRUCT_SIZE
            mesh_count, mesh_offset = _unpack_from("<2i", data, model_start + 72)
            if mesh_count < 0:
                raise ValueError(f"negative mesh count: {mesh_count}")
            for mesh_index in range(mesh_count):
                mesh_start = model_start + mesh_offset + mesh_index * _MESH_STRUCT_SIZE
                (material_slot,) = _unpack_from("<i", data, mesh_start)
                if not 0 <= material_slot < skinref_count:
                    raise ValueError(
                        f"mesh material slot {material_slot} is outside {skinref_count} slots"
                    )
                used_slots.add(material_slot)

    ordered_slots = sorted(used_slots)
    families: list[tuple[str, ...]] = []
    for family_index in range(skin_count):
        row_start = family_index * skinref_count
        families.append(tuple(
            textures[raw_skin_indices[row_start + slot]]
            for slot in ordered_slots
        ))
    return tuple(families)


def _validate_srctools_skins(
    model: Model,
    stable_skins: tuple[tuple[str, ...], ...],
) -> None:
    srctools_skins = tuple(
        tuple(material.replace("\\", "/").lstrip("/") for material in family)
        for family in model.skins
    )
    if len(srctools_skins) != len(stable_skins):
        raise ValueError("srctools and stable reader disagree on skin family count")
    for index, (actual, stable) in enumerate(zip(srctools_skins, stable_skins)):
        if sorted(actual) != sorted(stable):
            raise ValueError(
                f"srctools and stable reader disagree on skin family {index}"
            )


def _unpack_from(fmt: str, data: bytes, offset: int) -> tuple[int, ...]:
    if offset < 0:
        raise ValueError(f"negative MDL offset: {offset}")
    size = struct.calcsize(fmt)
    if offset + size > len(data):
        raise ValueError(
            f"MDL offset {offset} with size {size} exceeds file size {len(data)}"
        )
    return struct.unpack_from(fmt, data, offset)


def _read_ascii_cstring(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        raise ValueError(f"string offset {offset} exceeds file size {len(data)}")
    end = data.find(b"\0", offset)
    if end == -1:
        raise ValueError(f"unterminated string at offset {offset}")
    return data[offset:end].decode("ascii")


__all__ = [
    "MaterialReferenceMetadata",
    "SourceAssetInspectionError",
    "SourceAssetMetadata",
    "SourceFileMetadata",
    "inspect_source_model",
]
