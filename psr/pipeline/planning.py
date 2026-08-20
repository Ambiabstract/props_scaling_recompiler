"""Pure deterministic operation planning from inspected VMF requests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping

from psr.assets import SourceAssetMetadata

from .discovery import (
    InspectedMap,
    PipelineDiagnostic,
    VmfEntityRequest,
)


WHITE = (255, 255, 255)


@dataclass(frozen=True, slots=True)
class MapUsagePlan:
    """Resolved intent for one VMF entity; no output path is committed yet."""

    request: VmfEntityRequest
    compile_scale: Decimal
    source_skin: int
    render_color: tuple[int, int, int]
    operation: Literal["reuse_original", "generate_model"]


@dataclass(frozen=True, slots=True)
class GeneratedModelRequirement:
    """One unique source/compile-scale model requirement."""

    logical_source_model: str
    compile_scale: Decimal
    requires_static_conversion: bool
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ColoredSkinRequirement:
    """One source-skin/RGB mapping shared by every requested model scale."""

    logical_source_model: str
    source_skin: int
    render_color: tuple[int, int, int]
    source_materials: tuple[str, ...]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationPlan:
    """Immutable pre-generation plan with all currently known identities."""

    map_identity: str
    vmf_sha256: str
    source_assets: tuple[SourceAssetMetadata, ...]
    usages: tuple[MapUsagePlan, ...]
    generated_models: tuple[GeneratedModelRequirement, ...]
    colored_skins: tuple[ColoredSkinRequirement, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]
    requires_vmf_output: bool = True

    @property
    def is_valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def build_operation_plan(
    inspected: InspectedMap,
    compile_scales: Mapping[str, Decimal],
) -> OperationPlan:
    """Build a side-effect-free plan from explicit, externally resolved scales.

    Hammer++ scale parsing and suffix formatting are deliberately outside this
    function until their empirical contract is complete. Raw strings remain in
    each ``VmfEntityRequest``; callers provide only the resulting compile scale.
    """
    diagnostics = list(inspected.diagnostics)
    assets = {
        asset.logical_model_path: asset
        for asset in inspected.source_assets
    }
    usages: list[MapUsagePlan] = []

    for request in inspected.discovery.requests:
        asset = assets.get(request.logical_model_path)
        if asset is None:
            continue
        compile_scale = compile_scales.get(request.entity_id)
        if compile_scale is None:
            diagnostics.append(_request_error(
                request,
                "compile_scale_unresolved",
                "no empirically resolved PSR compile scale was supplied",
            ))
            continue
        if not isinstance(compile_scale, Decimal) or not compile_scale.is_finite():
            diagnostics.append(_request_error(
                request,
                "invalid_compile_scale",
                f"compile scale must be a finite Decimal, got {compile_scale!r}",
            ))
            continue
        if compile_scale < Decimal("0.01"):
            diagnostics.append(_request_error(
                request,
                "invalid_compile_scale",
                f"compile scale {compile_scale} is below the approved PSR minimum 0.01",
            ))
            continue
        source_skin = _parse_skin(request, asset, diagnostics)
        render_color = _parse_color(request, diagnostics)
        if source_skin is None or render_color is None:
            continue
        if render_color != WHITE and not _materials_available(
            request,
            asset,
            source_skin,
            diagnostics,
        ):
            continue
        operation: Literal["reuse_original", "generate_model"]
        if asset.is_static_prop and compile_scale == 1 and render_color == WHITE:
            operation = "reuse_original"
        else:
            operation = "generate_model"
        usages.append(MapUsagePlan(
            request,
            compile_scale,
            source_skin,
            render_color,
            operation,
        ))

    generated_models = _group_generated_models(usages, assets)
    colored_skins = _group_colored_skins(usages, assets)
    return OperationPlan(
        map_identity=inspected.discovery.map_identity,
        vmf_sha256=inspected.discovery.vmf_sha256,
        source_assets=inspected.source_assets,
        usages=tuple(usages),
        generated_models=generated_models,
        colored_skins=colored_skins,
        diagnostics=tuple(diagnostics),
    )


def _parse_skin(
    request: VmfEntityRequest,
    asset: SourceAssetMetadata,
    diagnostics: list[PipelineDiagnostic],
) -> int | None:
    try:
        value = int(request.raw_skin, 10)
    except ValueError:
        diagnostics.append(_request_error(
            request,
            "invalid_skin",
            f"skin must be a decimal integer, got {request.raw_skin!r}",
        ))
        return None
    if not 0 <= value < len(asset.skin_families):
        diagnostics.append(_request_error(
            request,
            "skin_out_of_range",
            f"skin {value} is outside 0..{len(asset.skin_families) - 1}",
        ))
        return None
    return value


def _parse_color(
    request: VmfEntityRequest,
    diagnostics: list[PipelineDiagnostic],
) -> tuple[int, int, int] | None:
    parts = request.raw_rendercolor.split()
    if len(parts) != 3:
        diagnostics.append(_request_error(
            request,
            "invalid_rendercolor",
            f"rendercolor must contain three channels, got {request.raw_rendercolor!r}",
        ))
        return None
    try:
        color = tuple(int(part, 10) for part in parts)
    except ValueError:
        diagnostics.append(_request_error(
            request,
            "invalid_rendercolor",
            f"rendercolor channels must be integers, got {request.raw_rendercolor!r}",
        ))
        return None
    if any(not 0 <= channel <= 255 for channel in color):
        diagnostics.append(_request_error(
            request,
            "rendercolor_out_of_range",
            f"rendercolor channels must be within 0..255, got {request.raw_rendercolor!r}",
        ))
        return None
    return color[0], color[1], color[2]


def _materials_available(
    request: VmfEntityRequest,
    asset: SourceAssetMetadata,
    source_skin: int,
    diagnostics: list[PipelineDiagnostic],
) -> bool:
    available = {
        material.material_name: material.logical_path is not None
        for material in asset.materials
    }
    missing = tuple(
        material
        for material in asset.skin_families[source_skin]
        if not available.get(material, False)
    )
    if not missing:
        return True
    diagnostics.append(_request_error(
        request,
        "material_not_found",
        "non-white request needs unresolved source materials: " + ", ".join(missing),
    ))
    return False


def _group_generated_models(
    usages: list[MapUsagePlan],
    assets: Mapping[str, SourceAssetMetadata],
) -> tuple[GeneratedModelRequirement, ...]:
    grouped: dict[tuple[str, Decimal], list[str]] = {}
    for usage in usages:
        if usage.operation != "generate_model":
            continue
        key = (usage.request.logical_model_path, usage.compile_scale)
        grouped.setdefault(key, []).append(usage.request.entity_id)
    return tuple(
        GeneratedModelRequirement(
            logical_source_model=model,
            compile_scale=scale,
            requires_static_conversion=not assets[model].is_static_prop,
            entity_ids=tuple(grouped[(model, scale)]),
        )
        for model, scale in sorted(grouped)
    )


def _group_colored_skins(
    usages: list[MapUsagePlan],
    assets: Mapping[str, SourceAssetMetadata],
) -> tuple[ColoredSkinRequirement, ...]:
    grouped: dict[tuple[str, int, tuple[int, int, int]], list[str]] = {}
    for usage in usages:
        if usage.render_color == WHITE:
            continue
        key = (
            usage.request.logical_model_path,
            usage.source_skin,
            usage.render_color,
        )
        grouped.setdefault(key, []).append(usage.request.entity_id)
    return tuple(
        ColoredSkinRequirement(
            logical_source_model=model,
            source_skin=skin,
            render_color=color,
            source_materials=assets[model].skin_families[skin],
            entity_ids=tuple(grouped[(model, skin, color)]),
        )
        for model, skin, color in sorted(grouped)
    )


def _request_error(
    request: VmfEntityRequest,
    code: str,
    detail: str,
) -> PipelineDiagnostic:
    return PipelineDiagnostic(
        "error",
        code,
        detail,
        request.entity_id,
        request.source_line,
    )


__all__ = [
    "ColoredSkinRequirement",
    "GeneratedModelRequirement",
    "MapUsagePlan",
    "OperationPlan",
    "WHITE",
    "build_operation_plan",
]
