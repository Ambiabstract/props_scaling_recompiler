"""Pure reference/scaled QC artifact planning."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Mapping

from psr.assets import (
    QCTransformError,
    build_reference_qc,
    build_scaled_qc,
    inspect_qc,
    normalize_logical_path,
)

from .discovery import PipelineDiagnostic
from .planning import OperationPlan
from .skin_layout import SkinLayoutOperationPlan


@dataclass(frozen=True, slots=True)
class ReferenceQCArtifactPlan:
    """One shared transformed decompile used to derive every scale variant."""

    logical_source_model: str
    staging_relative_path: str
    source_qc_sha256: str
    output_qc_sha256: str
    skin_layout_fingerprint: str
    requires_static_conversion: bool
    content: bytes
    mutations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScaledQCArtifactPlan:
    """One compile-ready QC for a deterministic generated model identity."""

    logical_source_model: str
    logical_output_model: str
    compile_scale: Decimal
    staging_relative_path: str
    reference_qc_sha256: str
    output_qc_sha256: str
    content: bytes
    mutations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QCOperationPlan:
    """All in-memory QC artifacts required by one map operation."""

    map_identity: str
    references: tuple[ReferenceQCArtifactPlan, ...]
    variants: tuple[ScaledQCArtifactPlan, ...]
    diagnostics: tuple[PipelineDiagnostic, ...]

    @property
    def is_valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def build_qc_operation_plan(
    operation: OperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    source_qcs: Mapping[str, bytes],
) -> QCOperationPlan:
    """Build reference and variant QC bytes without filesystem writes or tools."""
    if operation.map_identity != skin_layout.map_identity:
        raise ValueError("operation and skin layout plans belong to different maps")
    diagnostics = list(skin_layout.diagnostics)
    assets = {asset.logical_model_path: asset for asset in operation.source_assets}
    layouts = {layout.logical_source_model: layout for layout in skin_layout.layouts}
    qc_by_model: dict[str, bytes] = {}
    for raw_path, content in source_qcs.items():
        try:
            logical_path = normalize_logical_path(raw_path)
        except ValueError as exc:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "source_qc_model_path_invalid",
                f"{raw_path!r}: {exc}",
            ))
            continue
        if logical_path in qc_by_model:
            diagnostics.append(PipelineDiagnostic(
                "error",
                "source_qc_duplicate",
                f"multiple source QC byte streams were supplied for {logical_path}",
            ))
            continue
        qc_by_model[logical_path] = content

    requirements_by_model: dict[str, list] = {}
    for requirement in operation.generated_models:
        requirements_by_model.setdefault(requirement.logical_source_model, []).append(requirement)

    references: list[ReferenceQCArtifactPlan] = []
    variants: list[ScaledQCArtifactPlan] = []
    for model in sorted(requirements_by_model):
        asset = assets.get(model)
        if asset is None:
            diagnostics.append(_model_error(
                "qc_source_asset_missing",
                model,
                "generated model requirement has no inspected SourceAsset metadata",
            ))
            continue
        layout = layouts.get(model)
        if layout is None:
            diagnostics.append(_model_error(
                "qc_skin_layout_missing",
                model,
                "generated model requirement has no stable skin layout",
            ))
            continue
        source_qc = qc_by_model.get(model)
        if source_qc is None:
            diagnostics.append(_model_error(
                "source_qc_missing",
                model,
                "no staged/decompiled QC bytes were supplied",
            ))
            continue

        try:
            inspected = inspect_qc(source_qc)
            if inspected.is_static_prop != asset.is_static_prop:
                diagnostics.append(_model_error(
                    "qc_static_flag_mismatch",
                    model,
                    "decompiled QC $staticprop state disagrees with inspected MDL flags",
                ))
                continue
            reference_result = build_reference_qc(
                source_qc,
                expected_source_families=asset.skin_families,
                target_families=layout.families,
                require_staticprop=True,
            )
        except QCTransformError as exc:
            diagnostics.append(_transform_error(model, "reference", exc))
            continue

        reference = ReferenceQCArtifactPlan(
            logical_source_model=model,
            staging_relative_path=_reference_staging_path(model),
            source_qc_sha256=reference_result.source_sha256,
            output_qc_sha256=reference_result.output_sha256,
            skin_layout_fingerprint=layout.layout_fingerprint,
            requires_static_conversion=not asset.is_static_prop,
            content=reference_result.data,
            mutations=reference_result.mutations,
        )
        references.append(reference)

        for requirement in sorted(
            requirements_by_model[model],
            key=lambda item: (item.compile_scale, item.logical_output_model),
        ):
            try:
                variant_result = build_scaled_qc(
                    reference.content,
                    logical_output_model=requirement.logical_output_model,
                    compile_scale=requirement.compile_scale,
                )
            except QCTransformError as exc:
                diagnostics.append(_transform_error(
                    model,
                    requirement.logical_output_model,
                    exc,
                ))
                continue
            variants.append(ScaledQCArtifactPlan(
                logical_source_model=model,
                logical_output_model=requirement.logical_output_model,
                compile_scale=requirement.compile_scale,
                staging_relative_path=_variant_staging_path(
                    requirement.logical_output_model,
                ),
                reference_qc_sha256=reference.output_qc_sha256,
                output_qc_sha256=variant_result.output_sha256,
                content=variant_result.data,
                mutations=variant_result.mutations,
            ))

    return QCOperationPlan(
        map_identity=operation.map_identity,
        references=tuple(references),
        variants=tuple(variants),
        diagnostics=tuple(diagnostics),
    )


def _reference_staging_path(logical_model: str) -> str:
    relative = PurePosixPath(logical_model.removeprefix("models/"))
    return str(PurePosixPath("reference", relative).with_suffix(".qc"))


def _variant_staging_path(logical_model: str) -> str:
    relative = PurePosixPath(logical_model.removeprefix("models/"))
    return str(PurePosixPath("variants", relative).with_suffix(".qc"))


def _model_error(code: str, model: str, detail: str) -> PipelineDiagnostic:
    return PipelineDiagnostic("error", code, f"{model}: {detail}")


def _transform_error(model: str, stage: str, exc: QCTransformError) -> PipelineDiagnostic:
    return _model_error(
        f"qc_{exc.code}",
        model,
        f"{stage}: {exc.detail}",
    )


__all__ = [
    "QCOperationPlan",
    "ReferenceQCArtifactPlan",
    "ScaledQCArtifactPlan",
    "build_qc_operation_plan",
]
