"""Project-wide cache statistics derived from a validated manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from psr.cache import ProjectManifest


@dataclass(frozen=True, slots=True)
class ProjectCacheSummary:
    source_models: int
    model_variations: int
    material_variations: int
    skin_variations: int
    maps: int
    entity_usages: int
    managed_files: int
    managed_bytes: int
    missing_files: int


def build_project_cache_summary(
    game_directory: Path,
    manifest: ProjectManifest,
) -> ProjectCacheSummary:
    """Count cached variations and the current bytes of their managed files."""
    game = game_directory.resolve()
    logical_files = {
        logical
        for model in manifest.generated_models
        for logical in model.expected_files
    }
    logical_files.update(
        material.logical_output_material
        for material in manifest.colored_materials
    )
    managed_files = 0
    managed_bytes = 0
    missing_files = 0
    for logical in sorted(logical_files):
        physical = _managed_physical_path(game, logical)
        if physical is None or not physical.is_file():
            missing_files += 1
            continue
        managed_files += 1
        try:
            managed_bytes += physical.stat().st_size
        except OSError:
            managed_files -= 1
            missing_files += 1
    return ProjectCacheSummary(
        source_models=len(manifest.source_assets),
        model_variations=len(manifest.generated_models),
        material_variations=len(manifest.colored_materials),
        skin_variations=len(manifest.skin_mappings),
        maps=len({item.map_identity for item in manifest.map_usages}),
        entity_usages=len(manifest.map_usages),
        managed_files=managed_files,
        managed_bytes=managed_bytes,
        missing_files=missing_files,
    )


def _managed_physical_path(game: Path, logical_path: str) -> Path | None:
    pure = PurePosixPath(logical_path.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        return None
    folded = tuple(part.casefold() for part in pure.parts)
    if not (
        folded[:2] == ("models", "psr_scaled")
        or folded[:3] == ("materials", "models", "psr_scaled")
    ):
        return None
    physical = game.joinpath(*pure.parts).resolve()
    return physical if physical.is_relative_to(game) else None


__all__ = ["ProjectCacheSummary", "build_project_cache_summary"]
