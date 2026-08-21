"""Fail-closed reuse planning for validated project-scoped artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from psr.assets import CompiledModelValidationError, validate_compiled_model
from psr.cache import ColoredMaterialRecord, GeneratedModelRecord, ProjectManifest
from psr.domain import canonical_scale_percent

from .discovery import PipelineDiagnostic
from .generation import model_artifact_fingerprint
from .materials import ColoredMaterialOperationPlan
from .planning import OperationPlan
from .skin_layout import SkinLayoutOperationPlan, source_asset_fingerprint


@dataclass(frozen=True, slots=True)
class ExistingArtifact:
    """One already-published file whose exact bytes are required by commit."""

    logical_path: str
    physical_path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ReusedModelArtifact:
    """A cached model record revalidated against source, layout, and files."""

    record: GeneratedModelRecord
    files: tuple[ExistingArtifact, ...]


@dataclass(frozen=True, slots=True)
class ReusedMaterialArtifact:
    """A cached colored material whose published bytes still match its record."""

    record: ColoredMaterialRecord
    file: ExistingArtifact


@dataclass(frozen=True, slots=True)
class ArtifactReusePlan:
    """Partition complete requirements into reusable hits and generation misses."""

    map_identity: str
    generation_operation: OperationPlan
    generation_materials: ColoredMaterialOperationPlan
    reused_models: tuple[ReusedModelArtifact, ...]
    reused_materials: tuple[ReusedMaterialArtifact, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]


def plan_artifact_reuse(
    game_directory: Path,
    manifest: ProjectManifest,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> ArtifactReusePlan:
    """Reuse only artifacts whose current identity and bytes are fully proven."""
    if len({operation.map_identity, materials.map_identity, skin_layout.map_identity}) != 1:
        raise ValueError("operation, material, and skin-layout plans belong to different maps")
    game = game_directory.resolve(strict=True)
    if not game.is_dir():
        raise ValueError(f"game directory is not a directory: {game}")

    diagnostics: list[PipelineDiagnostic] = []
    assets = {item.logical_model_path: item for item in operation.source_assets}
    layouts = {item.logical_source_model: item for item in skin_layout.layouts}
    source_records = {
        item.logical_model_path: item
        for item in manifest.source_assets
    }
    model_records = {
        (
            item.logical_source_model,
            item.compile_scale_percent,
            item.skin_layout_fingerprint,
        ): item
        for item in manifest.generated_models
    }

    reused_models: list[ReusedModelArtifact] = []
    missed_models = []
    for requirement in operation.generated_models:
        asset = assets.get(requirement.logical_source_model)
        layout = layouts.get(requirement.logical_source_model)
        if asset is None or layout is None:
            missed_models.append(requirement)
            continue
        source_record = source_records.get(requirement.logical_source_model)
        if (
            source_record is None
            or source_record.source_fingerprint != source_asset_fingerprint(asset)
        ):
            missed_models.append(requirement)
            continue
        key = (
            requirement.logical_source_model,
            canonical_scale_percent(requirement.compile_scale),
            layout.layout_fingerprint,
        )
        record = model_records.get(key)
        if record is None:
            missed_models.append(requirement)
            continue
        if (
            record.logical_output_model != requirement.logical_output_model
            or record.requires_static_conversion
            != requirement.requires_static_conversion
        ):
            diagnostics.append(PipelineDiagnostic(
                "warning",
                "cached_model_record_invalid",
                f"{requirement.logical_output_model}: cached identity does not match "
                "the current generation plan; rebuilding",
            ))
            missed_models.append(requirement)
            continue
        try:
            reused_models.append(_validate_cached_model(game, record, asset.has_physics))
        except (OSError, ValueError, CompiledModelValidationError) as exc:
            diagnostics.append(PipelineDiagnostic(
                "warning",
                "cached_model_artifact_invalid",
                f"{requirement.logical_output_model}: {type(exc).__name__}: {exc}; "
                "rebuilding",
            ))
            missed_models.append(requirement)

    accepted_outputs = _accepted_material_outputs(materials, skin_layout)
    material_records = {
        (item.logical_source_material, item.render_color): item
        for item in manifest.colored_materials
    }
    reused_materials: list[ReusedMaterialArtifact] = []
    missed_materials = []
    for planned in materials.colored_materials:
        if planned.logical_output_material not in accepted_outputs:
            continue
        record = material_records.get(
            (planned.logical_source_material, planned.render_color)
        )
        if record is None or record.source_fingerprint != planned.source_fingerprint:
            missed_materials.append(planned)
            continue
        if (
            record.logical_output_material != planned.logical_output_material
            or record.color_parameter != planned.color_parameter
            or record.generation_mode != planned.generation_mode
        ):
            diagnostics.append(PipelineDiagnostic(
                "warning",
                "cached_material_record_invalid",
                f"{planned.logical_output_material}: cached identity does not match "
                "the current generation plan; regenerating",
            ))
            missed_materials.append(planned)
            continue
        try:
            reused_materials.append(_validate_cached_material(game, record))
        except (OSError, ValueError) as exc:
            diagnostics.append(PipelineDiagnostic(
                "warning",
                "cached_material_artifact_invalid",
                f"{planned.logical_output_material}: {type(exc).__name__}: {exc}; "
                "regenerating",
            ))
            missed_materials.append(planned)

    return ArtifactReusePlan(
        map_identity=operation.map_identity,
        generation_operation=replace(
            operation,
            generated_models=tuple(missed_models),
        ),
        generation_materials=replace(
            materials,
            colored_materials=tuple(missed_materials),
        ),
        reused_models=tuple(reused_models),
        reused_materials=tuple(reused_materials),
        diagnostics=tuple(diagnostics),
    )


def _validate_cached_model(
    game: Path,
    record: GeneratedModelRecord,
    source_has_physics: bool,
) -> ReusedModelArtifact:
    base = PurePosixPath(record.logical_output_model)
    expected = tuple(record.expected_files)
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("cached expected_files is empty or contains duplicates")
    validation = validate_compiled_model(
        game,
        record.logical_output_model,
        requires_physics=source_has_physics,
    )
    canonical_expected = {item.logical_path for item in validation.files}
    if canonical_expected != set(expected) or any(
        PurePosixPath(path).parent != base.parent
        for path in expected
    ):
        raise ValueError("validated companion set differs from cached expected_files")
    if model_artifact_fingerprint(validation) != record.artifact_fingerprint:
        raise ValueError("current companion hashes differ from cached artifact fingerprint")
    files = tuple(
        ExistingArtifact(item.logical_path, item.physical_path, item.size, item.sha256)
        for item in validation.files
    )
    return ReusedModelArtifact(record, files)


def _validate_cached_material(
    game: Path,
    record: ColoredMaterialRecord,
) -> ReusedMaterialArtifact:
    physical = _managed_path(game, record.logical_output_material)
    if not physical.is_file():
        raise ValueError("published material is missing")
    size = physical.stat().st_size
    if size == 0:
        raise ValueError("published material is empty")
    sha256 = _file_sha256(physical)
    if sha256 != record.artifact_sha256:
        raise ValueError("published material hash differs from cache")
    return ReusedMaterialArtifact(
        record,
        ExistingArtifact(record.logical_output_material, physical, size, sha256),
    )


def _managed_path(game: Path, logical_path: str) -> Path:
    path = PurePosixPath(logical_path)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not logical_path.startswith("materials/models/psr_scaled/")
        or path.suffix != ".vmt"
    ):
        raise ValueError(f"unmanaged cached material path {logical_path!r}")
    physical = game.joinpath(*path.parts).resolve()
    if game not in physical.parents:
        raise ValueError(f"cached material escapes game directory: {logical_path!r}")
    return physical


def _accepted_material_outputs(
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> set[str]:
    accepted = {
        (mapping.logical_source_model, mapping.source_skin, mapping.render_color)
        for layout in skin_layout.layouts
        for mapping in layout.mappings
    }
    return {
        logical_path
        for colored_skin in materials.colored_skins
        if (
            colored_skin.logical_source_model,
            colored_skin.source_skin,
            colored_skin.render_color,
        ) in accepted
        for logical_path in colored_skin.logical_colored_materials
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ArtifactReusePlan",
    "ExistingArtifact",
    "ReusedMaterialArtifact",
    "ReusedModelArtifact",
    "plan_artifact_reuse",
]
