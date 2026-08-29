"""Validated all-or-nothing publication of managed assets, cache, and VMF."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from psr.cache import (
    ColoredMaterialRecord,
    GeneratedModelRecord,
    ProjectManifest,
    manifest_to_json,
)
from psr.domain import canonical_scale_percent

from .generation import GenerationResult
from .materials import ColoredMaterialOperationPlan
from .planning import OperationPlan
from .reuse import ArtifactReusePlan, ExistingArtifact
from .skin_layout import (
    SkinLayoutOperationPlan,
    commit_skin_layout_plan,
    source_asset_fingerprint,
)
from .vmf_output import (
    VmfFallbackAssignment,
    VmfOutput,
    VmfOutputError,
    build_vmf_output,
)


class CommitError(RuntimeError):
    """A categorised refusal or failure during project publication."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CommitArtifact:
    """One staged managed file approved for project publication."""

    logical_path: str
    staged_path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CommitPlan:
    """Fully validated immutable inputs for the final filesystem transaction."""

    map_identity: str
    staging_root: Path
    artifacts: tuple[CommitArtifact, ...]
    existing_artifacts: tuple[ExistingArtifact, ...]
    manifest: ProjectManifest
    manifest_content: bytes
    vmf_output: VmfOutput


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Concrete files installed by a successful commit transaction."""

    map_identity: str
    published_artifacts: tuple[Path, ...]
    manifest_path: Path
    vmf_output_path: Path
    vmf_sha256: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Outcome of rolling back one durable interrupted commit journal."""

    recovered: bool
    restored_targets: tuple[Path, ...] = ()


@dataclass(slots=True)
class _PreparedWrite:
    target: Path
    temporary: Path
    sha256: str
    backup: Path | None = None
    original_existed: bool = False
    installed: bool = False


def build_commit_plan(
    source_vmf: bytes,
    manifest: ProjectManifest,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    generation: GenerationResult,
    reuse: ArtifactReusePlan | None = None,
    *,
    fallbacks: tuple[VmfFallbackAssignment, ...] = (),
) -> CommitPlan:
    """Prove staged outputs and build cache/VMF candidates without publishing."""
    identities = {
        operation.map_identity,
        materials.map_identity,
        skin_layout.map_identity,
        generation.map_identity,
    }
    if reuse is not None:
        identities.add(reuse.map_identity)
    if len(identities) != 1:
        raise CommitError(
            "commit_map_identity_mismatch",
            repr(sorted(identities)),
        )
    if not operation.is_valid or not materials.is_valid or not skin_layout.is_valid:
        raise CommitError(
            "commit_input_plan_invalid",
            "operation, material, and skin-layout plans must all be valid",
        )
    staging_root = generation.staging_root.resolve()
    if not staging_root.is_dir():
        raise CommitError("commit_staging_missing", str(staging_root))

    try:
        vmf_output = build_vmf_output(
            source_vmf,
            operation,
            skin_layout,
            fallbacks=fallbacks,
        )
    except VmfOutputError as exc:
        raise CommitError(exc.code, exc.detail) from exc
    layout_by_model = {
        item.logical_source_model: item
        for item in skin_layout.layouts
    }
    expected_models = {
        item.logical_output_model: item
        for item in operation.generated_models
    }
    actual_models = {
        item.requirement.logical_output_model: item
        for item in generation.models
    }
    reused_model_items = () if reuse is None else reuse.reused_models
    reused_models = {
        item.record.logical_output_model: item
        for item in reused_model_items
    }
    if len(reused_models) != len(reused_model_items):
        raise CommitError("commit_reused_model_duplicate", repr(sorted(reused_models)))
    expected_generated_models = set(expected_models) - set(reused_models)
    if (
        len(actual_models) != len(generation.models)
        or set(actual_models) != expected_generated_models
        or set(actual_models) & set(reused_models)
        or set(actual_models) | set(reused_models) != set(expected_models)
    ):
        raise CommitError(
            "commit_model_set_mismatch",
            f"generated {sorted(actual_models)!r}, reused {sorted(reused_models)!r}, "
            f"expected {sorted(expected_models)!r}",
        )

    accepted_materials = _accepted_material_outputs(materials, skin_layout)
    actual_materials = {
        item.generated.logical_output_material: item
        for item in generation.materials
    }
    reused_material_items = () if reuse is None else reuse.reused_materials
    reused_materials = {
        item.record.logical_output_material: item
        for item in reused_material_items
    }
    if len(reused_materials) != len(reused_material_items):
        raise CommitError(
            "commit_reused_material_duplicate",
            repr(sorted(reused_materials)),
        )
    expected_generated_materials = accepted_materials - set(reused_materials)
    if (
        len(actual_materials) != len(generation.materials)
        or set(actual_materials) != expected_generated_materials
        or set(actual_materials) & set(reused_materials)
        or set(actual_materials) | set(reused_materials) != accepted_materials
    ):
        raise CommitError(
            "commit_material_set_mismatch",
            f"generated {sorted(actual_materials)!r}, "
            f"reused {sorted(reused_materials)!r}, "
            f"expected {sorted(accepted_materials)!r}",
        )

    artifacts: list[CommitArtifact] = []
    existing_artifacts: list[ExistingArtifact] = []
    material_records: list[ColoredMaterialRecord] = []
    material_plan_by_output = {
        item.logical_output_material: item
        for item in materials.colored_materials
    }
    for logical_path in sorted(actual_materials):
        item = actual_materials[logical_path]
        planned = material_plan_by_output.get(logical_path)
        if planned is None or (
            item.generated.logical_source_material != planned.logical_source_material
            or item.generated.render_color != planned.render_color
            or item.generated.color_parameter != planned.color_parameter
            or item.generated.color_assignment != planned.color_assignment
            or item.generated.generation_mode != planned.generation_mode
            or item.generated.source_fingerprint != planned.source_fingerprint
        ):
            raise CommitError(
                "commit_material_identity_mismatch",
                logical_path,
            )
        artifacts.append(_checked_artifact(
            staging_root,
            logical_path,
            item.staged_file.physical_path,
            item.staged_file.size,
            item.staged_file.sha256,
        ))
        material_records.append(ColoredMaterialRecord(
            logical_source_material=planned.logical_source_material,
            render_color=planned.render_color,
            color_parameter=planned.color_parameter,
            generation_mode=planned.generation_mode,
            logical_output_material=planned.logical_output_material,
            source_fingerprint=planned.source_fingerprint,
            artifact_sha256=item.generated.sha256,
        ))

    for logical_path in sorted(reused_materials):
        item = reused_materials[logical_path]
        planned = material_plan_by_output.get(logical_path)
        record = item.record
        if planned is None or (
            record.logical_source_material != planned.logical_source_material
            or record.render_color != planned.render_color
            or record.color_parameter != planned.color_parameter
            or record.generation_mode != planned.generation_mode
            or record.logical_output_material != planned.logical_output_material
            or record.source_fingerprint != planned.source_fingerprint
        ):
            raise CommitError("commit_reused_material_identity_mismatch", logical_path)
        checked = _checked_existing_artifact(item.file)
        if checked.logical_path != logical_path or checked.sha256 != record.artifact_sha256:
            raise CommitError("commit_reused_material_changed", logical_path)
        existing_artifacts.append(checked)
        material_records.append(record)

    model_records: list[GeneratedModelRecord] = []
    for logical_model in sorted(actual_models):
        item = actual_models[logical_model]
        requirement = expected_models[logical_model]
        if item.requirement != requirement:
            raise CommitError("commit_model_identity_mismatch", logical_model)
        layout = layout_by_model.get(requirement.logical_source_model)
        if layout is None:
            raise CommitError(
                "commit_model_layout_missing",
                requirement.logical_source_model,
            )
        expected_files: list[str] = []
        fingerprint = hashlib.sha256()
        for output in sorted(item.validation.files, key=lambda value: value.logical_path):
            artifact = _checked_artifact(
                staging_root,
                output.logical_path,
                output.physical_path,
                output.size,
                output.sha256,
            )
            artifacts.append(artifact)
            expected_files.append(output.logical_path)
            fingerprint.update(output.logical_path.encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(bytes.fromhex(output.sha256))
            fingerprint.update(b"\0")
        if fingerprint.hexdigest() != item.artifact_fingerprint:
            raise CommitError(
                "commit_model_fingerprint_mismatch",
                logical_model,
            )
        model_records.append(GeneratedModelRecord(
            logical_source_model=requirement.logical_source_model,
            compile_scale_percent=canonical_scale_percent(requirement.compile_scale),
            logical_output_model=requirement.logical_output_model,
            requires_static_conversion=requirement.requires_static_conversion,
            skin_layout_fingerprint=layout.layout_fingerprint,
            expected_files=tuple(expected_files),
            artifact_fingerprint=item.artifact_fingerprint,
        ))

    for logical_model in sorted(reused_models):
        item = reused_models[logical_model]
        requirement = expected_models[logical_model]
        layout = layout_by_model.get(requirement.logical_source_model)
        record = item.record
        if layout is None or (
            record.logical_source_model != requirement.logical_source_model
            or record.compile_scale_percent
            != canonical_scale_percent(requirement.compile_scale)
            or record.logical_output_model != requirement.logical_output_model
            or record.requires_static_conversion
            != requirement.requires_static_conversion
            or record.skin_layout_fingerprint != layout.layout_fingerprint
        ):
            raise CommitError("commit_reused_model_identity_mismatch", logical_model)
        checked_files = tuple(
            _checked_existing_artifact(file)
            for file in item.files
        )
        if (
            {file.logical_path for file in checked_files}
            != set(record.expected_files)
            or _existing_model_fingerprint(checked_files)
            != record.artifact_fingerprint
        ):
            raise CommitError("commit_reused_model_changed", logical_model)
        existing_artifacts.extend(checked_files)
        model_records.append(record)

    _validate_reconciliation(
        manifest,
        operation,
        skin_layout,
        tuple(model_records),
    )

    logical_paths = [item.logical_path for item in artifacts]
    if len(set(logical_paths)) != len(logical_paths):
        raise CommitError(
            "commit_artifact_path_duplicate",
            repr(sorted(logical_paths)),
        )
    existing_paths = [item.logical_path for item in existing_artifacts]
    if (
        len(set(existing_paths)) != len(existing_paths)
        or set(logical_paths) & set(existing_paths)
    ):
        raise CommitError(
            "commit_existing_artifact_path_duplicate",
            repr(sorted(existing_paths)),
        )

    candidate = commit_skin_layout_plan(manifest, operation, skin_layout)
    candidate = _merge_generated_records(
        candidate,
        skin_layout,
        tuple(model_records),
        tuple(material_records),
    )
    manifest_content = manifest_to_json(candidate)
    return CommitPlan(
        map_identity=operation.map_identity,
        staging_root=staging_root,
        artifacts=tuple(sorted(artifacts, key=lambda item: item.logical_path)),
        existing_artifacts=tuple(sorted(
            existing_artifacts,
            key=lambda item: item.logical_path,
        )),
        manifest=candidate,
        manifest_content=manifest_content,
        vmf_output=vmf_output,
    )


def apply_commit_plan(
    plan: CommitPlan,
    *,
    game_directory: Path,
    manifest_path: Path,
    vmf_output_path: Path,
    recovery_journal_path: Path | None = None,
) -> CommitResult:
    """Publish every planned file with rollback if any replacement fails."""
    game_root = game_directory.resolve(strict=True)
    if not game_root.is_dir():
        raise CommitError("commit_game_not_directory", str(game_root))
    manifest_target = manifest_path.resolve()
    vmf_target = vmf_output_path.resolve()

    writes: list[tuple[Path, Path | bytes, int, str]] = []
    artifact_targets: list[Path] = []
    existing_targets: list[ExistingArtifact] = []
    for existing in plan.existing_artifacts:
        checked = _checked_existing_artifact(existing)
        target = game_root.joinpath(
            *PurePosixPath(checked.logical_path).parts
        ).resolve()
        if target != checked.physical_path.resolve():
            raise CommitError(
                "commit_existing_artifact_target_mismatch",
                checked.logical_path,
            )
        existing_targets.append(checked)
    for artifact in plan.artifacts:
        checked = _checked_artifact(
            plan.staging_root,
            artifact.logical_path,
            artifact.staged_path,
            artifact.size,
            artifact.sha256,
        )
        target = game_root.joinpath(*PurePosixPath(checked.logical_path).parts).resolve()
        if target != game_root and game_root not in target.parents:
            raise CommitError("commit_target_escape", checked.logical_path)
        writes.append((target, checked.staged_path, checked.size, checked.sha256))
        artifact_targets.append(target)
    writes.extend((
        (
            manifest_target,
            plan.manifest_content,
            len(plan.manifest_content),
            hashlib.sha256(plan.manifest_content).hexdigest(),
        ),
        (
            vmf_target,
            plan.vmf_output.content,
            len(plan.vmf_output.content),
            plan.vmf_output.sha256,
        ),
    ))
    targets = [item[0] for item in writes]
    if len(set(targets)) != len(targets):
        raise CommitError("commit_target_duplicate", repr(targets))

    prepared: list[_PreparedWrite] = []
    try:
        for target, content, size, sha256 in writes:
            prepared.append(_prepare_write(target, content, size, sha256))
        for existing in existing_targets:
            _checked_existing_artifact(existing)
        _install_prepared(prepared, recovery_journal_path=recovery_journal_path)
    except CommitError:
        _discard_temporaries(prepared)
        raise
    except OSError as exc:
        _discard_temporaries(prepared)
        raise CommitError(
            "commit_prepare_failed",
            f"{type(exc).__name__}: {exc}",
        ) from exc

    return CommitResult(
        map_identity=plan.map_identity,
        published_artifacts=tuple(artifact_targets),
        manifest_path=manifest_target,
        vmf_output_path=vmf_target,
        vmf_sha256=plan.vmf_output.sha256,
    )


def recover_interrupted_commit(
    journal_path: Path,
    *,
    game_directory: Path,
    manifest_path: Path,
    vmf_output_path: Path,
) -> RecoveryResult:
    """Rollback an interrupted transaction after strictly validating its journal."""
    journal = journal_path.resolve()
    if not journal.exists():
        return RecoveryResult(False)
    try:
        document = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommitError(
            "commit_recovery_journal_invalid",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "status", "writes"}
        or document.get("schema_version") != 1
        or document.get("status") != "installing"
        or not isinstance(document.get("writes"), list)
    ):
        raise CommitError(
            "commit_recovery_journal_invalid",
            "journal structure or status is not recognised",
        )

    game_root = game_directory.resolve(strict=True)
    manifest_target = manifest_path.resolve()
    vmf_target = vmf_output_path.resolve()
    records: list[tuple[Path, Path, Path | None, bool, str]] = []
    for index, raw in enumerate(document["writes"]):
        if not isinstance(raw, dict) or set(raw) != {
            "target", "temporary", "backup", "original_existed", "sha256"
        }:
            raise CommitError(
                "commit_recovery_journal_invalid",
                f"writes[{index}] has unexpected fields",
            )
        target = _journal_path(raw["target"], f"writes[{index}].target")
        temporary = _journal_path(raw["temporary"], f"writes[{index}].temporary")
        backup_raw = raw["backup"]
        backup = (
            None
            if backup_raw is None
            else _journal_path(backup_raw, f"writes[{index}].backup")
        )
        original_existed = raw["original_existed"]
        sha256 = raw["sha256"]
        if not isinstance(original_existed, bool) or not _is_sha256(sha256):
            raise CommitError(
                "commit_recovery_journal_invalid",
                f"writes[{index}] has invalid metadata",
            )
        _validate_recovery_target(
            target,
            game_root=game_root,
            manifest_target=manifest_target,
            vmf_target=vmf_target,
        )
        if temporary.parent != target.parent or not temporary.name.endswith(".psr-new"):
            raise CommitError(
                "commit_recovery_path_invalid",
                str(temporary),
            )
        if backup is not None and (
            backup.parent != target.parent
            or not backup.name.endswith(".psr-backup")
        ):
            raise CommitError("commit_recovery_path_invalid", str(backup))
        records.append((target, temporary, backup, original_existed, sha256))

    restored: list[Path] = []
    try:
        for target, temporary, backup, original_existed, sha256 in reversed(records):
            if backup is not None and backup.exists():
                if target.exists():
                    if not target.is_file():
                        raise CommitError("commit_recovery_target_not_file", str(target))
                    target.unlink()
                _replace_path(backup, target)
                restored.append(target)
            elif not original_existed and target.exists():
                if not target.is_file() or _file_sha256(target) != sha256:
                    raise CommitError(
                        "commit_recovery_unexpected_target",
                        str(target),
                    )
                target.unlink()
                restored.append(target)
            temporary.unlink(missing_ok=True)
        journal.unlink()
    except OSError as exc:
        raise CommitError(
            "commit_recovery_failed",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    return RecoveryResult(True, tuple(reversed(restored)))


def _accepted_material_outputs(
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
) -> set[str]:
    accepted = {
        (mapping.logical_source_model, mapping.source_skin, mapping.render_color)
        for layout in skin_layout.layouts
        for mapping in layout.mappings
    }
    return {
        logical_path
        for colored_skin in materials.colored_skins
        if (
            colored_skin.logical_source_model,
            colored_skin.source_skin,
            colored_skin.render_color,
        ) in accepted
        for logical_path in colored_skin.logical_colored_materials
    }


def _checked_existing_artifact(artifact: ExistingArtifact) -> ExistingArtifact:
    _validate_managed_logical_path(artifact.logical_path)
    physical = artifact.physical_path.resolve()
    if not physical.is_file():
        raise CommitError("commit_existing_artifact_missing", artifact.logical_path)
    size = physical.stat().st_size
    sha256 = _file_sha256(physical)
    if size != artifact.size or sha256 != artifact.sha256:
        raise CommitError("commit_existing_artifact_changed", artifact.logical_path)
    return ExistingArtifact(artifact.logical_path, physical, size, sha256)


def _existing_model_fingerprint(files: tuple[ExistingArtifact, ...]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value.logical_path):
        digest.update(item.logical_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(item.sha256))
        digest.update(b"\0")
    return digest.hexdigest()


def _checked_artifact(
    staging_root: Path,
    logical_path: str,
    physical_path: Path,
    expected_size: int,
    expected_sha256: str,
) -> CommitArtifact:
    _validate_managed_logical_path(logical_path)
    staged = physical_path.resolve()
    if staged != staging_root and staging_root not in staged.parents:
        raise CommitError("commit_artifact_outside_staging", str(staged))
    if not staged.is_file():
        raise CommitError("commit_artifact_missing", logical_path)
    size = staged.stat().st_size
    digest = _file_sha256(staged)
    if size != expected_size or digest != expected_sha256:
        raise CommitError(
            "commit_artifact_changed",
            f"{logical_path}: staged content differs from validated metadata",
        )
    return CommitArtifact(logical_path, staged, size, digest)


def _validate_managed_logical_path(logical_path: str) -> None:
    if "\\" in logical_path:
        raise CommitError("commit_artifact_path_invalid", logical_path)
    path = PurePosixPath(logical_path)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise CommitError("commit_artifact_path_invalid", logical_path)
    managed_model = (
        logical_path.startswith("models/psr_scaled/")
        and path.suffix in {".mdl", ".vvd", ".vtx", ".phy"}
    )
    managed_material = (
        logical_path.startswith("materials/models/psr_scaled/")
        and path.suffix == ".vmt"
    )
    if not managed_model and not managed_material:
        raise CommitError("commit_artifact_path_unmanaged", logical_path)


def _merge_generated_records(
    manifest: ProjectManifest,
    skin_layout: SkinLayoutOperationPlan,
    models: tuple[GeneratedModelRecord, ...],
    materials: tuple[ColoredMaterialRecord, ...],
) -> ProjectManifest:
    reset_models = {
        item.logical_source_model
        for item in skin_layout.layouts
        if item.cache_reset and not item.rebuild_cached_scales
    }
    model_keys = {
        (item.logical_source_model, item.compile_scale_percent)
        for item in models
    }
    merged_models = [
        item
        for item in manifest.generated_models
        if item.logical_source_model not in reset_models
        and (item.logical_source_model, item.compile_scale_percent) not in model_keys
    ]
    merged_models.extend(models)

    material_keys = {
        (item.logical_source_material, item.render_color)
        for item in materials
    }
    merged_materials = [
        item
        for item in manifest.colored_materials
        if (item.logical_source_material, item.render_color) not in material_keys
    ]
    merged_materials.extend(materials)
    return replace(
        manifest,
        generated_models=tuple(sorted(
            merged_models,
            key=lambda item: (item.logical_source_model, item.compile_scale_percent),
        )),
        colored_materials=tuple(sorted(
            merged_materials,
            key=lambda item: (item.logical_source_material, item.render_color),
        )),
    )


def _validate_reconciliation(
    manifest: ProjectManifest,
    operation: OperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    generated: tuple[GeneratedModelRecord, ...],
) -> None:
    """Refuse a mixed source/layout revision if the caller skipped reconciliation."""
    assets = {
        item.logical_model_path: item
        for item in operation.source_assets
    }
    source_records = {
        item.logical_model_path: item
        for item in manifest.source_assets
    }
    generated_keys = {
        (item.logical_source_model, item.compile_scale_percent)
        for item in generated
    }
    cached_by_model: dict[str, list[GeneratedModelRecord]] = {}
    for item in manifest.generated_models:
        cached_by_model.setdefault(item.logical_source_model, []).append(item)

    for layout in skin_layout.layouts:
        if layout.cache_reset and not layout.rebuild_cached_scales:
            continue
        cached = cached_by_model.get(layout.logical_source_model, [])
        if not cached:
            continue
        asset = assets.get(layout.logical_source_model)
        if asset is None:
            raise CommitError(
                "commit_source_asset_missing",
                layout.logical_source_model,
            )
        previous_source = source_records.get(layout.logical_source_model)
        source_changed = (
            previous_source is None
            or previous_source.source_fingerprint != source_asset_fingerprint(asset)
        )
        layout_changed = any(
            item.skin_layout_fingerprint != layout.layout_fingerprint
            for item in cached
        )
        if not source_changed and not layout_changed:
            continue
        missing = sorted(
            item.compile_scale_percent
            for item in cached
            if (item.logical_source_model, item.compile_scale_percent)
            not in generated_keys
        )
        if missing:
            raise CommitError(
                "commit_reconciliation_incomplete",
                f"{layout.logical_source_model}: cached scales {missing!r} must be "
                "regenerated for the current source/layout revision",
            )


def _prepare_write(
    target: Path,
    content: Path | bytes,
    expected_size: int,
    expected_sha256: str,
) -> _PreparedWrite:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not target.is_file():
        raise CommitError(
            "commit_target_not_file",
            str(target),
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".psr-new",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            if isinstance(content, bytes):
                stream.write(content)
            else:
                with content.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.stat().st_size != expected_size or _file_sha256(temporary) != expected_sha256:
            raise CommitError(
                "commit_temporary_validation_failed",
                str(target),
            )
        return _PreparedWrite(target, temporary, expected_sha256)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _install_prepared(
    prepared: list[_PreparedWrite],
    *,
    recovery_journal_path: Path | None,
) -> None:
    token = uuid.uuid4().hex
    for item in prepared:
        item.original_existed = item.target.exists()
        if item.original_existed:
            item.backup = item.target.with_name(
                f".{item.target.name}.{token}.psr-backup"
            )
            if item.backup.exists():
                raise CommitError("commit_backup_conflict", str(item.backup))
    journal = recovery_journal_path.resolve() if recovery_journal_path is not None else None
    if journal is not None:
        _write_recovery_journal(journal, prepared)
    try:
        for item in prepared:
            if item.original_existed:
                assert item.backup is not None
                _replace_path(item.target, item.backup)
            _replace_path(item.temporary, item.target)
            item.installed = True
        for item in prepared:
            if _file_sha256(item.target) != item.sha256:
                raise CommitError(
                    "commit_installed_hash_mismatch",
                    str(item.target),
                )
    except Exception as exc:
        rollback_errors: list[str] = []
        for item in reversed(prepared):
            try:
                if item.installed:
                    item.target.unlink(missing_ok=True)
                if item.backup is not None and item.backup.exists():
                    _replace_path(item.backup, item.target)
            except OSError as rollback_exc:
                rollback_errors.append(f"{item.target}: {rollback_exc}")
        detail = f"{type(exc).__name__}: {exc}"
        if rollback_errors:
            detail += "; rollback failures: " + "; ".join(rollback_errors)
        if journal is not None and not rollback_errors:
            journal.unlink(missing_ok=True)
        if isinstance(exc, CommitError) and not rollback_errors:
            raise
        raise CommitError("commit_transaction_failed", detail) from exc
    else:
        if journal is not None:
            journal.unlink(missing_ok=True)
        for item in prepared:
            if item.backup is not None:
                try:
                    item.backup.unlink(missing_ok=True)
                except OSError:
                    # Publication has succeeded and every installed hash was
                    # verified. A leftover uniquely named backup is safer than
                    # reporting a false failed commit after state changed.
                    pass


def _write_recovery_journal(path: Path, prepared: list[_PreparedWrite]) -> None:
    document = {
        "schema_version": 1,
        "status": "installing",
        "writes": [
            {
                "target": str(item.target),
                "temporary": str(item.temporary),
                "backup": None if item.backup is None else str(item.backup),
                "original_existed": item.original_existed,
                "sha256": item.sha256,
            }
            for item in prepared
        ],
    }
    content = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _journal_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CommitError("commit_recovery_journal_invalid", f"{label} is not a path")
    return Path(value).resolve()


def _validate_recovery_target(
    target: Path,
    *,
    game_root: Path,
    manifest_target: Path,
    vmf_target: Path,
) -> None:
    if target in {manifest_target, vmf_target}:
        return
    managed_roots = (
        (game_root / "models" / "psr_scaled").resolve(),
        (game_root / "materials" / "models" / "psr_scaled").resolve(),
    )
    if any(root in target.parents for root in managed_roots):
        return
    raise CommitError("commit_recovery_target_unmanaged", str(target))


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _discard_temporaries(prepared: list[_PreparedWrite]) -> None:
    for item in prepared:
        item.temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_path(source: Path, destination: Path) -> None:
    os.replace(source, destination)


__all__ = [
    "CommitArtifact",
    "CommitError",
    "CommitPlan",
    "CommitResult",
    "RecoveryResult",
    "apply_commit_plan",
    "build_commit_plan",
    "recover_interrupted_commit",
]
