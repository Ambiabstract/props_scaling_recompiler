"""Versioned, project-scoped JSON manifest with atomic storage."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal


SCHEMA_VERSION = 1
ManifestLoadStatus = Literal[
    "missing",
    "loaded",
    "migrated",
    "corrupt",
    "incompatible",
    "project_mismatch",
]


class ManifestSchemaError(ValueError):
    """Raised when a manifest document cannot be safely accepted or migrated."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """Stable project boundary and current GameInfo content provenance."""

    project_id: str
    normalized_gameinfo_path: str
    gameinfo_sha256: str


@dataclass(frozen=True, slots=True)
class SourceAssetRecord:
    logical_model_path: str
    source_fingerprint: str
    skin_families_fingerprint: str


@dataclass(frozen=True, slots=True)
class GeneratedModelRecord:
    logical_source_model: str
    compile_scale_percent: int
    logical_output_model: str
    requires_static_conversion: bool
    skin_layout_fingerprint: str
    expected_files: tuple[str, ...]
    artifact_fingerprint: str


@dataclass(frozen=True, slots=True)
class ColoredMaterialRecord:
    logical_source_material: str
    render_color: tuple[int, int, int]
    color_parameter: Literal["$color", "$color2"]
    generation_mode: Literal["patch", "full_copy"]
    logical_output_material: str
    source_fingerprint: str
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class SkinMappingRecord:
    logical_source_model: str
    source_skin: int
    render_color: tuple[int, int, int]
    target_skin: int
    source_skin_families_fingerprint: str
    layout_fingerprint: str


@dataclass(frozen=True, slots=True)
class MapUsageRecord:
    map_identity: str
    entity_id: str
    logical_source_model: str
    raw_modelscale: str | None
    compile_scale_percent: int
    source_skin: int
    render_color: tuple[int, int, int]
    logical_output_model: str
    target_skin: int


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    schema_version: int
    project: ProjectIdentity
    source_assets: tuple[SourceAssetRecord, ...]
    generated_models: tuple[GeneratedModelRecord, ...]
    colored_materials: tuple[ColoredMaterialRecord, ...]
    skin_mappings: tuple[SkinMappingRecord, ...]
    map_usages: tuple[MapUsageRecord, ...]


@dataclass(frozen=True, slots=True)
class ManifestLoadResult:
    manifest: ProjectManifest
    status: ManifestLoadStatus
    detail: str | None = None


def build_project_identity(gameinfo_path: Path) -> ProjectIdentity:
    """Build a Windows-style project identity from one concrete GameInfo file."""
    resolved = gameinfo_path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"GameInfo path is not a file: {resolved}")
    normalized = resolved.as_posix().casefold()
    project_digest = hashlib.sha256()
    project_digest.update(b"psr-project-identity-v1\0")
    project_digest.update(normalized.encode("utf-8"))
    return ProjectIdentity(
        project_id=project_digest.hexdigest(),
        normalized_gameinfo_path=normalized,
        gameinfo_sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


def empty_manifest(project: ProjectIdentity) -> ProjectManifest:
    return ProjectManifest(
        schema_version=SCHEMA_VERSION,
        project=project,
        source_assets=(),
        generated_models=(),
        colored_materials=(),
        skin_mappings=(),
        map_usages=(),
    )


def load_manifest(path: Path, expected_project: ProjectIdentity) -> ManifestLoadResult:
    """Load a manifest, recovering to an empty project cache on any unsafe input."""
    if not path.exists():
        return ManifestLoadResult(empty_manifest(expected_project), "missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ManifestLoadResult(
            empty_manifest(expected_project),
            "corrupt",
            f"JSON read failed: {type(exc).__name__}: {exc}",
        )

    original_version = document.get("schema_version") if isinstance(document, dict) else None
    try:
        migrated = migrate_manifest_document(document)
        manifest = manifest_from_document(migrated)
    except ManifestSchemaError as exc:
        status: ManifestLoadStatus = (
            "incompatible" if exc.code == "incompatible_schema" else "corrupt"
        )
        return ManifestLoadResult(empty_manifest(expected_project), status, exc.detail)
    except Exception as exc:
        return ManifestLoadResult(
            empty_manifest(expected_project),
            "corrupt",
            f"manifest validation failed: {type(exc).__name__}: {exc}",
        )

    if (
        manifest.project.project_id != expected_project.project_id
        or manifest.project.normalized_gameinfo_path
        != expected_project.normalized_gameinfo_path
    ):
        return ManifestLoadResult(
            empty_manifest(expected_project),
            "project_mismatch",
            "manifest belongs to a different normalized GameInfo identity",
        )

    manifest = replace(manifest, project=expected_project)
    status = "migrated" if original_version != SCHEMA_VERSION else "loaded"
    return ManifestLoadResult(manifest, status)


def save_manifest_atomic(path: Path, manifest: ProjectManifest) -> None:
    """Atomically replace a manifest after strict schema validation."""
    data = manifest_to_json(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def manifest_to_json(manifest: ProjectManifest) -> bytes:
    document = manifest_to_document(manifest)
    validated = manifest_from_document(document)
    canonical = manifest_to_document(validated)
    return (
        json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def migrate_manifest_document(document: Any) -> dict[str, Any]:
    """Migrate a decoded JSON object through every known schema version."""
    if not isinstance(document, dict):
        raise ManifestSchemaError("invalid_manifest", "manifest root must be an object")
    migrated = copy.deepcopy(document)
    version = migrated.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ManifestSchemaError("invalid_manifest", "schema_version must be an integer")
    if version > SCHEMA_VERSION or version < 0:
        raise ManifestSchemaError(
            "incompatible_schema",
            f"unsupported manifest schema {version}; current schema is {SCHEMA_VERSION}",
        )
    while version < SCHEMA_VERSION:
        if version == 0:
            migrated = _migrate_v0_to_v1(migrated)
        else:
            raise ManifestSchemaError(
                "incompatible_schema",
                f"no migration registered from schema {version}",
            )
        version = migrated["schema_version"]
    return migrated


def manifest_to_document(manifest: ProjectManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "project": {
            "project_id": manifest.project.project_id,
            "normalized_gameinfo_path": manifest.project.normalized_gameinfo_path,
            "gameinfo_sha256": manifest.project.gameinfo_sha256,
        },
        "source_assets": [
            {
                "logical_model_path": item.logical_model_path,
                "source_fingerprint": item.source_fingerprint,
                "skin_families_fingerprint": item.skin_families_fingerprint,
            }
            for item in sorted(manifest.source_assets, key=lambda item: item.logical_model_path)
        ],
        "generated_models": [
            {
                "logical_source_model": item.logical_source_model,
                "compile_scale_percent": item.compile_scale_percent,
                "logical_output_model": item.logical_output_model,
                "requires_static_conversion": item.requires_static_conversion,
                "skin_layout_fingerprint": item.skin_layout_fingerprint,
                "expected_files": list(item.expected_files),
                "artifact_fingerprint": item.artifact_fingerprint,
            }
            for item in sorted(
                manifest.generated_models,
                key=lambda item: (
                    item.logical_source_model,
                    item.compile_scale_percent,
                    item.skin_layout_fingerprint,
                ),
            )
        ],
        "colored_materials": [
            {
                "logical_source_material": item.logical_source_material,
                "render_color": list(item.render_color),
                "color_parameter": item.color_parameter,
                "generation_mode": item.generation_mode,
                "logical_output_material": item.logical_output_material,
                "source_fingerprint": item.source_fingerprint,
                "artifact_sha256": item.artifact_sha256,
            }
            for item in sorted(
                manifest.colored_materials,
                key=lambda item: (item.logical_source_material, item.render_color),
            )
        ],
        "skin_mappings": [
            {
                "logical_source_model": item.logical_source_model,
                "source_skin": item.source_skin,
                "render_color": list(item.render_color),
                "target_skin": item.target_skin,
                "source_skin_families_fingerprint": (
                    item.source_skin_families_fingerprint
                ),
                "layout_fingerprint": item.layout_fingerprint,
            }
            for item in sorted(
                manifest.skin_mappings,
                key=lambda item: (
                    item.logical_source_model,
                    item.target_skin,
                    item.source_skin,
                    item.render_color,
                ),
            )
        ],
        "map_usages": [
            {
                "map_identity": item.map_identity,
                "entity_id": item.entity_id,
                "logical_source_model": item.logical_source_model,
                "raw_modelscale": item.raw_modelscale,
                "compile_scale_percent": item.compile_scale_percent,
                "source_skin": item.source_skin,
                "render_color": list(item.render_color),
                "logical_output_model": item.logical_output_model,
                "target_skin": item.target_skin,
            }
            for item in sorted(
                manifest.map_usages,
                key=lambda item: (item.map_identity, int(item.entity_id)),
            )
        ],
    }


def manifest_from_document(document: Any) -> ProjectManifest:
    obj = _object(document, "manifest")
    _exact_keys(obj, {
        "schema_version",
        "project",
        "source_assets",
        "generated_models",
        "colored_materials",
        "skin_mappings",
        "map_usages",
    }, "manifest")
    version = _integer(obj["schema_version"], "schema_version", minimum=0)
    if version != SCHEMA_VERSION:
        raise ManifestSchemaError(
            "incompatible_schema",
            f"manifest_from_document requires schema {SCHEMA_VERSION}, got {version}",
        )
    project = _parse_project(obj["project"])
    source_assets = tuple(
        _parse_source_asset(item)
        for item in _array(obj["source_assets"], "source_assets")
    )
    generated_models = tuple(
        _parse_generated_model(item)
        for item in _array(obj["generated_models"], "generated_models")
    )
    colored_materials = tuple(
        _parse_colored_material(item)
        for item in _array(obj["colored_materials"], "colored_materials")
    )
    skin_mappings = tuple(
        _parse_skin_mapping(item)
        for item in _array(obj["skin_mappings"], "skin_mappings")
    )
    map_usages = tuple(
        _parse_map_usage(item)
        for item in _array(obj["map_usages"], "map_usages")
    )
    _validate_unique(source_assets, lambda item: item.logical_model_path, "source asset")
    _validate_unique(
        generated_models,
        lambda item: (
            item.logical_source_model,
            item.compile_scale_percent,
            item.skin_layout_fingerprint,
        ),
        "generated model",
    )
    _validate_unique(
        colored_materials,
        lambda item: (item.logical_source_material, item.render_color),
        "colored material",
    )
    _validate_skin_mappings(skin_mappings)
    _validate_unique(
        map_usages,
        lambda item: (item.map_identity, item.entity_id),
        "map usage",
    )
    return ProjectManifest(
        schema_version=version,
        project=project,
        source_assets=source_assets,
        generated_models=generated_models,
        colored_materials=colored_materials,
        skin_mappings=skin_mappings,
        map_usages=map_usages,
    )


def _migrate_v0_to_v1(document: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(document)
    mappings = migrated.get("skin_mappings", [])
    if isinstance(mappings, list):
        for mapping in mappings:
            if isinstance(mapping, dict) and "final_skin_index" in mapping:
                mapping["target_skin"] = mapping.pop("final_skin_index")
    migrated.update({
        "schema_version": 1,
        "source_assets": migrated.get("source_assets", []),
        "generated_models": migrated.get("generated_models", []),
        "colored_materials": migrated.get("colored_materials", []),
        "skin_mappings": mappings,
        "map_usages": migrated.get("map_usages", []),
    })
    return migrated


def _parse_project(value: Any) -> ProjectIdentity:
    obj = _object(value, "project")
    _exact_keys(obj, {"project_id", "normalized_gameinfo_path", "gameinfo_sha256"}, "project")
    return ProjectIdentity(
        project_id=_hash(obj["project_id"], "project.project_id"),
        normalized_gameinfo_path=_string(
            obj["normalized_gameinfo_path"],
            "project.normalized_gameinfo_path",
        ),
        gameinfo_sha256=_hash(obj["gameinfo_sha256"], "project.gameinfo_sha256"),
    )


def _parse_source_asset(value: Any) -> SourceAssetRecord:
    obj = _record(value, {
        "logical_model_path", "source_fingerprint", "skin_families_fingerprint",
    }, "source_asset")
    return SourceAssetRecord(
        _logical_path(obj["logical_model_path"], "logical_model_path", ".mdl"),
        _hash(obj["source_fingerprint"], "source_fingerprint"),
        _hash(obj["skin_families_fingerprint"], "skin_families_fingerprint"),
    )


def _parse_generated_model(value: Any) -> GeneratedModelRecord:
    obj = _record(value, {
        "logical_source_model", "compile_scale_percent", "logical_output_model",
        "requires_static_conversion", "skin_layout_fingerprint", "expected_files",
        "artifact_fingerprint",
    }, "generated_model")
    return GeneratedModelRecord(
        _logical_path(obj["logical_source_model"], "logical_source_model", ".mdl"),
        _integer(obj["compile_scale_percent"], "compile_scale_percent", minimum=1),
        _logical_path(obj["logical_output_model"], "logical_output_model", ".mdl"),
        _boolean(obj["requires_static_conversion"], "requires_static_conversion"),
        _hash(obj["skin_layout_fingerprint"], "skin_layout_fingerprint"),
        tuple(
            _logical_path(item, "expected_files[]")
            for item in _array(obj["expected_files"], "expected_files")
        ),
        _hash(obj["artifact_fingerprint"], "artifact_fingerprint"),
    )


def _parse_colored_material(value: Any) -> ColoredMaterialRecord:
    obj = _record(value, {
        "logical_source_material", "render_color", "color_parameter",
        "generation_mode", "logical_output_material", "source_fingerprint",
        "artifact_sha256",
    }, "colored_material")
    parameter = _string(obj["color_parameter"], "color_parameter")
    if parameter not in {"$color", "$color2"}:
        raise ManifestSchemaError("invalid_manifest", f"invalid color_parameter {parameter!r}")
    mode = _string(obj["generation_mode"], "generation_mode")
    if mode not in {"patch", "full_copy"}:
        raise ManifestSchemaError("invalid_manifest", f"invalid generation_mode {mode!r}")
    return ColoredMaterialRecord(
        _logical_path(obj["logical_source_material"], "logical_source_material", ".vmt"),
        _color(obj["render_color"]),
        parameter,
        mode,
        _logical_path(obj["logical_output_material"], "logical_output_material", ".vmt"),
        _hash(obj["source_fingerprint"], "source_fingerprint"),
        _hash(obj["artifact_sha256"], "artifact_sha256"),
    )


def _parse_skin_mapping(value: Any) -> SkinMappingRecord:
    obj = _record(value, {
        "logical_source_model", "source_skin", "render_color", "target_skin",
        "source_skin_families_fingerprint", "layout_fingerprint",
    }, "skin_mapping")
    return SkinMappingRecord(
        _logical_path(obj["logical_source_model"], "logical_source_model", ".mdl"),
        _integer(obj["source_skin"], "source_skin", minimum=0),
        _color(obj["render_color"]),
        _integer(obj["target_skin"], "target_skin", minimum=0),
        _hash(
            obj["source_skin_families_fingerprint"],
            "source_skin_families_fingerprint",
        ),
        _hash(obj["layout_fingerprint"], "layout_fingerprint"),
    )


def _parse_map_usage(value: Any) -> MapUsageRecord:
    obj = _record(value, {
        "map_identity", "entity_id", "logical_source_model", "raw_modelscale",
        "compile_scale_percent", "source_skin", "render_color",
        "logical_output_model", "target_skin",
    }, "map_usage")
    raw_scale = obj["raw_modelscale"]
    if raw_scale is not None:
        raw_scale = _string(raw_scale, "raw_modelscale", allow_empty=True)
    entity_id = _string(obj["entity_id"], "entity_id")
    if not entity_id.isdecimal() or int(entity_id) <= 0:
        raise ManifestSchemaError("invalid_manifest", f"invalid entity_id {entity_id!r}")
    return MapUsageRecord(
        _string(obj["map_identity"], "map_identity"),
        entity_id,
        _logical_path(obj["logical_source_model"], "logical_source_model", ".mdl"),
        raw_scale,
        _integer(obj["compile_scale_percent"], "compile_scale_percent", minimum=1),
        _integer(obj["source_skin"], "source_skin", minimum=0),
        _color(obj["render_color"]),
        _logical_path(obj["logical_output_model"], "logical_output_model", ".mdl"),
        _integer(obj["target_skin"], "target_skin", minimum=0),
    )


def _validate_skin_mappings(mappings: tuple[SkinMappingRecord, ...]) -> None:
    _validate_unique(
        mappings,
        lambda item: (item.logical_source_model, item.source_skin, item.render_color),
        "skin mapping",
    )
    _validate_unique(
        mappings,
        lambda item: (item.logical_source_model, item.target_skin),
        "skin target",
    )
    per_model: dict[str, tuple[str, str]] = {}
    for item in mappings:
        pair = (
            item.source_skin_families_fingerprint,
            item.layout_fingerprint,
        )
        previous = per_model.setdefault(item.logical_source_model, pair)
        if previous != pair:
            raise ManifestSchemaError(
                "invalid_manifest",
                f"skin mappings for {item.logical_source_model!r} mix layout fingerprints",
            )


def _validate_unique(values: tuple[Any, ...], key: Any, label: str) -> None:
    seen: set[Any] = set()
    for item in values:
        identity = key(item)
        if identity in seen:
            raise ManifestSchemaError(
                "invalid_manifest",
                f"duplicate {label} identity {identity!r}",
            )
        seen.add(identity)


def _record(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    obj = _object(value, label)
    _exact_keys(obj, keys, label)
    return obj


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestSchemaError("invalid_manifest", f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestSchemaError("invalid_manifest", f"{label} must be an array")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ManifestSchemaError(
            "invalid_manifest",
            f"{label} keys mismatch; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}",
        )


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ManifestSchemaError("invalid_manifest", f"{label} must be a string")
    return value


def _integer(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestSchemaError(
            "invalid_manifest",
            f"{label} must be an integer >= {minimum}",
        )
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestSchemaError("invalid_manifest", f"{label} must be boolean")
    return value


def _hash(value: Any, label: str) -> str:
    text = _string(value, label)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ManifestSchemaError(
            "invalid_manifest",
            f"{label} must be a lowercase SHA-256 hex digest",
        )
    return text


def _color(value: Any) -> tuple[int, int, int]:
    channels = _array(value, "render_color")
    if len(channels) != 3:
        raise ManifestSchemaError("invalid_manifest", "render_color must have 3 channels")
    parsed: list[int] = []
    for channel in channels:
        integer = _integer(channel, "render_color[]", minimum=0)
        if integer > 255:
            raise ManifestSchemaError(
                "invalid_manifest",
                f"render_color channel must be within 0..255, got {channel!r}",
            )
        parsed.append(integer)
    return parsed[0], parsed[1], parsed[2]


def _logical_path(value: Any, label: str, suffix: str | None = None) -> str:
    text = _string(value, label).strip().replace("\\", "/").casefold()
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise ManifestSchemaError("invalid_manifest", f"{label} must be a logical path")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or ".." in parts:
        raise ManifestSchemaError("invalid_manifest", f"{label} escapes its logical root")
    normalized = "/".join(parts)
    if suffix is not None and not normalized.endswith(suffix):
        raise ManifestSchemaError(
            "invalid_manifest",
            f"{label} must end with {suffix!r}",
        )
    return normalized


__all__ = [
    "SCHEMA_VERSION",
    "ColoredMaterialRecord",
    "GeneratedModelRecord",
    "ManifestLoadResult",
    "ManifestLoadStatus",
    "ManifestSchemaError",
    "MapUsageRecord",
    "ProjectIdentity",
    "ProjectManifest",
    "SkinMappingRecord",
    "SourceAssetRecord",
    "build_project_identity",
    "empty_manifest",
    "load_manifest",
    "manifest_from_document",
    "manifest_to_document",
    "manifest_to_json",
    "migrate_manifest_document",
    "save_manifest_atomic",
]
