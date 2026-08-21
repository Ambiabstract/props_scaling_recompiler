"""Expand map-local generation needs to preserve project-wide cached artifacts."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from psr.cache import ProjectManifest
from psr.domain import resolve_geometry_scale, scaled_model_path

from .discovery import PipelineDiagnostic
from .planning import GeneratedModelRequirement, OperationPlan
from .skin_layout import SkinLayoutOperationPlan, source_asset_fingerprint


def reconcile_generation_requirements(
    operation: OperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    manifest: ProjectManifest,
) -> OperationPlan:
    """Include cached scales which must be rebuilt for a new source/layout.

    Generated filenames do not contain the skin-layout fingerprint. Whenever
    the source content or colored layout changes, every compatible cached
    scale for the touched model must therefore be compiled with the same new
    reference QC before publication can be atomic project-wide. A source-skin
    count increase explicitly preserves that scale set while rebasing colored
    rows; other cache resets discard it.
    """
    if operation.map_identity != skin_layout.map_identity:
        raise ValueError("operation and skin layout belong to different maps")
    diagnostics = list(operation.diagnostics)
    assets = {
        item.logical_model_path: item
        for item in operation.source_assets
    }
    source_records = {
        item.logical_model_path: item
        for item in manifest.source_assets
    }
    cached_by_model: dict[str, list] = {}
    for item in manifest.generated_models:
        cached_by_model.setdefault(item.logical_source_model, []).append(item)

    requirements = {
        (item.logical_source_model, item.compile_scale): item
        for item in operation.generated_models
    }
    for layout in skin_layout.layouts:
        model = layout.logical_source_model
        asset = assets.get(model)
        if asset is None:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "reconcile_source_asset_missing",
                f"{model}: skin layout has no inspected source asset",
            ))
            continue
        cached_models = cached_by_model.get(model, [])
        if not cached_models or (
            layout.cache_reset and not layout.rebuild_cached_scales
        ):
            continue
        source_record = source_records.get(model)
        source_changed = (
            source_record is None
            or source_record.source_fingerprint != source_asset_fingerprint(asset)
        )
        layout_changed = any(
            item.skin_layout_fingerprint != layout.layout_fingerprint
            for item in cached_models
        )
        if not source_changed and not layout_changed:
            continue

        for cached in cached_models:
            compile_scale = Decimal(cached.compile_scale_percent) / Decimal(100)
            expected_output = scaled_model_path(model, compile_scale)
            if cached.logical_output_model != expected_output:
                diagnostics.append(PipelineDiagnostic(
                    "error",
                    "cached_generated_model_path_mismatch",
                    f"{model}: cached scale {compile_scale} points to "
                    f"{cached.logical_output_model}, expected {expected_output}",
                ))
                continue
            expected_conversion = not asset.is_static_prop
            if cached.requires_static_conversion != expected_conversion:
                diagnostics.append(PipelineDiagnostic(
                    "error",
                    "cached_static_conversion_mismatch",
                    f"{model}: cached static-conversion state disagrees with source MDL",
                ))
                continue
            key = (model, compile_scale)
            if key in requirements:
                continue
            geometry = resolve_geometry_scale(
                compile_scale,
                bone_count=asset.bone_count,
                is_static_prop=asset.is_static_prop,
            )
            for item in geometry.diagnostics:
                diagnostics.append(PipelineDiagnostic(
                    "warning",
                    item.code,
                    f"{model}: cached scale {compile_scale}: {item.detail}",
                ))
            requirements[key] = GeneratedModelRequirement(
                logical_source_model=model,
                logical_output_model=expected_output,
                compile_scale=compile_scale,
                geometry_scale=geometry.geometry_scale,
                requires_static_conversion=expected_conversion,
                entity_ids=(),
            )

    return replace(
        operation,
        generated_models=tuple(sorted(
            requirements.values(),
            key=lambda item: (
                item.logical_source_model,
                item.compile_scale,
                item.logical_output_model,
            ),
        )),
        diagnostics=tuple(diagnostics),
    )


__all__ = ["reconcile_generation_requirements"]
