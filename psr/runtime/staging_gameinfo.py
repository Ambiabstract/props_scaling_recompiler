"""Build the minimal GameInfo used by StudioMDL inside staging."""

from __future__ import annotations

from collections.abc import Iterable

from psr.assets import MountedSearchPath


def build_staging_gameinfo(mounts: Iterable[MountedSearchPath]) -> bytes:
    """Render ordered, concrete read-only mounts after the staging game root.

    StudioMDL requires a GameInfo.txt beside the temporary ``game`` directory.
    PSR writes only into that first root; every source folder and VPK is then
    exposed as an absolute, ordered ``game`` SearchPath.
    """
    lines = [
        '"GameInfo"',
        "{",
        '\t"game" "PSR staging"',
        '\t"FileSystem"',
        "\t{",
        '\t\t"SearchPaths"',
        "\t\t{",
        '\t\t\t"game+mod+mod_write+default_write_path" "|gameinfo_path|."',
    ]
    for mount in mounts:
        value = str(mount.container_path.resolve())
        if any(character in value for character in ('"', "\r", "\n")):
            raise ValueError(f"SearchPath cannot be represented in GameInfo: {value!r}")
        lines.append(f'\t\t\t"game" "{value}"')
    lines.extend(("\t\t}", "\t}", "}"))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


__all__ = ["build_staging_gameinfo"]
