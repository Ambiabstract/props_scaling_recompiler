"""Explicit destructive debug cleanup confined to PSR-owned paths."""

from __future__ import annotations

import os
import shutil
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .state import ProjectLock, ProjectStatePaths


@dataclass(frozen=True, slots=True)
class DebugCleanupResult:
    mode: int
    removed_files: int
    removed_bytes: int


def perform_debug_cleanup(
    mode: int,
    *,
    game_directory: Path,
    state: ProjectStatePaths,
    current_lock_held: bool = False,
) -> DebugCleanupResult:
    """Apply cleanup mode 1 or 2 before the normal compile pipeline starts."""
    if mode not in {1, 2}:
        raise ValueError("debug cleanup mode must be 1 or 2")
    game = game_directory.resolve(strict=True)
    projects_root = state.root.parent.resolve()
    current_root = state.root.resolve()
    if current_root.parent != projects_root:
        raise ValueError(
            f"current project state escaped the PSR projects directory: {current_root}"
        )
    project_roots = _project_roots(projects_root, current_root)
    locks = project_roots if mode == 2 else (current_root,)
    if current_lock_held:
        locks = tuple(root for root in locks if root != current_root)
    removed_files = 0
    removed_bytes = 0
    with ExitStack() as stack:
        for root in locks:
            stack.enter_context(ProjectLock(
                root / "operation.lock",
                map_identity=f"debug-cleanup-{mode}",
            ))
        for relative in (
            Path("models") / "psr_scaled",
            Path("materials") / "models" / "psr_scaled",
        ):
            count, size = _remove_tree(_exact_child(game, relative))
            removed_files += count
            removed_bytes += size
        if mode == 1:
            for path in (
                state.manifest,
                state.recovery_journal,
                *state.root.glob("manifest.*.json"),
            ):
                count, size = _remove_path(path)
                removed_files += count
                removed_bytes += size
        else:
            for root in project_roots:
                for child in tuple(root.iterdir()):
                    if child.name in {"operation.lock", "logs"}:
                        continue
                    count, size = _remove_path(child)
                    removed_files += count
                    removed_bytes += size
            work_root = projects_root.parent / "work"
            count, size = _remove_tree(work_root)
            removed_files += count
            removed_bytes += size
    return DebugCleanupResult(mode, removed_files, removed_bytes)


def _project_roots(projects_root: Path, current_root: Path) -> tuple[Path, ...]:
    roots = {current_root.resolve()}
    if projects_root.is_dir():
        for child in projects_root.iterdir():
            resolved = child.resolve()
            if (
                child.is_dir()
                and resolved.parent == projects_root
                and len(child.name) == 64
                and all(
                    character in "0123456789abcdefABCDEF"
                    for character in child.name
                )
            ):
                roots.add(resolved)
    return tuple(sorted(roots, key=lambda path: os.path.normcase(str(path))))


def _exact_child(root: Path, relative: Path) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError(f"cleanup target escaped the game directory: {target}")
    return target


def _remove_tree(path: Path) -> tuple[int, int]:
    if not path.exists() and not path.is_symlink():
        return 0, 0
    count, size = _tree_size(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)
    return count, size


def _remove_path(path: Path) -> tuple[int, int]:
    return _remove_tree(path)


def _tree_size(path: Path) -> tuple[int, int]:
    if path.is_symlink() or path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    count = 0
    size = 0
    for directory, _subdirectories, files in os.walk(path, followlinks=False):
        for filename in files:
            count += 1
            try:
                size += (Path(directory) / filename).stat().st_size
            except OSError:
                pass
    return count, size


__all__ = ["DebugCleanupResult", "perform_debug_cleanup"]
