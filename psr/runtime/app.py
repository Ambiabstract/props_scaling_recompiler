"""Production orchestration for one Hammer compile-run invocation."""

from __future__ import annotations

import os
import shutil
import hashlib
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from psr.assets import (
    OrderedAssetFileSystem,
    SearchPathParseError,
    parse_gameinfo_search_paths,
    plan_search_paths,
)
from psr.cache import ProjectManifest, build_project_identity, load_manifest
from psr.keyvalues import parse_vmf
from psr.pipeline import (
    CommitError,
    ColoredMaterialOperationPlan,
    MaterialInspection,
    OperationPlan,
    OutcomeLedger,
    SkinLayoutOperationPlan,
    StagingError,
    StagingWorkspace,
    VmfFallbackAssignment,
    VmfEntityRequest,
    WorkFailure,
    apply_commit_plan,
    build_colored_material_plan,
    build_commit_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    filter_operation_plan,
    filter_skin_layout_plan,
    generate_materials_and_validate,
    generate_models_and_validate,
    inspect_colored_material_sources,
    inspect_map_sources,
    plan_artifact_reuse,
    reconcile_generation_requirements,
    recover_interrupted_commit,
)

from .reporting import DiagnosticReport
from .progress import NullProgressReporter, ProgressReporter
from .staging_gameinfo import build_staging_gameinfo
from .state import ProjectLock, ProjectStatePaths, build_project_state_paths


class RuntimeExecutionError(RuntimeError):
    """A categorised failure before or around the pure/staged pipeline."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CompileRequest:
    game_directory: Path
    vmf_input_path: Path
    vmf_output_path: Path
    engine_root: Path | None
    crowbar_command: tuple[str | Path, ...] | None
    studiomdl_command: tuple[str | Path, ...] | None
    local_appdata: Path | None = None
    dynamic_fallback: bool = True


@dataclass(frozen=True, slots=True)
class CompileRunResult:
    success: bool
    map_identity: str
    state: ProjectStatePaths
    active_entities: int
    generated_models: int
    reused_models: int
    generated_materials: int
    reused_materials: int
    published_files: int
    retained_staging: Path | None = None


def execute_compile_run(
    request: CompileRequest,
    report: DiagnosticReport,
    progress: ProgressReporter | NullProgressReporter | None = None,
) -> CompileRunResult:
    """Execute discover -> plan -> generate -> validate -> commit once."""
    progress = progress or NullProgressReporter()
    progress.start("Validating project and VMF inputs")
    game = request.game_directory.resolve(strict=True)
    if not game.is_dir():
        raise RuntimeExecutionError("game_not_directory", str(game))
    gameinfo = _find_gameinfo(game)
    vmf_input = request.vmf_input_path.resolve(strict=True)
    if not vmf_input.is_file():
        raise RuntimeExecutionError("vmf_input_not_file", str(vmf_input))
    vmf_output = request.vmf_output_path.resolve()
    source_vmf = _read_bytes(vmf_input, "vmf_input_read_failed")
    map_identity = _map_identity(vmf_input, game)
    project = build_project_identity(gameinfo)
    state = build_project_state_paths(project, local_appdata=request.local_appdata)
    state.ensure_directories()

    with ProjectLock(state.lock, map_identity=map_identity):
        recovery = recover_interrupted_commit(
            state.recovery_journal,
            game_directory=game,
            manifest_path=state.manifest,
            vmf_output_path=vmf_output,
        )
        if recovery.recovered:
            report.add(
                "warning",
                "interrupted_commit_recovered",
                f"restored {len(recovery.restored_targets)} targets from the previous run",
            )

        loaded = load_manifest(state.manifest, project)
        if loaded.status not in {"missing", "loaded"}:
            preserved = _preserve_rejected_manifest(state, loaded.status)
            detail = f"cache status {loaded.status}; continuing from an empty manifest"
            if preserved is not None:
                detail += f"; previous file preserved at {preserved}"
            report.add("warning", "manifest_cold_recovery", detail)
        elif loaded.status == "missing":
            report.add(
                "recommendation",
                "manifest_created_on_commit",
                f"new project state will be created at {state.manifest}",
            )

        try:
            specs = parse_gameinfo_search_paths(gameinfo)
            search_plan = plan_search_paths(
                specs,
                gameinfo_dir=game,
                engine_root=request.engine_root,
            )
        except (OSError, UnicodeError, SearchPathParseError) as exc:
            raise RuntimeExecutionError(
                "gameinfo_searchpaths_invalid",
                f"{type(exc).__name__}: {exc}",
            ) from exc
        _report_search_path_diagnostics(search_plan.diagnostics, report)
        filesystem = OrderedAssetFileSystem(search_plan.mounts)

        progress.start("Discovering entities and planning source assets")
        discovery = discover_vmf_requests(source_vmf, map_identity=map_identity)
        inspected = inspect_map_sources(discovery, filesystem)
        base_operation = build_operation_plan(inspected)
        material_inspection = inspect_colored_material_sources(
            base_operation, filesystem
        )
        initial_materials = build_colored_material_plan(
            base_operation, material_inspection
        )
        initial_layout = build_skin_layout_plan(
            base_operation, initial_materials, loaded.manifest
        )
        report.extend_pipeline(
            item for item in initial_layout.diagnostics
            if item.code != "dynamic_bodygroup_fallback"
        )
        for usage in base_operation.usages:
            if usage.operation == "reuse_dynamic":
                report.add(
                    "error",
                    "dynamic_bodygroup_static_result_unavailable",
                    (
                        f"{usage.request.logical_model_path} cannot be made static "
                        "safely because it contains an empty bodygroup option; the "
                        "confirmed prop_dynamic fallback is used"
                    ),
                    entity_id=usage.request.entity_id,
                    source_line=usage.request.source_line,
                )

        failures = _planning_failures(
            discovery.requests,
            base_operation,
            initial_materials,
            initial_layout,
        )
        excluded = set(OutcomeLedger(tuple(failures)).affected_entity_ids(
            base_operation, initial_materials
        ))
        operation, materials, skin_layout = _surviving_plans(
            base_operation,
            material_inspection,
            loaded.manifest,
            excluded,
        )

        workspace = StagingWorkspace.create(
            state.staging,
            operation_identity=map_identity,
            preserve=True,
        )
        try:
            workspace.write_bytes(
                "game/GameInfo.txt",
                build_staging_gameinfo(search_plan.mounts),
            )
            generated_materials = {}
            while True:
                operation = reconcile_generation_requirements(
                    operation, skin_layout, loaded.manifest
                )
                reuse = plan_artifact_reuse(
                    game, loaded.manifest, operation, materials, skin_layout
                )
                report.extend_pipeline(reuse.diagnostics)
                progress.start(
                    f"Generating and validating materials "
                    f"({len(reuse.generation_materials.colored_materials)} pending)"
                )
                material_result = generate_materials_and_validate(
                    workspace,
                    filesystem,
                    reuse.generation_operation,
                    reuse.generation_materials,
                    skin_layout,
                )
                generated_materials.update({
                    item.generated.logical_output_material: item
                    for item in material_result.materials
                })
                if not material_result.failures:
                    break
                new_failures = tuple(
                    WorkFailure(
                        "material",
                        item.code,
                        item.detail,
                        logical_material=item.logical_path,
                    )
                    for item in material_result.failures
                )
                failures.extend(new_failures)
                newly_excluded = OutcomeLedger(new_failures).affected_entity_ids(
                    operation, materials
                )
                if not newly_excluded - excluded:
                    break
                excluded.update(newly_excluded)
                operation, materials, skin_layout = _surviving_plans(
                    base_operation,
                    material_inspection,
                    loaded.manifest,
                    excluded,
                )

            operation = reconcile_generation_requirements(
                operation, skin_layout, loaded.manifest
            )
            reuse = plan_artifact_reuse(
                game, loaded.manifest, operation, materials, skin_layout
            )
            report.extend_pipeline(reuse.diagnostics)

            tool_failures: list[WorkFailure] = []
            if reuse.generation_operation.generated_models:
                missing_code = None
                missing_detail = None
                if not request.crowbar_command:
                    missing_code = "crowbar_not_found"
                    missing_detail = (
                        "CrowbarCommandLineDecomp.exe was not found beside PSR or "
                        "in third-party; place the PSR EXE in the Source SDK 2013 SP "
                        "bin directory and install CrowbarCommandLineDecomp.exe under "
                        "bin/third-party"
                    )
                elif not request.studiomdl_command:
                    missing_code = "studiomdl_not_found"
                    missing_detail = (
                        "studiomdl.exe was not found beside props_scaling_recompiler.exe; "
                        "place the PSR EXE in the Source SDK 2013 SP bin directory"
                    )
                if missing_code is not None:
                    tool_failures.extend(
                        WorkFailure(
                            "model_variant" if item.entity_ids else "source_model",
                            missing_code,
                            missing_detail or missing_code,
                            logical_source_model=item.logical_source_model,
                            logical_output_model=item.logical_output_model,
                        )
                        for item in reuse.generation_operation.generated_models
                    )
            if tool_failures:
                failures.extend(tool_failures)
                excluded.update(OutcomeLedger(tuple(tool_failures)).affected_entity_ids(
                    operation, materials
                ))
                operation, materials, skin_layout = _surviving_plans(
                    base_operation,
                    material_inspection,
                    loaded.manifest,
                    excluded,
                )
                operation = reconcile_generation_requirements(
                    operation, skin_layout, loaded.manifest
                )
                reuse = plan_artifact_reuse(
                    game, loaded.manifest, operation, materials, skin_layout
                )

            progress.start(
                f"Decompiling and compiling models "
                f"({len(reuse.generation_operation.generated_models)} pending)"
            )
            model_result = generate_models_and_validate(
                workspace,
                filesystem,
                reuse.generation_operation,
                skin_layout,
                crowbar_command=request.crowbar_command or ("unused-crowbar",),
                studiomdl_command=request.studiomdl_command or ("unused-studiomdl",),
            )
            generation_failures: list[WorkFailure] = []
            requirements = {
                item.logical_output_model: item
                for item in reuse.generation_operation.generated_models
            }
            for item in model_result.failures:
                scope = item.scope
                requirement = requirements.get(item.logical_output_model or "")
                if scope == "model_variant" and requirement is not None and not requirement.entity_ids:
                    scope = "source_model"
                generation_failures.append(WorkFailure(
                    scope,
                    item.code,
                    item.detail,
                    logical_source_model=item.logical_source_model,
                    logical_output_model=item.logical_output_model,
                ))
            if generation_failures:
                failures.extend(generation_failures)
                excluded.update(OutcomeLedger(tuple(generation_failures)).affected_entity_ids(
                    operation, materials
                ))
                operation, materials, skin_layout = _surviving_plans(
                    base_operation,
                    material_inspection,
                    loaded.manifest,
                    excluded,
                )
                operation = reconcile_generation_requirements(
                    operation, skin_layout, loaded.manifest
                )
                reuse = plan_artifact_reuse(
                    game, loaded.manifest, operation, materials, skin_layout
                )
                skin_layout = filter_skin_layout_plan(skin_layout, operation)

            expected_models = {
                item.logical_output_model
                for item in reuse.generation_operation.generated_models
            }
            accepted_materials = _accepted_material_outputs(materials, skin_layout)
            reused_material_paths = {
                item.record.logical_output_material for item in reuse.reused_materials
            }
            expected_materials = accepted_materials - reused_material_paths
            generation = replace(
                model_result,
                materials=tuple(
                    generated_materials[path]
                    for path in sorted(expected_materials)
                    if path in generated_materials
                ),
                models=tuple(
                    item for item in model_result.models
                    if item.requirement.logical_output_model in expected_models
                ),
            )
            _report_work_failures(failures, base_operation, initial_materials, report)
            fallback_ids = excluded if request.dynamic_fallback else set()
            progress.start("Validating and publishing assets, manifest, and VMF")
            commit_plan = build_commit_plan(
                source_vmf,
                loaded.manifest,
                operation,
                materials,
                skin_layout,
                generation,
                reuse,
                fallbacks=tuple(
                    VmfFallbackAssignment(entity_id)
                    for entity_id in sorted(fallback_ids, key=int)
                ),
            )
            committed = apply_commit_plan(
                commit_plan,
                game_directory=game,
                manifest_path=state.manifest,
                vmf_output_path=vmf_output,
                recovery_journal_path=state.recovery_journal,
            )
        except (CommitError, StagingError) as exc:
            progress.finish()
            detail = getattr(exc, "detail", str(exc))
            logical_path = getattr(exc, "logical_path", None)
            if logical_path is not None:
                detail = f"{logical_path}: {detail}"
            report.add(
                "error",
                getattr(exc, "code", "pipeline_failed"),
                detail,
            )
            invocation = getattr(exc, "invocation", None)
            if invocation is not None:
                captured = _captured_tool_output(invocation.stdout, invocation.stderr)
                if captured:
                    report.add(
                        "recommendation",
                        "external_tool_output",
                        captured,
                    )
            report.add(
                "recommendation",
                "staging_retained",
                f"failed-run staging retained at {workspace.root}",
            )
            delivered = False
            try:
                _publish_passthrough_vmf(source_vmf, vmf_output)
            except (OSError, ValueError) as passthrough_exc:
                report.add(
                    "error",
                    "vmf_passthrough_failed",
                    f"{type(passthrough_exc).__name__}: {passthrough_exc}",
                )
            else:
                delivered = True
                report.add(
                    "info",
                    "vmf_passthrough_written",
                    "the validated original VMF was delivered unchanged",
                )
            return CompileRunResult(
                success=delivered,
                map_identity=map_identity,
                state=state,
                active_entities=len(discovery.requests),
                generated_models=0,
                reused_models=len(reuse.reused_models),
                generated_materials=0,
                reused_materials=len(reuse.reused_materials),
                published_files=0,
                retained_staging=workspace.root,
            )
        else:
            progress.finish()
            try:
                workspace.cleanup()
            except StagingError as exc:
                report.add("warning", exc.code, exc.detail)
            return CompileRunResult(
                success=True,
                map_identity=map_identity,
                state=state,
                active_entities=len(discovery.requests),
                generated_models=len(generation.models),
                reused_models=len(reuse.reused_models),
                generated_materials=len(generation.materials),
                reused_materials=len(reuse.reused_materials),
                published_files=len(committed.published_artifacts),
            )


def _planning_failures(
    requests: Sequence[VmfEntityRequest],
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> list[WorkFailure]:
    failures: list[WorkFailure] = []
    usage_ids = {item.request.entity_id for item in operation.usages}
    asset_models = {item.logical_model_path for item in operation.source_assets}
    for request in requests:
        if request.entity_id in usage_ids:
            continue
        if request.logical_model_path not in asset_models:
            failures.append(WorkFailure(
                "source_model",
                "source_model_unavailable",
                f"{request.logical_model_path}: source model could not be inspected",
                entity_id=request.entity_id,
                logical_source_model=request.logical_model_path,
            ))
        else:
            failures.append(WorkFailure(
                "entity",
                "request_invalid",
                "entity request could not be normalised into a safe static result",
                entity_id=request.entity_id,
            ))

    planned_colored = {
        (item.logical_source_model, item.source_skin, item.render_color)
        for item in materials.colored_skins
    }
    for item in operation.colored_skins:
        identity = (
            item.logical_source_model,
            item.source_skin,
            item.render_color,
        )
        if identity not in planned_colored:
            failures.append(WorkFailure(
                "colored_skin",
                "colored_skin_unavailable",
                (
                    f"{item.logical_source_model}: colored skin {item.source_skin}, "
                    f"RGB {item.render_color} could not be planned"
                ),
                logical_source_model=item.logical_source_model,
                source_skin=item.source_skin,
                render_color=item.render_color,
            ))

    assigned = {item.entity_id for item in skin_layout.assignments}
    for usage in operation.usages:
        if usage.request.entity_id not in assigned:
            failures.append(WorkFailure(
                "entity",
                "skin_assignment_unavailable",
                "no complete skin-layout assignment could be produced",
                entity_id=usage.request.entity_id,
            ))

    models = {item.request.logical_model_path for item in operation.usages}
    for diagnostic in skin_layout.diagnostics:
        if diagnostic.severity != "error" or diagnostic.entity_id is not None:
            continue
        matched = [model for model in models if model in diagnostic.detail]
        for model in matched:
            failures.append(WorkFailure(
                "source_model",
                diagnostic.code,
                diagnostic.detail,
                logical_source_model=model,
            ))
    return failures


def _surviving_plans(
    base_operation: OperationPlan,
    material_inspection: MaterialInspection,
    manifest: ProjectManifest,
    excluded: set[str],
) -> tuple[OperationPlan, ColoredMaterialOperationPlan, SkinLayoutOperationPlan]:
    """Rebuild dependent plans until every surviving usage has an assignment."""
    clean_inspection = MaterialInspection(material_inspection.source_materials, ())
    for _attempt in range(len(base_operation.usages) + 1):
        operation = filter_operation_plan(base_operation, excluded)
        materials = build_colored_material_plan(operation, clean_inspection)
        skin_layout = build_skin_layout_plan(operation, materials, manifest)
        usage_ids = {item.request.entity_id for item in operation.usages}
        assignment_ids = {item.entity_id for item in skin_layout.assignments}
        missing = usage_ids - assignment_ids
        if not missing and skin_layout.is_valid and materials.is_valid:
            return operation, materials, skin_layout
        before = len(excluded)
        excluded.update(missing)
        for diagnostic in (*materials.diagnostics, *skin_layout.diagnostics):
            if diagnostic.severity == "error" and diagnostic.entity_id is not None:
                excluded.add(diagnostic.entity_id)
        if len(excluded) == before:
            # An unscoped layout failure makes every usage of the mentioned
            # original model unsafe. If no model can be identified, preserve
            # the complete remaining request set instead of guessing.
            models = {item.request.logical_model_path for item in operation.usages}
            matched = {
                model
                for diagnostic in (*materials.diagnostics, *skin_layout.diagnostics)
                if diagnostic.severity == "error"
                for model in models
                if model in diagnostic.detail
            }
            if matched:
                excluded.update(
                    item.request.entity_id
                    for item in operation.usages
                    if item.request.logical_model_path in matched
                )
            else:
                excluded.update(usage_ids)
    operation = filter_operation_plan(base_operation, excluded)
    materials = build_colored_material_plan(operation, clean_inspection)
    return operation, materials, build_skin_layout_plan(operation, materials, manifest)


def _accepted_material_outputs(
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> set[str]:
    accepted = {
        (item.logical_source_model, item.source_skin, item.render_color)
        for layout in skin_layout.layouts
        for item in layout.mappings
    }
    return {
        logical_path
        for item in materials.colored_skins
        if (item.logical_source_model, item.source_skin, item.render_color) in accepted
        for logical_path in item.logical_colored_materials
    }


def _report_work_failures(
    failures: Sequence[WorkFailure],
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    report: DiagnosticReport,
) -> None:
    for failure in failures:
        affected = OutcomeLedger((failure,)).affected_entity_ids(operation, materials)
        if affected:
            for entity_id in sorted(affected, key=int):
                report.add(
                    "error",
                    failure.code,
                    failure.detail,
                    entity_id=entity_id,
                )
        else:
            report.add("error", failure.code, failure.detail)


def _publish_passthrough_vmf(source: bytes, destination: Path) -> None:
    """Atomically deliver a structurally valid byte-equivalent fallback VMF."""
    parse_vmf(source)
    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".psr-passthrough",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        if (
            temporary.stat().st_size != len(source)
            or hashlib.sha256(temporary.read_bytes()).digest()
            != hashlib.sha256(source).digest()
        ):
            raise OSError("passthrough temporary file failed content validation")
        os.replace(temporary, target)
        temporary = None
        written = target.read_bytes()
        parse_vmf(written)
        if written != source:
            raise OSError("installed passthrough VMF differs from the input bytes")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def deliver_passthrough_vmf(source_path: Path, destination: Path) -> None:
    """Public emergency boundary for failures before project orchestration."""
    source = source_path.resolve(strict=True).read_bytes()
    _publish_passthrough_vmf(source, destination)


def _find_gameinfo(game: Path) -> Path:
    exact = game / "GameInfo.txt"
    if exact.is_file():
        return exact
    matches = [
        item
        for item in game.iterdir()
        if item.is_file() and item.name.casefold() == "gameinfo.txt"
    ]
    if len(matches) == 1:
        return matches[0]
    raise RuntimeExecutionError(
        "gameinfo_not_found",
        f"expected one GameInfo.txt in {game}",
    )


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeExecutionError(
            code,
            f"{type(exc).__name__}: {exc}",
        ) from exc


def _map_identity(vmf_path: Path, game: Path) -> str:
    try:
        relative = vmf_path.relative_to(game)
    except ValueError:
        return vmf_path.as_posix().casefold()
    return relative.as_posix().casefold()


def _report_search_path_diagnostics(
    diagnostics: Sequence[object],
    report: DiagnosticReport,
) -> None:
    grouped: dict[str, list[object]] = {}
    for item in diagnostics:
        grouped.setdefault(item.reason, []).append(item)
    for reason, items in sorted(grouped.items()):
        examples = ", ".join(repr(item.raw_value) for item in items[:3])
        if len(items) > 3:
            examples += f", and {len(items) - 3} more"
        report.add(
            "recommendation",
            f"searchpath_{reason}",
            f"{len(items)} SearchPath entries were not mounted: {examples}",
        )


def _preserve_rejected_manifest(
    state: ProjectStatePaths,
    status: str,
) -> Path | None:
    if not state.manifest.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = state.root / f"manifest.{status}.{stamp}.json"
    try:
        shutil.copy2(state.manifest, destination)
    except OSError:
        return None
    return destination


def _captured_tool_output(stdout: bytes, stderr: bytes) -> str:
    combined = b"\n".join(item for item in (stdout, stderr) if item).strip()
    if not combined:
        return ""
    limit = 16 * 1024
    suffix = b"\n...[tool output truncated]" if len(combined) > limit else b""
    return combined[:limit].decode("utf-8", errors="replace") + suffix.decode("ascii")


__all__ = [
    "CompileRequest",
    "CompileRunResult",
    "RuntimeExecutionError",
    "deliver_passthrough_vmf",
    "execute_compile_run",
]
