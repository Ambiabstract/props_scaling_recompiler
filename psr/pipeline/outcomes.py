"""Pure fail-soft work ledger and minimal dependency-closure helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .materials import ColoredMaterialOperationPlan
from .planning import OperationPlan
from .skin_layout import SkinLayoutOperationPlan


FailureScope = Literal[
    "entity",
    "colored_skin",
    "material",
    "model_variant",
    "source_model",
]


@dataclass(frozen=True, slots=True)
class WorkFailure:
    """One failed work unit before dependency closure is applied."""

    scope: FailureScope
    code: str
    detail: str
    entity_id: str | None = None
    logical_source_model: str | None = None
    logical_output_model: str | None = None
    logical_material: str | None = None
    source_skin: int | None = None
    render_color: tuple[int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class OutcomeLedger:
    """All independent failures and their resolved affected entity IDs."""

    failures: tuple[WorkFailure, ...] = ()

    def affected_entity_ids(
        self,
        operation: OperationPlan,
        materials: ColoredMaterialOperationPlan,
    ) -> frozenset[str]:
        affected: set[str] = set()
        usages_by_model: dict[str, set[str]] = {}
        variants: dict[str, set[str]] = {}
        colored: dict[tuple[str, int, tuple[int, int, int]], set[str]] = {}
        material_users: dict[str, set[str]] = {}
        for usage in operation.usages:
            usages_by_model.setdefault(
                usage.request.logical_model_path, set()
            ).add(usage.request.entity_id)
        for requirement in operation.generated_models:
            variants.setdefault(requirement.logical_output_model, set()).update(
                requirement.entity_ids
            )
        for requirement in operation.colored_skins:
            colored.setdefault((
                requirement.logical_source_model,
                requirement.source_skin,
                requirement.render_color,
            ), set()).update(requirement.entity_ids)
        for colored_skin in materials.colored_skins:
            for logical_material in colored_skin.logical_colored_materials:
                material_users.setdefault(logical_material, set()).update(
                    colored_skin.entity_ids
                )

        for failure in self.failures:
            if failure.entity_id is not None:
                affected.add(failure.entity_id)
            if failure.scope == "entity":
                continue
            if (
                failure.scope == "source_model"
                and failure.logical_source_model is not None
            ):
                affected.update(usages_by_model.get(failure.logical_source_model, ()))
            elif (
                failure.scope == "model_variant"
                and failure.logical_output_model is not None
            ):
                affected.update(variants.get(failure.logical_output_model, ()))
            elif failure.scope == "material" and failure.logical_material is not None:
                affected.update(material_users.get(failure.logical_material, ()))
            elif (
                failure.scope == "colored_skin"
                and failure.logical_source_model is not None
                and failure.source_skin is not None
                and failure.render_color is not None
            ):
                affected.update(colored.get((
                    failure.logical_source_model,
                    failure.source_skin,
                    failure.render_color,
                ), ()))
        return frozenset(affected)


def filter_operation_plan(
    operation: OperationPlan,
    excluded_entity_ids: set[str] | frozenset[str],
) -> OperationPlan:
    """Keep only work required by non-failed entities."""
    excluded = set(excluded_entity_ids)
    usages = tuple(
        item for item in operation.usages
        if item.request.entity_id not in excluded
    )
    used_models = {item.request.logical_model_path for item in usages}
    generated_models = tuple(
        replace(
            item,
            entity_ids=tuple(
                entity_id for entity_id in item.entity_ids
                if entity_id not in excluded
            ),
        )
        for item in operation.generated_models
        if any(entity_id not in excluded for entity_id in item.entity_ids)
    )
    colored_skins = tuple(
        replace(
            item,
            entity_ids=tuple(
                entity_id for entity_id in item.entity_ids
                if entity_id not in excluded
            ),
        )
        for item in operation.colored_skins
        if any(entity_id not in excluded for entity_id in item.entity_ids)
    )
    return replace(
        operation,
        source_assets=tuple(
            item for item in operation.source_assets
            if item.logical_model_path in used_models
        ),
        usages=usages,
        generated_models=generated_models,
        colored_skins=colored_skins,
        diagnostics=tuple(
            item for item in operation.diagnostics
            if item.severity != "error"
            and (item.entity_id is None or item.entity_id not in excluded)
        ),
    )


def filter_material_plan(
    materials: ColoredMaterialOperationPlan,
    operation: OperationPlan,
) -> ColoredMaterialOperationPlan:
    """Restrict a material plan to the surviving colored usages."""
    identities = {
        (item.logical_source_model, item.source_skin, item.render_color)
        for item in operation.colored_skins
    }
    entity_ids = {item.request.entity_id for item in operation.usages}
    colored_skins = tuple(
        replace(
            item,
            entity_ids=tuple(
                entity_id for entity_id in item.entity_ids
                if entity_id in entity_ids
            ),
        )
        for item in materials.colored_skins
        if (item.logical_source_model, item.source_skin, item.render_color) in identities
    )
    outputs = {
        logical_path
        for item in colored_skins
        for logical_path in item.logical_colored_materials
    }
    colored_materials = tuple(
        item for item in materials.colored_materials
        if item.logical_output_material in outputs
    )
    sources = {item.logical_source_material for item in colored_materials}
    return replace(
        materials,
        source_materials=tuple(
            item for item in materials.source_materials
            if item.logical_material_path in sources
        ),
        colored_materials=colored_materials,
        colored_skins=colored_skins,
        diagnostics=tuple(
            item for item in materials.diagnostics
            if item.severity != "error"
            and (item.entity_id is None or item.entity_id in entity_ids)
        ),
    )


def filter_skin_layout_plan(
    plan: SkinLayoutOperationPlan,
    operation: OperationPlan,
) -> SkinLayoutOperationPlan:
    """Restrict assignments/layouts after variation-level generation failures."""
    entity_ids = {item.request.entity_id for item in operation.usages}
    layout_models = {
        item.request.logical_model_path
        for item in operation.usages
        if item.operation != "reuse_dynamic"
    }
    return replace(
        plan,
        layouts=tuple(
            item for item in plan.layouts
            if item.logical_source_model in layout_models
        ),
        assignments=tuple(
            item for item in plan.assignments
            if item.entity_id in entity_ids
        ),
        diagnostics=tuple(
            item for item in plan.diagnostics
            if item.severity != "error"
            and (item.entity_id is None or item.entity_id in entity_ids)
        ),
    )


__all__ = [
    "FailureScope",
    "OutcomeLedger",
    "WorkFailure",
    "filter_material_plan",
    "filter_operation_plan",
    "filter_skin_layout_plan",
]
