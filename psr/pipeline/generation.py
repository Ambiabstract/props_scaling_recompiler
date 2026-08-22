"""Execute an already-planned operation entirely inside isolated staging."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from psr.assets import (
    CompiledModelValidation,
    CompiledModelValidationError,
    CrowbarDecompileResult,
    GeneratedMaterialContent,
    MaterialGenerationError,
    OrderedAssetFileSystem,
    SourceMaterialInspectionError,
    ToolExecutionError,
    ToolInvocation,
    generate_colored_material,
    inspect_source_material,
    run_crowbar_decompile,
    run_studiomdl_compile,
    validate_compiled_model,
)

from .materials import ColoredMaterialOperationPlan
from .planning import GeneratedModelRequirement, OperationPlan
from .qc import (
    QCOperationPlan,
    ScaledQCArtifactPlan,
    build_qc_operation_plan,
)
from .skin_layout import SkinLayoutOperationPlan
from .staging import (
    StagedFile,
    StagingError,
    StagingWorkspace,
    stage_qc_operation,
    stage_source_model,
)


class GenerationError(RuntimeError):
    """A categorised all-or-nothing staged generation failure."""

    def __init__(
        self,
        code: str,
        stage: str,
        detail: str,
        *,
        logical_path: str | None = None,
        invocation: ToolInvocation | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.detail = detail
        self.logical_path = logical_path
        self.invocation = invocation
        subject = f": {logical_path}" if logical_path is not None else ""
        super().__init__(f"{code} [{stage}]{subject}: {detail}")


@dataclass(frozen=True, slots=True)
class ValidatedMaterialArtifact:
    """One generated VMT whose bytes and staging identity were verified."""

    generated: GeneratedMaterialContent
    staged_file: StagedFile


@dataclass(frozen=True, slots=True)
class ValidatedModelArtifact:
    """One generated model variant and its complete validated companion set."""

    requirement: GeneratedModelRequirement
    source_has_physics: bool
    qc_artifact: ScaledQCArtifactPlan
    compile_qc: StagedFile
    compile_invocation: ToolInvocation
    validation: CompiledModelValidation
    artifact_fingerprint: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Validated staged outputs; no project, manifest, or VMF was committed."""

    map_identity: str
    staging_root: Path
    materials: tuple[ValidatedMaterialArtifact, ...]
    decompilations: tuple[CrowbarDecompileResult, ...]
    qc_plan: QCOperationPlan
    models: tuple[ValidatedModelArtifact, ...]


def generate_and_validate(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    *,
    crowbar_command: Sequence[str | Path],
    studiomdl_command: Sequence[str | Path],
    crowbar_timeout_seconds: float = 300.0,
    studiomdl_timeout_seconds: float = 300.0,
) -> GenerationResult:
    """Generate every required VMT/MDL and validate it before any commit.

    The caller owns ``workspace``. On failure this function raises one
    categorised exception and leaves all partial output confined to that
    workspace, whose context-manager policy decides whether to preserve it.
    """
    _validate_inputs(workspace, operation, materials, skin_layout)
    material_results = _generate_materials(
        workspace,
        filesystem,
        materials,
        skin_layout,
    )

    assets = {
        item.logical_model_path: item
        for item in operation.source_assets
    }
    required_models = tuple(sorted({
        requirement.logical_source_model
        for requirement in operation.generated_models
    }))
    decompiled_by_model: dict[str, CrowbarDecompileResult] = {}
    source_qcs: dict[str, bytes] = {}
    for logical_model in required_models:
        source = assets.get(logical_model)
        if source is None:
            raise GenerationError(
                "generation_source_asset_missing",
                "decompile",
                "generated requirement has no inspected source metadata",
                logical_path=logical_model,
            )
        try:
            staged_source = stage_source_model(workspace, filesystem, source)
            result = run_crowbar_decompile(
                crowbar_command,
                model_path=staged_source.physical_model_path,
                output_directory=workspace.path(_decompile_directory(logical_model)),
                timeout_seconds=crowbar_timeout_seconds,
            )
        except StagingError as exc:
            raise GenerationError(
                exc.code,
                "stage_source",
                exc.detail,
                logical_path=logical_model,
            ) from exc
        except ToolExecutionError as exc:
            raise GenerationError(
                exc.code,
                "decompile",
                exc.detail,
                logical_path=logical_model,
                invocation=exc.invocation,
            ) from exc
        decompiled_by_model[logical_model] = result
        try:
            source_qcs[logical_model] = result.qc_path.read_bytes()
        except OSError as exc:
            raise GenerationError(
                "decompiled_qc_read_failed",
                "decompile",
                f"{type(exc).__name__}: {exc}",
                logical_path=logical_model,
                invocation=result.invocation,
            ) from exc

    qc_plan = build_qc_operation_plan(operation, skin_layout, source_qcs)
    if not qc_plan.is_valid:
        errors = [
            f"{item.code}: {item.detail}"
            for item in qc_plan.diagnostics
            if item.severity == "error"
        ]
        raise GenerationError(
            "qc_operation_plan_invalid",
            "qc_plan",
            "; ".join(errors) or operation.map_identity,
        )
    try:
        stage_qc_operation(workspace, qc_plan)
    except StagingError as exc:
        raise GenerationError(exc.code, "stage_qc", exc.detail) from exc

    requirements = {
        item.logical_output_model: item
        for item in operation.generated_models
    }
    model_results: list[ValidatedModelArtifact] = []
    for variant in qc_plan.variants:
        requirement = requirements.get(variant.logical_output_model)
        source = assets.get(variant.logical_source_model)
        decompile = decompiled_by_model.get(variant.logical_source_model)
        if requirement is None or source is None or decompile is None:
            raise GenerationError(
                "generation_variant_unplanned",
                "compile",
                "QC variant does not match its operation/source/decompile plan",
                logical_path=variant.logical_output_model,
            )
        compile_qc = _stage_compile_qc(workspace, decompile, variant)
        try:
            invocation = run_studiomdl_compile(
                studiomdl_command,
                game_directory=workspace.path("game"),
                qc_path=compile_qc.physical_path,
                timeout_seconds=studiomdl_timeout_seconds,
            )
            validation = validate_compiled_model(
                workspace.path("game"),
                variant.logical_output_model,
                requires_physics=source.has_physics,
            )
        except ToolExecutionError as exc:
            raise GenerationError(
                exc.code,
                "compile",
                exc.detail,
                logical_path=variant.logical_output_model,
                invocation=exc.invocation,
            ) from exc
        except CompiledModelValidationError as exc:
            raise GenerationError(
                exc.code,
                "validate_model",
                exc.detail,
                logical_path=exc.logical_path,
                invocation=invocation,
            ) from exc
        model_results.append(ValidatedModelArtifact(
            requirement=requirement,
            source_has_physics=source.has_physics,
            qc_artifact=variant,
            compile_qc=compile_qc,
            compile_invocation=invocation,
            validation=validation,
            artifact_fingerprint=model_artifact_fingerprint(validation),
        ))

    if len(model_results) != len(operation.generated_models):
        raise GenerationError(
            "generation_model_count_mismatch",
            "validate_model",
            f"validated {len(model_results)} of {len(operation.generated_models)} models",
        )
    return GenerationResult(
        map_identity=operation.map_identity,
        staging_root=workspace.root,
        materials=material_results,
        decompilations=tuple(
            decompiled_by_model[model]
            for model in required_models
        ),
        qc_plan=qc_plan,
        models=tuple(model_results),
    )


def _validate_inputs(
    workspace: StagingWorkspace,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> None:
    if not workspace.is_open:
        raise GenerationError("staging_closed", "preflight", str(workspace.root))
    identities = {operation.map_identity, materials.map_identity, skin_layout.map_identity}
    if len(identities) != 1:
        raise GenerationError(
            "generation_map_identity_mismatch",
            "preflight",
            repr(sorted(identities)),
        )
    for label, valid in (
        ("operation", operation.is_valid),
        ("materials", materials.is_valid),
        ("skin_layout", skin_layout.is_valid),
    ):
        if not valid:
            raise GenerationError(
                "generation_input_plan_invalid",
                "preflight",
                label,
            )


def _generate_materials(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    plan: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> tuple[ValidatedMaterialArtifact, ...]:
    accepted_skin_identities = {
        (
            mapping.logical_source_model,
            mapping.source_skin,
            mapping.render_color,
        )
        for layout in skin_layout.layouts
        for mapping in layout.mappings
    }
    required_outputs = {
        logical_path
        for colored_skin in plan.colored_skins
        if (
            colored_skin.logical_source_model,
            colored_skin.source_skin,
            colored_skin.render_color,
        ) in accepted_skin_identities
        for logical_path in colored_skin.logical_colored_materials
    }
    metadata = {
        item.logical_material_path: item
        for item in plan.source_materials
    }
    generated: list[ValidatedMaterialArtifact] = []
    for item in plan.colored_materials:
        if item.logical_output_material not in required_outputs:
            continue
        planned_source = metadata.get(item.logical_source_material)
        if planned_source is None:
            raise GenerationError(
                "generation_material_source_missing",
                "generate_material",
                "planned material has no inspected source metadata",
                logical_path=item.logical_source_material,
            )
        try:
            source = inspect_source_material(filesystem, item.logical_source_material)
        except SourceMaterialInspectionError as exc:
            raise GenerationError(
                exc.code,
                "generate_material",
                exc.detail,
                logical_path=exc.logical_path,
            ) from exc
        if (
            planned_source.dependency_fingerprint != item.source_fingerprint
            or source.dependency_fingerprint != item.source_fingerprint
            or source != planned_source
        ):
            raise GenerationError(
                "generation_material_source_changed",
                "generate_material",
                "source dependency fingerprint differs from the material plan",
                logical_path=item.logical_source_material,
            )
        try:
            content = generate_colored_material(
                source,
                logical_output_material=item.logical_output_material,
                render_color=item.render_color,
                color_parameter=item.color_parameter,
                color_assignment=item.color_assignment,
                generation_mode=item.generation_mode,
            )
            staged = workspace.write_bytes(
                f"game/{content.logical_output_material}",
                content.content,
            )
        except MaterialGenerationError as exc:
            raise GenerationError(
                exc.code,
                "generate_material",
                exc.detail,
                logical_path=exc.logical_path,
            ) from exc
        except StagingError as exc:
            raise GenerationError(
                exc.code,
                "stage_material",
                exc.detail,
                logical_path=item.logical_output_material,
            ) from exc
        if staged.sha256 != content.sha256:
            raise GenerationError(
                "staged_material_hash_mismatch",
                "validate_material",
                staged.relative_path,
                logical_path=item.logical_output_material,
            )
        generated.append(ValidatedMaterialArtifact(content, staged))
    return tuple(generated)


def _decompile_directory(logical_model: str) -> str:
    relative = PurePosixPath(logical_model.removeprefix("models/")).with_suffix("")
    return str(PurePosixPath("decompiled", relative))


def _stage_compile_qc(
    workspace: StagingWorkspace,
    decompile: CrowbarDecompileResult,
    variant: ScaledQCArtifactPlan,
) -> StagedFile:
    try:
        parent = decompile.qc_path.parent.resolve().relative_to(workspace.root)
    except ValueError as exc:
        raise GenerationError(
            "decompiled_qc_outside_staging",
            "stage_qc",
            str(decompile.qc_path),
            logical_path=variant.logical_output_model,
        ) from exc
    filename = f"_psr_variant_{variant.output_qc_sha256[:20]}.qc"
    relative_path = PurePosixPath(*parent.parts, filename).as_posix()
    try:
        return workspace.write_bytes(relative_path, variant.content)
    except StagingError as exc:
        raise GenerationError(
            exc.code,
            "stage_qc",
            exc.detail,
            logical_path=variant.logical_output_model,
        ) from exc


def model_artifact_fingerprint(validation: CompiledModelValidation) -> str:
    digest = hashlib.sha256()
    for item in sorted(validation.files, key=lambda value: value.logical_path):
        digest.update(item.logical_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(item.sha256))
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "GenerationError",
    "GenerationResult",
    "ValidatedMaterialArtifact",
    "ValidatedModelArtifact",
    "generate_and_validate",
    "model_artifact_fingerprint",
]
