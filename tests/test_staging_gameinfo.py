from pathlib import Path

import pytest

from psr.assets import MountedSearchPath, parse_search_paths_text
from psr.runtime.staging_gameinfo import build_staging_gameinfo


def _mount(index: int, path: str, *, kind: str = "folder") -> MountedSearchPath:
    return MountedSearchPath(
        mount_index=index,
        source_ordinal=index,
        expansion_index=0,
        path_id="game",
        raw_value=path,
        kind=kind,
        container_path=Path(path),
    )


def test_staging_gameinfo_preserves_concrete_mount_order(tmp_path: Path) -> None:
    first = _mount(0, str(tmp_path / "content"))
    second = _mount(1, str(tmp_path / "pak01_dir.vpk"), kind="vpk")

    content = build_staging_gameinfo((first, second))
    specs = parse_search_paths_text(
        content.decode("utf-8"),
        filename="staging/GameInfo.txt",
    )

    assert [spec.path_id for spec in specs] == [
        "game+mod+mod_write+default_write_path",
        "game",
        "game",
    ]
    assert [spec.raw_value for spec in specs] == [
        "|gameinfo_path|.",
        str(first.container_path.resolve()),
        str(second.container_path.resolve()),
    ]


def test_staging_gameinfo_rejects_unrepresentable_path() -> None:
    mount = _mount(0, 'C:/broken"path')

    with pytest.raises(ValueError, match="cannot be represented"):
        build_staging_gameinfo((mount,))
