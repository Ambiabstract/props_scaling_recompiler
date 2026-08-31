"""Execute an already-planned operation entirely inside isolated staging."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

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

from .materials import ColoredMaterialOperationPlan, ColoredMaterialPlan
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


ProgressCallback = Callable[[int, int, str], None]


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
class GenerationFailure:
    """One failed material, source-model, or scale-variation work unit."""

    code: str
    stage: str
    detail: str
    scope: str
    logical_path: str | None = None
    logical_source_model: str | None = None
    logical_output_model: str | None = None
    invocation: ToolInvocation | None = None


@dataclass(frozen=True, slots=True)
class MaterialGenerationResult:
    """Independent validated VMT outputs plus per-output failures."""

    map_identity: str
    staging_root: Path
    materials: tuple[ValidatedMaterialArtifact, ...]
    failures: tuple[GenerationFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Validated staged outputs; no project, manifest, or VMF was committed."""

    map_identity: str
    staging_root: Path
    materials: tuple[ValidatedMaterialArtifact, ...]
    decompilations: tuple[CrowbarDecompileResult, ...]
    qc_plan: QCOperationPlan
    models: tuple[ValidatedModelArtifact, ...]
    failures: tuple[GenerationFailure, ...] = ()


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
    material_result = generate_materials_and_validate(
        workspace,
        filesystem,
        operation,
        materials,
        skin_layout,
    )
    if material_result.failures:
        raise _failure_as_error(material_result.failures[0])
    model_result = generate_models_and_validate(
        workspace,
        filesystem,
        operation,
        skin_layout,
        crowbar_command=crowbar_command,
        studiomdl_command=studiomdl_command,
        crowbar_timeout_seconds=crowbar_timeout_seconds,
        studiomdl_timeout_seconds=studiomdl_timeout_seconds,
    )
    if model_result.failures:
        raise _failure_as_error(model_result.failures[0])
    return replace(model_result, materials=material_result.materials)


def generate_models_and_validate(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    operation: OperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    *,
    crowbar_command: Sequence[str | Path],
    studiomdl_command: Sequence[str | Path],
    crowbar_timeout_seconds: float = 300.0,
    studiomdl_timeout_seconds: float = 300.0,
    progress_callback: ProgressCallback | None = None,
) -> GenerationResult:
    """Continue independent source models and scale variants after failures."""
    empty_materials = ColoredMaterialOperationPlan(
        operation.map_identity, (), (), (), ()
    )
    _validate_inputs(workspace, operation, empty_materials, skin_layout)

    assets = {
        item.logical_model_path: item
        for item in operation.source_assets
    }
    required_models = tuple(sorted({
        requirement.logical_source_model
        for requirement in operation.generated_models
    }))
    decompiled_by_model: dict[str, CrowbarDecompileResult] = {}
    failures: list[GenerationFailure] = []
    references = []
    variants = []
    qc_diagnostics = []
    model_results: list[ValidatedModelArtifact] = []
    total_work = len(required_models) + len(operation.generated_models)
    completed_work = 0

    def advance(count: int, detail: str) -> None:
        nonlocal completed_work
        completed_work += count
        if progress_callback is not None:
            progress_callback(completed_work, total_work, detail)

    for logical_model in required_models:
        source_variants = tuple(
            item for item in operation.generated_models
            if item.logical_source_model == logical_model
        )
        if progress_callback is not None:
            progress_callback(
                completed_work,
                total_work,
                f"decompiling {logical_model}",
            )
        source = assets.get(logical_model)
        if source is None:
            failures.append(GenerationFailure(
                "generation_source_asset_missing", "decompile",
                "generated requirement has no inspected source metadata",
                "source_model", logical_model, logical_model,
            ))
            advance(1 + len(source_variants), f"skipped {logical_model}")
            continue
        try:
            staged_source = stage_source_model(workspace, filesystem, source)
            result = run_crowbar_decompile(
                crowbar_command,
                model_path=staged_source.physical_model_path,
                output_directory=workspace.path(_decompile_directory(logical_model)),
                timeout_seconds=crowbar_timeout_seconds,
            )
        except StagingError as exc:
            failures.append(GenerationFailure(
                exc.code, "stage_source", exc.detail, "source_model",
                logical_model, logical_model,
            ))
            advance(1 + len(source_variants), f"failed {logical_model}")
            continue
        except ToolExecutionError as exc:
            failures.append(GenerationFailure(
                exc.code, "decompile", exc.detail, "source_model",
                logical_model, logical_model, invocation=exc.invocation,
            ))
            advance(1 + len(source_variants), f"failed {logical_model}")
            continue
        decompiled_by_model[logical_model] = result
        advance(1, f"decompiled {logical_model}")
        try:
            source_qc = result.qc_path.read_bytes()
        except OSError as exc:
            failures.append(GenerationFailure(
                "decompiled_qc_read_failed", "decompile",
                f"{type(exc).__name__}: {exc}", "source_model",
                logical_model, logical_model, invocation=result.invocation,
            ))
            advance(len(source_variants), f"skipped variants for {logical_model}")
            continue

        source_operation = replace(
            operation,
            source_assets=(source,),
            usages=tuple(
                item for item in operation.usages
                if item.request.logical_model_path == logical_model
            ),
            generated_models=tuple(
                item for item in operation.generated_models
                if item.logical_source_model == logical_model
            ),
            colored_skins=tuple(
                item for item in operation.colored_skins
                if item.logical_source_model == logical_model
            ),
            diagnostics=(),
        )
        source_layout = replace(
            skin_layout,
            layouts=tuple(
                item for item in skin_layout.layouts
                if item.logical_source_model == logical_model
            ),
            assignments=tuple(
                item for item in skin_layout.assignments
                if item.logical_source_model == logical_model
            ),
            diagnostics=(),
        )
        source_qc_plan = build_qc_operation_plan(
            source_operation, source_layout, {logical_model: source_qc}
        )
        if not source_qc_plan.is_valid:
            detail = "; ".join(
                f"{item.code}: {item.detail}"
                for item in source_qc_plan.diagnostics
                if item.severity == "error"
            )
            failures.append(GenerationFailure(
                "qc_operation_plan_invalid", "qc_plan",
                detail or logical_model, "source_model",
                logical_model, logical_model,
            ))
            advance(len(source_variants), f"skipped variants for {logical_model}")
            continue
        try:
            stage_qc_operation(workspace, source_qc_plan)
        except StagingError as exc:
            failures.append(GenerationFailure(
                exc.code, "stage_qc", exc.detail, "source_model",
                logical_model, logical_model,
            ))
            advance(len(source_variants), f"skipped variants for {logical_model}")
            continue
        references.extend(source_qc_plan.references)
        variants.extend(source_qc_plan.variants)
        qc_diagnostics.extend(source_qc_plan.diagnostics)
        requirements = {
            item.logical_output_model: item
            for item in source_operation.generated_models
        }
        for variant in source_qc_plan.variants:
            requirement = requirements[variant.logical_output_model]
            if progress_callback is not None:
                progress_callback(
                    completed_work,
                    total_work,
                    f"compiling {variant.logical_output_model}",
                )
            try:
                compile_qc = _stage_compile_qc(workspace, result, variant)
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
                    requires_static_conversion=requirement.requires_static_conversion,
                )
            except GenerationError as exc:
                failures.append(_error_as_failure(
                    exc, "model_variant", logical_model,
                    variant.logical_output_model,
                ))
                advance(1, f"failed {variant.logical_output_model}")
                continue
            except ToolExecutionError as exc:
                failures.append(GenerationFailure(
                    exc.code, "compile", exc.detail, "model_variant",
                    variant.logical_output_model, logical_model,
                    variant.logical_output_model, exc.invocation,
                ))
                advance(1, f"failed {variant.logical_output_model}")
                continue
            except CompiledModelValidationError as exc:
                failures.append(GenerationFailure(
                    exc.code, "validate_model", exc.detail, "model_variant",
                    exc.logical_path, logical_model,
                    variant.logical_output_model, invocation,
                ))
                advance(1, f"failed {variant.logical_output_model}")
                continue
            model_results.append(ValidatedModelArtifact(
                requirement=requirement,
                source_has_physics=source.has_physics,
                qc_artifact=variant,
                compile_qc=compile_qc,
                compile_invocation=invocation,
                validation=validation,
                artifact_fingerprint=model_artifact_fingerprint(validation),
            ))
            advance(1, f"compiled {variant.logical_output_model}")

    qc_plan = QCOperationPlan(
        operation.map_identity,
        tuple(references),
        tuple(variants),
        tuple(qc_diagnostics),
    )
    return GenerationResult(
        map_identity=operation.map_identity,
        staging_root=workspace.root,
        materials=(),
        decompilations=tuple(
            decompiled_by_model[model]
            for model in required_models if model in decompiled_by_model
        ),
        qc_plan=qc_plan,
        models=tuple(model_results),
        failures=tuple(failures),
    )


def generate_materials_and_validate(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    *,
    progress_callback: ProgressCallback | None = None,
) -> MaterialGenerationResult:
    """Generate independent material outputs without aborting sibling work."""
    _validate_inputs(workspace, operation, materials, skin_layout)
    generated: list[ValidatedMaterialArtifact] = []
    failures: list[GenerationFailure] = []
    required = _required_material_plans(materials, skin_layout)
    total = len(required)
    for index, item in enumerate(required):
        if progress_callback is not None:
            progress_callback(index, total, f"generating {item.logical_output_material}")
        try:
            generated.append(_generate_one_material(
                workspace, filesystem, materials, item
            ))
        except GenerationError as exc:
            failures.append(_error_as_failure(exc, "material"))
        if progress_callback is not None:
            progress_callback(index + 1, total, f"processed {item.logical_output_material}")
    return MaterialGenerationResult(
        operation.map_identity, workspace.root, tuple(generated), tuple(failures)
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
    return tuple(
        _generate_one_material(workspace, filesystem, plan, item)
        for item in _required_material_plans(plan, skin_layout)
    )


def _required_material_plans(
    plan: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> tuple[ColoredMaterialPlan, ...]:
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
    return tuple(
        item for item in plan.colored_materials
        if item.logical_output_material in required_outputs
    )


def _generate_one_material(
    workspace: StagingWorkspace,
    filesystem: OrderedAssetFileSystem,
    plan: ColoredMaterialOperationPlan,
    item: ColoredMaterialPlan,
) -> ValidatedMaterialArtifact:
    metadata = {
        value.logical_material_path: value
        for value in plan.source_materials
    }
    planned_source = metadata.get(item.logical_source_material)
    if planned_source is None:
        raise GenerationError(
            "generation_material_source_missing",
            "generate_material",
            "planned material has no inspected source metadata",
            logical_path=item.logical_output_material,
        )
    try:
        source = inspect_source_material(filesystem, item.logical_source_material)
    except SourceMaterialInspectionError as exc:
        raise GenerationError(
            exc.code,
            "generate_material",
            exc.detail,
            logical_path=item.logical_output_material,
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
            logical_path=item.logical_output_material,
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
            logical_path=item.logical_output_material,
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
    return ValidatedMaterialArtifact(content, staged)


def _error_as_failure(
    exc: GenerationError,
    scope: str,
    logical_source_model: str | None = None,
    logical_output_model: str | None = None,
) -> GenerationFailure:
    return GenerationFailure(
        exc.code,
        exc.stage,
        exc.detail,
        scope,
        exc.logical_path,
        logical_source_model,
        logical_output_model,
        exc.invocation,
    )


def _failure_as_error(failure: GenerationFailure) -> GenerationError:
    return GenerationError(
        failure.code,
        failure.stage,
        failure.detail,
        logical_path=failure.logical_path,
        invocation=failure.invocation,
    )


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
    "GenerationFailure",
    "GenerationResult",
    "MaterialGenerationResult",
    "ValidatedMaterialArtifact",
    "ValidatedModelArtifact",
    "generate_and_validate",
    "generate_materials_and_validate",
    "generate_models_and_validate",
    "model_artifact_fingerprint",
]
