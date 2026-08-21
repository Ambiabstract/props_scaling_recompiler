"""Validated all-or-nothing publication of managed assets, cache, and VMF."""

from __future__ import annotations

import hashlib
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
from .skin_layout import (
    SkinLayoutOperationPlan,
    commit_skin_layout_plan,
    source_asset_fingerprint,
)
from .vmf_output import VmfOutput, VmfOutputError, build_vmf_output


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


@dataclass(slots=True)
class _PreparedWrite:
    target: Path
    temporary: Path
    sha256: str
    backup: Path | None = None
    installed: bool = False


def build_commit_plan(
    source_vmf: bytes,
    manifest: ProjectManifest,
    operation: OperationPlan,
    materials: ColoredMaterialOperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    generation: GenerationResult,
) -> CommitPlan:
    """Prove staged outputs and build cache/VMF candidates without publishing."""
    identities = {
        operation.map_identity,
        materials.map_identity,
        skin_layout.map_identity,
        generation.map_identity,
    }
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
        vmf_output = build_vmf_output(source_vmf, operation, skin_layout)
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
    if len(actual_models) != len(generation.models) or set(actual_models) != set(expected_models):
        raise CommitError(
            "commit_model_set_mismatch",
            f"validated outputs {sorted(actual_models)!r}, expected {sorted(expected_models)!r}",
        )

    accepted_materials = _accepted_material_outputs(materials, skin_layout)
    actual_materials = {
        item.generated.logical_output_material: item
        for item in generation.materials
    }
    if (
        len(actual_materials) != len(generation.materials)
        or set(actual_materials) != accepted_materials
    ):
        raise CommitError(
            "commit_material_set_mismatch",
            f"validated outputs {sorted(actual_materials)!r}, "
            f"expected {sorted(accepted_materials)!r}",
        )

    artifacts: list[CommitArtifact] = []
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
) -> CommitResult:
    """Publish every planned file with rollback if any replacement fails."""
    game_root = game_directory.resolve(strict=True)
    if not game_root.is_dir():
        raise CommitError("commit_game_not_directory", str(game_root))
    manifest_target = manifest_path.resolve()
    vmf_target = vmf_output_path.resolve()

    writes: list[tuple[Path, Path | bytes, int, str]] = []
    artifact_targets: list[Path] = []
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
        _install_prepared(prepared)
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


def _install_prepared(prepared: list[_PreparedWrite]) -> None:
    token = uuid.uuid4().hex
    try:
        for item in prepared:
            if item.target.exists():
                item.backup = item.target.with_name(
                    f".{item.target.name}.{token}.psr-backup"
                )
                if item.backup.exists():
                    raise CommitError("commit_backup_conflict", str(item.backup))
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
        if isinstance(exc, CommitError) and not rollback_errors:
            raise
        raise CommitError("commit_transaction_failed", detail) from exc
    else:
        for item in prepared:
            if item.backup is not None:
                try:
                    item.backup.unlink(missing_ok=True)
                except OSError:
                    # Publication has succeeded and every installed hash was
                    # verified. A leftover uniquely named backup is safer than
                    # reporting a false failed commit after state changed.
                    pass


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
    "apply_commit_plan",
    "build_commit_plan",
]
