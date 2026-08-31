"""Read-only VMF request discovery and source-asset inspection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Literal

from psr.assets import (
    OrderedAssetFileSystem,
    SourceAssetInspectionError,
    SourceAssetMetadata,
    inspect_source_model,
    normalize_logical_path,
)
from psr.keyvalues import Block, Document, iter_blocks, parse_vmf


_PSR_CLASSNAME = b"prop_static_scalable"


@dataclass(frozen=True, slots=True)
class PipelineDiagnostic:
    """One deterministic discovery/planning problem or warning."""

    severity: Literal["error", "warning"]
    code: str
    detail: str
    entity_id: str | None = None
    source_line: int | None = None


@dataclass(frozen=True, slots=True)
class VmfEntityRequest:
    """Raw PSR request from one active, top-level VMF entity."""

    entity_id: str
    logical_model_path: str
    raw_modelscale: str | None
    raw_skin: str
    raw_rendercolor: str
    origin: str | None
    source_line: int
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class MapDiscovery:
    """Structurally validated raw VMF discovery result."""

    map_identity: str
    vmf_size: int
    vmf_sha256: str
    requests: tuple[VmfEntityRequest, ...]
    hidden_psr_entities: int
    diagnostics: tuple[PipelineDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class InspectedMap:
    """Map discovery linked to all successfully inspected unique sources."""

    discovery: MapDiscovery
    source_assets: tuple[SourceAssetMetadata, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]


def discover_vmf_requests(
    source: bytes,
    *,
    map_identity: str,
    value_encoding: str = "cp1252",
) -> MapDiscovery:
    """Collect active PSR entities without mutating or reserialising VMF bytes."""
    if not map_identity.strip():
        raise ValueError("map_identity must be a stable, non-empty project-relative ID")
    document = parse_vmf(source)
    diagnostics: list[PipelineDiagnostic] = []
    candidates: list[VmfEntityRequest] = []

    for block in document.blocks:
        if block.name.lower() != b"entity":
            continue
        class_values = block.direct_values(b"classname")
        if not any(value.lower() == _PSR_CLASSNAME for value in class_values):
            continue
        request = _extract_request(
            document,
            block,
            diagnostics,
            value_encoding=value_encoding,
        )
        if request is not None:
            candidates.append(request)

    counts: dict[str, int] = {}
    for request in candidates:
        counts[request.entity_id] = counts.get(request.entity_id, 0) + 1
    requests: list[VmfEntityRequest] = []
    for request in candidates:
        if counts[request.entity_id] > 1:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "duplicate_entity_id",
                f"entity ID {request.entity_id!r} occurs more than once",
                request.entity_id,
                request.source_line,
            ))
        else:
            requests.append(request)

    return MapDiscovery(
        map_identity=map_identity,
        vmf_size=len(source),
        vmf_sha256=hashlib.sha256(source).hexdigest(),
        requests=tuple(requests),
        hidden_psr_entities=_count_hidden_psr_entities(document),
        diagnostics=tuple(diagnostics),
    )


def inspect_map_sources(
    discovery: MapDiscovery,
    filesystem: OrderedAssetFileSystem,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> InspectedMap:
    """Inspect every unique source model while collecting all failures."""
    assets: list[SourceAssetMetadata] = []
    diagnostics = list(discovery.diagnostics)
    model_paths = sorted({request.logical_model_path for request in discovery.requests})
    total = len(model_paths)
    if progress_callback is not None:
        progress_callback(0, total, "waiting for first source model")
    for index, logical_path in enumerate(model_paths):
        if progress_callback is not None:
            progress_callback(index, total, f"inspecting {logical_path}")
        try:
            assets.append(inspect_source_model(filesystem, logical_path))
        except SourceAssetInspectionError as exc:
            diagnostics.append(PipelineDiagnostic(
                "error",
                exc.code,
                f"{logical_path}: {exc.detail}",
                entity_id=None,
                source_line=None,
            ))
        if progress_callback is not None:
            progress_callback(index + 1, total, f"inspected {logical_path}")
    return InspectedMap(discovery, tuple(assets), tuple(diagnostics))


def _extract_request(
    document: Document,
    block: Block,
    diagnostics: list[PipelineDiagnostic],
    *,
    value_encoding: str,
) -> VmfEntityRequest | None:
    line = document.line_number(block.start)
    values: dict[bytes, bytes | None] = {}
    invalid = False
    for key in (b"classname", b"id", b"model", b"modelscale", b"skin", b"rendercolor"):
        found = block.direct_values(key)
        if len(found) > 1:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "duplicate_entity_property",
                f"active PSR entity has {len(found)} direct {key.decode('ascii')!r} keys",
                source_line=line,
            ))
            invalid = True
        values[key] = found[0] if len(found) == 1 else None
    if invalid:
        return None

    try:
        entity_id = _required_text(values[b"id"], "id", value_encoding)
        model = _required_text(values[b"model"], "model", value_encoding)
        raw_modelscale = _optional_text(values[b"modelscale"], value_encoding)
        raw_skin_value = _optional_text(values[b"skin"], value_encoding)
        raw_skin = "0" if raw_skin_value is None else raw_skin_value
        raw_color_value = _optional_text(values[b"rendercolor"], value_encoding)
        raw_rendercolor = "255 255 255" if raw_color_value is None else raw_color_value
        origin_values = block.direct_values(b"origin")
        if len(origin_values) > 1:
            raise ValueError("active PSR entity has duplicate direct 'origin' keys")
        origin = _optional_text(origin_values[0] if origin_values else None, value_encoding)
    except (UnicodeError, ValueError) as exc:
        diagnostics.append(PipelineDiagnostic(
            "error",
            "invalid_entity_property",
            str(exc),
            source_line=line,
        ))
        return None

    if not entity_id.isdecimal() or int(entity_id) <= 0:
        diagnostics.append(PipelineDiagnostic(
            "error",
            "invalid_entity_id",
            f"entity ID must be a positive decimal integer, got {entity_id!r}",
            entity_id,
            line,
        ))
        return None
    try:
        logical_model_path = normalize_logical_path(model)
    except ValueError as exc:
        diagnostics.append(PipelineDiagnostic(
            "error",
            "invalid_model_path",
            str(exc),
            entity_id,
            line,
        ))
        return None
    return VmfEntityRequest(
        entity_id=entity_id,
        logical_model_path=logical_model_path,
        raw_modelscale=raw_modelscale,
        raw_skin=raw_skin,
        raw_rendercolor=raw_rendercolor,
        origin=origin,
        source_line=line,
        source_start=block.start,
        source_end=block.end,
    )


def _required_text(value: bytes | None, key: str, encoding: str) -> str:
    text = _optional_text(value, encoding)
    if not text:
        raise ValueError(f"active PSR entity requires one non-empty {key!r} value")
    return text


def _optional_text(value: bytes | None, encoding: str) -> str | None:
    return None if value is None else value.decode(encoding)


def _count_hidden_psr_entities(document: Document) -> int:
    count = 0
    for hidden in document.blocks:
        if hidden.name.lower() != b"hidden":
            continue
        for block in iter_blocks(hidden.children, recursive=True):
            if block.name.lower() != b"entity":
                continue
            class_values = block.direct_values(b"classname")
            if any(value.lower() == _PSR_CLASSNAME for value in class_values):
                count += 1
    return count


__all__ = [
    "InspectedMap",
    "MapDiscovery",
    "PipelineDiagnostic",
    "VmfEntityRequest",
    "discover_vmf_requests",
    "inspect_map_sources",
]
