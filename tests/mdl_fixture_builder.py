"""Build tiny, deterministic StudioMDL binaries for adapter contract tests."""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any


_HEADER_SIZE = 392
_TEXTURE_SIZE = 64
_BODYPART_SIZE = 16
_MODEL_SIZE = 148
_MESH_SIZE = 116


def build_case_files(case: Mapping[str, Any]) -> dict[str, bytes]:
    """Return all logical files for one JSON fixture case."""
    logical_model_path = case["logical_model_path"]
    result = {logical_model_path: build_mdl(case)}
    result.update(
        (logical_path, text.encode("ascii"))
        for logical_path, text in case["material_files"].items()
    )
    model_path = PurePosixPath(logical_model_path)
    for extension in case["companions"]:
        logical_path = str(model_path.with_suffix(extension))
        if extension == ".phy":
            result[logical_path] = build_phy()
        else:
            result[logical_path] = f"synthetic {extension}\n".encode("ascii")
    return result


def build_mdl(case: Mapping[str, Any]) -> bytes:
    """Build the minimum valid MDL structure consumed by srctools 2.7.0."""
    skin_families = tuple(tuple(row) for row in case["skin_families"])
    if not skin_families or not skin_families[0]:
        raise ValueError("fixture needs at least one skin family and material slot")
    skinref_count = len(skin_families[0])
    if any(len(row) != skinref_count for row in skin_families):
        raise ValueError("all fixture skin families must have the same width")

    texture_names = _unique_in_order(
        material for family in skin_families for material in family
    )
    texture_indexes = {name: index for index, name in enumerate(texture_names)}
    data = bytearray(_HEADER_SIZE)

    def align(alignment: int = 4) -> None:
        data.extend(b"\0" * (-len(data) % alignment))

    def append(value: bytes, *, alignment: int = 4) -> int:
        align(alignment)
        offset = len(data)
        data.extend(value)
        return offset

    texture_offset = append(b"\0" * (_TEXTURE_SIZE * len(texture_names)))
    for index, texture_name in enumerate(texture_names):
        entry_offset = texture_offset + index * _TEXTURE_SIZE
        name_bytes = texture_name.encode("ascii") + b"\0"
        name_offset = append(name_bytes, alignment=1)
        struct.pack_into("<i", data, entry_offset, name_offset - entry_offset)

    cdmaterial_offsets: list[int] = []
    for cdmaterial in case["cdmaterials"]:
        cdmaterial_offsets.append(append(cdmaterial.encode("ascii") + b"\0", alignment=1))
    cdmaterial_table = append(b"\0" * (4 * len(cdmaterial_offsets)))
    for index, offset in enumerate(cdmaterial_offsets):
        struct.pack_into("<i", data, cdmaterial_table + index * 4, offset)

    skin_values = [
        texture_indexes[material]
        for family in skin_families
        for material in family
    ]
    skin_offset = append(struct.pack(f"<{len(skin_values)}H", *skin_values), alignment=2)
    surface_offset = append(case["surface_property"].encode("ascii") + b"\0", alignment=1)

    bodypart_offset = append(b"\0" * _BODYPART_SIZE)
    model_offset = append(b"\0" * _MODEL_SIZE)
    mesh_offset = append(b"\0" * (_MESH_SIZE * skinref_count))
    struct.pack_into("<4i", data, bodypart_offset, 0, 1, 0, model_offset - bodypart_offset)
    model_name = _fixed_ascii(case["internal_model_name"], 64)
    struct.pack_into(
        "<64sif9i",
        data,
        model_offset,
        model_name,
        0,
        1.0,
        skinref_count,
        mesh_offset - model_offset,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    for material_slot in range(skinref_count):
        struct.pack_into("<i", data, mesh_offset + material_slot * _MESH_SIZE, material_slot)

    flags = 16 if case["static_prop"] else 0
    checksum = bytes.fromhex(case["checksum_hex"])
    if len(checksum) != 4:
        raise ValueError("fixture checksum must contain exactly four bytes")
    struct.pack_into(
        "<4si4s64si",
        data,
        0,
        b"IDST",
        case["mdl_version"],
        checksum,
        _fixed_ascii(case["internal_model_name"], 64),
        len(data),
    )
    struct.pack_into("<18f", data, 80, *([0.0] * 18))
    struct.pack_into("<11I", data, 152, flags, *([0] * 10))
    struct.pack_into(
        "<13i",
        data,
        196,
        0,
        0,
        len(texture_names),
        texture_offset,
        len(cdmaterial_offsets),
        cdmaterial_table,
        skinref_count,
        len(skin_families),
        skin_offset,
        1,
        bodypart_offset,
        0,
        0,
    )
    struct.pack_into("<15I", data, 248, *([0] * 15))
    struct.pack_into("<5I", data, 308, surface_offset, 0, 0, 0, 0)
    struct.pack_into("<f11I", data, 328, 1.0, *([0] * 11))
    struct.pack_into("<3b5x2I", data, 376, 0, 0, 0, 0, 0)
    return bytes(data)


def build_phy() -> bytes:
    """Build a minimal PHY header and KeyValues solid block."""
    return struct.pack("<4i", 16, 0, 0, 123) + b'"solid" { "mass" "1" }\0'


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _fixed_ascii(value: str, size: int) -> bytes:
    encoded = value.encode("ascii")
    if len(encoded) >= size:
        raise ValueError(f"fixture string must be shorter than {size} bytes")
    return encoded + b"\0" * (size - len(encoded))
