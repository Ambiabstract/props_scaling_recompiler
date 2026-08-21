"""Read-only VMT inspection and pure deterministic colored-material planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psr.assets import (
    ColorParameter,
    OrderedAssetFileSystem,
    SourceMaterialInspectionError,
    SourceMaterialMetadata,
    colored_material_path,
    inspect_source_material,
    select_color_parameter,
)

from .discovery import PipelineDiagnostic
from .planning import OperationPlan


@dataclass(frozen=True, slots=True)
class MaterialInspection:
    """All unique VMT metadata required by an operation plan."""

    source_materials: tuple[SourceMaterialMetadata, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class ColoredMaterialPlan:
    """One generated VMT identity and its deterministic generation policy."""

    logical_source_material: str
    logical_output_material: str
    render_color: tuple[int, int, int]
    color_parameter: ColorParameter
    color_assignment: Literal["insert", "replace"]
    generation_mode: Literal["patch", "full_copy"]
    generation_reason: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class ColoredSkinMaterialPlan:
    """Material rows for one future source-skin/RGB skin-family mapping."""

    logical_source_model: str
    source_skin: int
    render_color: tuple[int, int, int]
    material_slots: tuple[int, ...]
    logical_source_materials: tuple[str, ...]
    logical_colored_materials: tuple[str, ...]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ColoredMaterialOperationPlan:
    """Complete read-only material phase result for one map operation."""

    map_identity: str
    source_materials: tuple[SourceMaterialMetadata, ...]
    colored_materials: tuple[ColoredMaterialPlan, ...]
    colored_skins: tuple[ColoredSkinMaterialPlan, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]

    @property
    def is_valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def inspect_colored_material_sources(
    operation: OperationPlan,
    filesystem: OrderedAssetFileSystem,
) -> MaterialInspection:
    """Inspect each unique VMT needed by non-white skin requirements."""
    diagnostics: list[PipelineDiagnostic] = []
    required_paths = _required_source_material_paths(operation, diagnostics)
    materials: list[SourceMaterialMetadata] = []
    for logical_path in required_paths:
        try:
            materials.append(inspect_source_material(filesystem, logical_path))
        except SourceMaterialInspectionError as exc:
            diagnostics.append(PipelineDiagnostic(
                "error",
                exc.code,
                f"{logical_path}: {exc.detail}",
            ))
    return MaterialInspection(tuple(materials), tuple(diagnostics))


def build_colored_material_plan(
    operation: OperationPlan,
    inspection: MaterialInspection,
) -> ColoredMaterialOperationPlan:
    """Build a pure material plan from already normalised model/VMT metadata."""
    diagnostics = [*operation.diagnostics, *inspection.diagnostics]
    metadata_by_path = {
        item.logical_material_path: item
        for item in inspection.source_materials
    }
    source_paths_by_model = _source_paths_by_model(operation)

    requested: set[tuple[str, tuple[int, int, int]]] = set()
    for requirement in operation.colored_skins:
        paths = source_paths_by_model.get(requirement.logical_source_model, {})
        for material_name in requirement.source_materials:
            logical_path = paths.get(material_name)
            if logical_path is not None:
                requested.add((logical_path, requirement.render_color))

    colored_materials: list[ColoredMaterialPlan] = []
    for logical_path, color in sorted(requested):
        metadata = metadata_by_path.get(logical_path)
        if metadata is None:
            continue
        parameter = select_color_parameter(metadata)
        if parameter is None:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "unsupported_color_shader",
                f"{logical_path}: shader {metadata.effective_shader!r} has no confirmed "
                "$color/$color2 policy for Source SDK 2013 SP",
            ))
            continue
        parameter_names = {name for name, _value in metadata.parameters}
        assignment: Literal["insert", "replace"] = (
            "replace" if parameter in parameter_names else "insert"
        )
        if metadata.is_patch:
            mode: Literal["patch", "full_copy"] = "full_copy"
            reason = "source_is_patch_pending_sdk_patch_chain_validation"
        else:
            mode = "patch"
            reason = "direct_source_vmt"
        colored_materials.append(ColoredMaterialPlan(
            logical_source_material=logical_path,
            logical_output_material=colored_material_path(logical_path, color),
            render_color=color,
            color_parameter=parameter,
            color_assignment=assignment,
            generation_mode=mode,
            generation_reason=reason,
            source_fingerprint=metadata.dependency_fingerprint,
        ))

    plan_by_identity = {
        (item.logical_source_material, item.render_color): item
        for item in colored_materials
    }
    colored_skins: list[ColoredSkinMaterialPlan] = []
    for requirement in operation.colored_skins:
        paths = source_paths_by_model.get(requirement.logical_source_model, {})
        logical_sources: list[str] = []
        logical_outputs: list[str] = []
        complete = True
        for material_name in requirement.source_materials:
            logical_path = paths.get(material_name)
            if logical_path is None:
                complete = False
                break
            material_plan = plan_by_identity.get((logical_path, requirement.render_color))
            if material_plan is None:
                complete = False
                break
            logical_sources.append(logical_path)
            logical_outputs.append(material_plan.logical_output_material)
        if complete:
            colored_skins.append(ColoredSkinMaterialPlan(
                logical_source_model=requirement.logical_source_model,
                source_skin=requirement.source_skin,
                render_color=requirement.render_color,
                material_slots=requirement.material_slots,
                logical_source_materials=tuple(logical_sources),
                logical_colored_materials=tuple(logical_outputs),
                entity_ids=requirement.entity_ids,
            ))

    return ColoredMaterialOperationPlan(
        map_identity=operation.map_identity,
        source_materials=inspection.source_materials,
        colored_materials=tuple(colored_materials),
        colored_skins=tuple(colored_skins),
        diagnostics=tuple(diagnostics),
    )


def _required_source_material_paths(
    operation: OperationPlan,
    diagnostics: list[PipelineDiagnostic],
) -> tuple[str, ...]:
    paths_by_model = _source_paths_by_model(operation)
    required: set[str] = set()
    for requirement in operation.colored_skins:
        paths = paths_by_model.get(requirement.logical_source_model, {})
        for material_name in requirement.source_materials:
            logical_path = paths.get(material_name)
            if logical_path is None:
                diagnostics.append(PipelineDiagnostic(
                    "error",
                    "material_metadata_missing",
                    f"{requirement.logical_source_model}: no resolved VMT metadata for "
                    f"material slot {material_name!r}",
                    requirement.entity_ids[0] if requirement.entity_ids else None,
                ))
            else:
                required.add(logical_path)
    return tuple(sorted(required))


def _source_paths_by_model(
    operation: OperationPlan,
) -> dict[str, dict[str, str]]:
    return {
        asset.logical_model_path: {
            material.material_name: material.logical_path
            for material in asset.materials
            if material.logical_path is not None
        }
        for asset in operation.source_assets
    }


__all__ = [
    "ColoredMaterialOperationPlan",
    "ColoredMaterialPlan",
    "ColoredSkinMaterialPlan",
    "MaterialInspection",
    "build_colored_material_plan",
    "inspect_colored_material_sources",
]
