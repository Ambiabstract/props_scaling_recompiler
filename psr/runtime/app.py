"""Production orchestration for one Hammer compile-run invocation."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from psr.assets import (
    OrderedAssetFileSystem,
    SearchPathParseError,
    parse_gameinfo_search_paths,
    plan_search_paths,
)
from psr.cache import build_project_identity, load_manifest
from psr.pipeline import (
    CommitError,
    GenerationError,
    StagingError,
    StagingWorkspace,
    apply_commit_plan,
    build_colored_material_plan,
    build_commit_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    generate_and_validate,
    inspect_colored_material_sources,
    inspect_map_sources,
    reconcile_generation_requirements,
    recover_interrupted_commit,
)

from .reporting import DiagnosticReport
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


@dataclass(frozen=True, slots=True)
class CompileRunResult:
    success: bool
    map_identity: str
    state: ProjectStatePaths
    active_entities: int
    generated_models: int
    generated_materials: int
    published_files: int
    retained_staging: Path | None = None


def execute_compile_run(
    request: CompileRequest,
    report: DiagnosticReport,
) -> CompileRunResult:
    """Execute discover -> plan -> generate -> validate -> commit once."""
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

        discovery = discover_vmf_requests(source_vmf, map_identity=map_identity)
        inspected = inspect_map_sources(discovery, filesystem)
        operation = build_operation_plan(inspected)
        material_inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, material_inspection)
        skin_layout = build_skin_layout_plan(operation, materials, loaded.manifest)
        operation = reconcile_generation_requirements(
            operation,
            skin_layout,
            loaded.manifest,
        )
        report.extend_pipeline(operation.diagnostics)
        report.extend_pipeline(materials.diagnostics)
        report.extend_pipeline(skin_layout.diagnostics)
        if not operation.is_valid or not materials.is_valid or not skin_layout.is_valid:
            return CompileRunResult(
                False,
                map_identity,
                state,
                len(discovery.requests),
                0,
                0,
                0,
            )

        if operation.generated_models:
            if not request.crowbar_command:
                raise RuntimeExecutionError(
                    "crowbar_not_found",
                    "CrowbarCommandLineDecomp.exe was not found beside PSR or in third-party",
                )
            if not request.studiomdl_command:
                raise RuntimeExecutionError(
                    "studiomdl_not_found",
                    "studiomdl.exe was not found beside props_scaling_recompiler.exe",
                )
        crowbar = request.crowbar_command or ("unused-crowbar",)
        studiomdl = request.studiomdl_command or ("unused-studiomdl",)

        workspace = StagingWorkspace.create(
            state.staging,
            operation_identity=map_identity,
            preserve=True,
        )
        try:
            generation = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=crowbar,
                studiomdl_command=studiomdl,
            )
            commit_plan = build_commit_plan(
                source_vmf,
                loaded.manifest,
                operation,
                materials,
                skin_layout,
                generation,
            )
            committed = apply_commit_plan(
                commit_plan,
                game_directory=game,
                manifest_path=state.manifest,
                vmf_output_path=vmf_output,
                recovery_journal_path=state.recovery_journal,
            )
        except (GenerationError, CommitError, StagingError) as exc:
            report.add(
                "error",
                getattr(exc, "code", "pipeline_failed"),
                getattr(exc, "detail", str(exc)),
            )
            report.add(
                "recommendation",
                "staging_retained",
                f"failed-run staging retained at {workspace.root}",
            )
            return CompileRunResult(
                False,
                map_identity,
                state,
                len(discovery.requests),
                0,
                0,
                0,
                workspace.root,
            )
        else:
            try:
                workspace.cleanup()
            except StagingError as exc:
                report.add("warning", exc.code, exc.detail)
            return CompileRunResult(
                True,
                map_identity,
                state,
                len(discovery.requests),
                len(generation.models),
                len(generation.materials),
                len(committed.published_artifacts),
            )


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


__all__ = [
    "CompileRequest",
    "CompileRunResult",
    "RuntimeExecutionError",
    "execute_compile_run",
]
