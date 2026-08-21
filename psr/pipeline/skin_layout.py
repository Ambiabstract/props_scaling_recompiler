"""Stable cache-backed skin-family layout planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace

from psr.assets import (
    MAX_STUDIO_MATERIALS,
    MAX_STUDIO_SKIN_FAMILIES,
    SourceAssetMetadata,
    colored_material_path,
)
from psr.cache import (
    MapUsageRecord,
    ProjectManifest,
    SkinMappingRecord,
    SourceAssetRecord,
)
from psr.domain import canonical_scale_percent

from .discovery import PipelineDiagnostic
from .materials import ColoredMaterialOperationPlan, ColoredSkinMaterialPlan
from .planning import OperationPlan, WHITE


@dataclass(frozen=True, slots=True)
class ModelSkinLayoutPlan:
    """Complete source and generated skin-family table for one model."""

    logical_source_model: str
    source_family_count: int
    source_skin_families_fingerprint: str
    families: tuple[tuple[str, ...], ...]
    mappings: tuple[SkinMappingRecord, ...]
    layout_fingerprint: str
    cache_reset: bool
    rebuild_cached_scales: bool


@dataclass(frozen=True, slots=True)
class EntitySkinAssignment:
    """Final skin index selected for one valid VMF usage."""

    entity_id: str
    logical_source_model: str
    source_skin: int
    render_color: tuple[int, int, int]
    target_skin: int
    logical_output_model: str
    used_color_fallback: bool


@dataclass(frozen=True, slots=True)
class SkinLayoutOperationPlan:
    """Pure proposed skin layouts and entity assignments for one map."""

    map_identity: str
    layouts: tuple[ModelSkinLayoutPlan, ...]
    assignments: tuple[EntitySkinAssignment, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]

    @property
    def is_valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def build_skin_layout_plan(
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    manifest: ProjectManifest,
) -> SkinLayoutOperationPlan:
    """Append new sorted mappings while retaining valid cache-assigned indices."""
    if operation.map_identity != materials.map_identity:
        raise ValueError("operation and colored-material plans belong to different maps")
    diagnostics = list(materials.diagnostics)
    usages_by_model: dict[str, list] = {}
    for usage in operation.usages:
        usages_by_model.setdefault(usage.request.logical_model_path, []).append(usage)
    assets = {
        asset.logical_model_path: asset
        for asset in operation.source_assets
    }
    requested_rows = {
        (
            item.logical_source_model,
            item.source_skin,
            item.render_color,
        ): item
        for item in materials.colored_skins
    }
    cached_by_model: dict[str, list[SkinMappingRecord]] = {}
    for mapping in manifest.skin_mappings:
        cached_by_model.setdefault(mapping.logical_source_model, []).append(mapping)

    layouts: list[ModelSkinLayoutPlan] = []
    mapping_by_identity: dict[
        tuple[str, int, tuple[int, int, int]],
        SkinMappingRecord,
    ] = {}
    rejected_keys_by_model: dict[
        str,
        set[tuple[int, tuple[int, int, int]]],
    ] = {}
    for model in sorted(usages_by_model):
        asset = assets[model]
        source_fingerprint = source_skin_families_fingerprint(asset)
        source_count = len(asset.skin_families)
        cached = sorted(
            cached_by_model.get(model, []),
            key=lambda item: item.target_skin,
        )
        valid_cached = _valid_cached_mappings(
            cached,
            source_fingerprint=source_fingerprint,
            source_family_count=source_count,
        )
        cache_reset = bool(cached and not valid_cached)
        rebuild_cached_scales = False
        if cached and not valid_cached:
            previous_source_count = _previous_source_family_count_for_increase(
                cached,
                source_fingerprint=source_fingerprint,
                source_family_count=source_count,
            )
            if previous_source_count is not None:
                shift = source_count - previous_source_count
                cached = [
                    replace(item, target_skin=item.target_skin + shift)
                    for item in cached
                ]
                rebuild_cached_scales = True
                diagnostics.append(PipelineDiagnostic(
                    "warning",
                    "source_skin_count_increased",
                    f"{model}: source skin-family count increased from "
                    f"{previous_source_count} to {source_count}; cached colored-skin "
                    f"mappings are shifted by {shift} and every cached scale must be "
                    "rebuilt",
                ))
            else:
                code = (
                    "source_skin_layout_changed"
                    if any(
                        item.source_skin_families_fingerprint != source_fingerprint
                        for item in cached
                    )
                    else "cached_skin_layout_invalid"
                )
                diagnostics.append(PipelineDiagnostic(
                    "warning",
                    code,
                    f"{model}: cached colored-skin mappings are rebuilt for the current "
                    "source skin-family table",
                ))
                cached = []

        target_by_key = {
            (item.source_skin, item.render_color): item.target_skin
            for item in cached
        }
        requested_keys = {
            (skin, color)
            for req_model, skin, color in requested_rows
            if req_model == model
        }
        family_by_target: dict[int, tuple[str, ...]] = {
            index: family
            for index, family in enumerate(asset.skin_families)
        }
        for (source_skin, color), target_skin in sorted(
            target_by_key.items(),
            key=lambda item: item[1],
        ):
            logical_materials = _colored_family(
                asset,
                source_skin,
                color,
                requested_rows.get((model, source_skin, color)),
            )
            if logical_materials is None:
                diagnostics.append(PipelineDiagnostic(
                    "error",
                    "skin_layout_material_missing",
                    f"{model}: cannot derive colored material row for source skin "
                    f"{source_skin}, RGB {color}",
                ))
                continue
            family_by_target[target_skin] = logical_materials

        rejected_keys: set[tuple[int, tuple[int, int, int]]] = set()
        next_target = source_count + len(cached)
        existing_materials = _family_materials(family_by_target.values())
        if next_target > MAX_STUDIO_SKIN_FAMILIES:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "cached_skin_family_limit_exceeded",
                f"{model}: existing source and cached colored rows require "
                f"{next_target} skin families, limit is {MAX_STUDIO_SKIN_FAMILIES}; "
                "an explicit colored-skin cleanup is required",
            ))
        if len(existing_materials) > MAX_STUDIO_MATERIALS:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "cached_model_material_limit_exceeded",
                f"{model}: existing source and cached colored rows require "
                f"{len(existing_materials)} unique materials, limit is "
                f"{MAX_STUDIO_MATERIALS}; an explicit colored-skin cleanup is required",
            ))

        for source_skin, color in sorted(requested_keys - set(target_by_key)):
            colored_plan = requested_rows[(model, source_skin, color)]
            logical_materials = _colored_family(
                asset,
                source_skin,
                color,
                colored_plan,
            )
            if logical_materials is None:
                diagnostics.append(PipelineDiagnostic(
                    "error",
                    "skin_layout_material_missing",
                    f"{model}: cannot derive colored material row for source skin "
                    f"{source_skin}, RGB {color}",
                ))
                continue
            proposed_materials = existing_materials | set(logical_materials)
            if next_target >= MAX_STUDIO_SKIN_FAMILIES:
                rejected_keys.add((source_skin, color))
                diagnostics.append(_capacity_fallback_warning(
                    model,
                    source_skin,
                    color,
                    colored_plan.entity_ids,
                    "skin_family_limit_reached",
                    f"skin-family limit {MAX_STUDIO_SKIN_FAMILIES} is already reached",
                ))
                continue
            if len(proposed_materials) > MAX_STUDIO_MATERIALS:
                rejected_keys.add((source_skin, color))
                diagnostics.append(_capacity_fallback_warning(
                    model,
                    source_skin,
                    color,
                    colored_plan.entity_ids,
                    "model_material_limit_reached",
                    f"colored row would require {len(proposed_materials)} unique "
                    f"materials, limit is {MAX_STUDIO_MATERIALS}",
                ))
                continue
            target_by_key[(source_skin, color)] = next_target
            family_by_target[next_target] = logical_materials
            existing_materials = proposed_materials
            next_target += 1
        rejected_keys_by_model[model] = rejected_keys

        expected_indexes = list(range(next_target))
        if sorted(family_by_target) != expected_indexes:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "skin_layout_not_contiguous",
                f"{model}: planned skin rows are not contiguous 0..{next_target - 1}",
            ))
            continue
        families = tuple(family_by_target[index] for index in expected_indexes)
        layout_fingerprint = _layout_fingerprint(model, families)
        mappings = tuple(
            SkinMappingRecord(
                logical_source_model=model,
                source_skin=source_skin,
                render_color=color,
                target_skin=target_skin,
                source_skin_families_fingerprint=source_fingerprint,
                layout_fingerprint=layout_fingerprint,
            )
            for (source_skin, color), target_skin in sorted(
                target_by_key.items(),
                key=lambda item: item[1],
            )
        )
        for mapping in mappings:
            mapping_by_identity[(model, mapping.source_skin, mapping.render_color)] = mapping
        layouts.append(ModelSkinLayoutPlan(
            logical_source_model=model,
            source_family_count=source_count,
            source_skin_families_fingerprint=source_fingerprint,
            families=families,
            mappings=mappings,
            layout_fingerprint=layout_fingerprint,
            cache_reset=cache_reset,
            rebuild_cached_scales=rebuild_cached_scales,
        ))

    assignments: list[EntitySkinAssignment] = []
    for usage in operation.usages:
        used_color_fallback = False
        if usage.render_color == WHITE:
            target_skin = usage.source_skin
        else:
            mapping = mapping_by_identity.get((
                usage.request.logical_model_path,
                usage.source_skin,
                usage.render_color,
            ))
            if mapping is None:
                if (usage.source_skin, usage.render_color) in rejected_keys_by_model.get(
                    usage.request.logical_model_path,
                    set(),
                ):
                    target_skin = usage.source_skin
                    used_color_fallback = True
                else:
                    diagnostics.append(PipelineDiagnostic(
                        "error",
                        "skin_mapping_missing",
                        f"no final skin mapping for model {usage.request.logical_model_path}, "
                        f"source skin {usage.source_skin}, RGB {usage.render_color}",
                        usage.request.entity_id,
                        usage.request.source_line,
                    ))
                    continue
            else:
                target_skin = mapping.target_skin
        assignments.append(EntitySkinAssignment(
            entity_id=usage.request.entity_id,
            logical_source_model=usage.request.logical_model_path,
            source_skin=usage.source_skin,
            render_color=usage.render_color,
            target_skin=target_skin,
            logical_output_model=usage.logical_output_model,
            used_color_fallback=used_color_fallback,
        ))

    return SkinLayoutOperationPlan(
        map_identity=operation.map_identity,
        layouts=tuple(layouts),
        assignments=tuple(assignments),
        diagnostics=tuple(diagnostics),
    )


def commit_skin_layout_plan(
    manifest: ProjectManifest,
    operation: OperationPlan,
    plan: SkinLayoutOperationPlan,
) -> ProjectManifest:
    """Return a manifest candidate after validated artifacts make this plan committable.

    This function performs no filesystem write.  The caller must invoke it only
    after generation/validation succeeds, then atomically save the returned
    manifest during the pipeline commit phase.
    """
    if not plan.is_valid:
        raise ValueError("invalid skin layout plan cannot be committed")
    if operation.map_identity != plan.map_identity:
        raise ValueError("operation and skin layout plans belong to different maps")
    touched_models = {item.logical_source_model for item in plan.layouts}
    reset_models = {
        item.logical_source_model
        for item in plan.layouts
        if item.cache_reset
    }
    mappings = [
        item
        for item in manifest.skin_mappings
        if item.logical_source_model not in touched_models
    ]
    for layout in plan.layouts:
        mappings.extend(layout.mappings)

    assignment_by_entity = {
        item.entity_id: item
        for item in plan.assignments
    }
    usages = [
        item
        for item in manifest.map_usages
        if item.map_identity != operation.map_identity
        and item.logical_source_model not in reset_models
    ]
    for usage in operation.usages:
        assignment = assignment_by_entity[usage.request.entity_id]
        usages.append(MapUsageRecord(
            map_identity=operation.map_identity,
            entity_id=usage.request.entity_id,
            logical_source_model=usage.request.logical_model_path,
            raw_modelscale=usage.request.raw_modelscale,
            compile_scale_percent=canonical_scale_percent(usage.compile_scale),
            source_skin=usage.source_skin,
            render_color=usage.render_color,
            logical_output_model=usage.logical_output_model,
            target_skin=assignment.target_skin,
        ))

    layout_by_model = {
        item.logical_source_model: item
        for item in plan.layouts
    }
    source_assets = [
        item
        for item in manifest.source_assets
        if item.logical_model_path not in touched_models
    ]
    for asset in operation.source_assets:
        layout = layout_by_model.get(asset.logical_model_path)
        if layout is None:
            continue
        source_assets.append(SourceAssetRecord(
            logical_model_path=asset.logical_model_path,
            source_fingerprint=source_asset_fingerprint(asset),
            skin_families_fingerprint=layout.source_skin_families_fingerprint,
        ))

    return replace(
        manifest,
        source_assets=tuple(sorted(
            source_assets,
            key=lambda item: item.logical_model_path,
        )),
        skin_mappings=tuple(sorted(
            mappings,
            key=lambda item: (item.logical_source_model, item.target_skin),
        )),
        map_usages=tuple(sorted(
            usages,
            key=lambda item: (item.map_identity, int(item.entity_id)),
        )),
    )


def source_skin_families_fingerprint(asset: SourceAssetMetadata) -> str:
    """Fingerprint only data that controls original/colored skin row identity."""
    material_paths = {
        item.material_name: item.logical_path
        for item in asset.materials
    }
    payload = {
        "skin_families": [list(family) for family in asset.skin_families],
        "used_material_slots": list(asset.used_material_slots),
        "material_paths": [
            [name, material_paths.get(name)]
            for name in sorted(material_paths)
        ],
    }
    return _canonical_fingerprint(payload)


def _valid_cached_mappings(
    mappings: list[SkinMappingRecord],
    *,
    source_fingerprint: str,
    source_family_count: int,
) -> bool:
    if not mappings:
        return True
    if any(
        item.source_skin_families_fingerprint != source_fingerprint
        or item.source_skin >= source_family_count
        or item.render_color == WHITE
        for item in mappings
    ):
        return False
    targets = [item.target_skin for item in mappings]
    return targets == list(range(source_family_count, source_family_count + len(mappings)))


def _previous_source_family_count_for_increase(
    mappings: list[SkinMappingRecord],
    *,
    source_fingerprint: str,
    source_family_count: int,
) -> int | None:
    """Infer and validate a safe original-row count increase from cached mappings."""
    if not mappings:
        return None
    previous_source_count = mappings[0].target_skin
    if previous_source_count < 1 or source_family_count <= previous_source_count:
        return None
    if any(
        item.source_skin_families_fingerprint == source_fingerprint
        or item.source_skin < 0
        or item.source_skin >= previous_source_count
        or item.render_color == WHITE
        for item in mappings
    ):
        return None
    if len({item.source_skin_families_fingerprint for item in mappings}) != 1:
        return None
    if len({item.layout_fingerprint for item in mappings}) != 1:
        return None
    identities = {
        (item.source_skin, item.render_color)
        for item in mappings
    }
    if len(identities) != len(mappings):
        return None
    targets = [item.target_skin for item in mappings]
    expected = list(range(previous_source_count, previous_source_count + len(mappings)))
    if targets != expected:
        return None
    return previous_source_count


def _derive_colored_family(
    asset: SourceAssetMetadata,
    source_skin: int,
    color: tuple[int, int, int],
) -> tuple[str, ...] | None:
    if not 0 <= source_skin < len(asset.skin_families):
        return None
    material_paths = {
        item.material_name: item.logical_path
        for item in asset.materials
    }
    result = list(asset.skin_families[source_skin])
    for slot in asset.used_material_slots:
        material_name = result[slot]
        logical_path = material_paths.get(material_name)
        if logical_path is None:
            return None
        result[slot] = _qc_material_name(colored_material_path(logical_path, color))
    return tuple(result)


def _colored_family(
    asset: SourceAssetMetadata,
    source_skin: int,
    color: tuple[int, int, int],
    plan: ColoredSkinMaterialPlan | None,
) -> tuple[str, ...] | None:
    if plan is None:
        return _derive_colored_family(asset, source_skin, color)
    return _compose_colored_family(
        asset,
        source_skin,
        plan.material_slots,
        plan.logical_colored_materials,
    )


def _family_materials(
    families: Iterable[tuple[str, ...]],
) -> set[str]:
    return {
        material
        for family in families
        for material in family
    }


def _capacity_fallback_warning(
    model: str,
    source_skin: int,
    color: tuple[int, int, int],
    entity_ids: tuple[str, ...],
    code: str,
    reason: str,
) -> PipelineDiagnostic:
    entities = ", ".join(entity_ids) if entity_ids else "unknown"
    return PipelineDiagnostic(
        "warning",
        code,
        f"{model}: colored variation from source skin {source_skin}, RGB {color} "
        f"is omitted because {reason}; entities {entities} fall back to original "
        f"skin {source_skin}",
        entity_ids[0] if entity_ids else None,
    )


def _compose_colored_family(
    asset: SourceAssetMetadata,
    source_skin: int,
    material_slots: tuple[int, ...],
    logical_colored_materials: tuple[str, ...],
) -> tuple[str, ...] | None:
    if (
        not 0 <= source_skin < len(asset.skin_families)
        or material_slots != asset.used_material_slots
        or len(material_slots) != len(logical_colored_materials)
    ):
        return None
    result = list(asset.skin_families[source_skin])
    for slot, logical_path in zip(material_slots, logical_colored_materials):
        if not 0 <= slot < len(result):
            return None
        result[slot] = _qc_material_name(logical_path)
    return tuple(result)


def _qc_material_name(logical_vmt_path: str) -> str:
    normalized = logical_vmt_path.replace("\\", "/").casefold()
    if not normalized.startswith("materials/") or not normalized.endswith(".vmt"):
        raise ValueError(f"invalid logical VMT path for QC skin row: {logical_vmt_path!r}")
    return normalized.removeprefix("materials/").removesuffix(".vmt")


def _layout_fingerprint(
    logical_source_model: str,
    families: tuple[tuple[str, ...], ...],
) -> str:
    return _canonical_fingerprint({
        "logical_source_model": logical_source_model,
        "families": [list(family) for family in families],
    })


def source_asset_fingerprint(asset: SourceAssetMetadata) -> str:
    return _canonical_fingerprint({
        "logical_model_path": asset.logical_model_path,
        "mdl_version": asset.mdl_version,
        "mdl_header_checksum": asset.mdl_header_checksum,
        "files": [
            [item.logical_path, item.sha256]
            for item in asset.files
        ],
    })


def _canonical_fingerprint(value: object) -> str:
    data = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "EntitySkinAssignment",
    "ModelSkinLayoutPlan",
    "SkinLayoutOperationPlan",
    "build_skin_layout_plan",
    "commit_skin_layout_plan",
    "source_asset_fingerprint",
    "source_skin_families_fingerprint",
]
