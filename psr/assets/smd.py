"""Minimal deterministic SMD helpers for staged StudioMDL compatibility."""

from __future__ import annotations


class SMDTransformError(ValueError):
    """A categorised failure to derive a staging-only SMD artifact."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def build_empty_bodygroup_smd(source: bytes) -> bytes:
    """Preserve a reference SMD skeleton while removing all mesh triangles.

    Source SDK 2013 SP StudioMDL can crash when a dynamic model is converted
    to ``$staticprop`` and a bodygroup begins with the QC ``blank`` token.  A
    zero-triangle SMD represents the same bodygroup choice while preserving
    its numeric index and compiles safely.  This helper only produces staged
    input; source SMD files are never modified.
    """
    if b"\0" in source:
        raise SMDTransformError("smd_nul_byte", "SMD text contains a NUL byte")
    newline = b"\r\n" if b"\r\n" in source else b"\n"
    lines = source.splitlines()
    version = _find_version(lines)
    nodes_start, nodes_end = _section(lines, b"nodes", "smd_nodes_missing")
    skeleton_start, skeleton_end = _section(
        lines,
        b"skeleton",
        "smd_skeleton_missing",
    )
    triangles_start, _triangles_end = _section(
        lines,
        b"triangles",
        "smd_triangles_missing",
    )
    if not (
        version < nodes_start < nodes_end < skeleton_start
        < skeleton_end < triangles_start
    ):
        raise SMDTransformError(
            "smd_section_order_invalid",
            "expected version, nodes, skeleton, and triangles in that order",
        )
    output = [
        b"version 1",
        *lines[nodes_start:nodes_end + 1],
        *lines[skeleton_start:skeleton_end + 1],
        b"triangles",
        b"end",
    ]
    return newline.join(output) + newline


def _find_version(lines: list[bytes]) -> int:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(b"//"):
            continue
        if stripped.lower() != b"version 1":
            raise SMDTransformError(
                "smd_version_invalid",
                f"expected 'version 1', got {stripped!r}",
            )
        return index
    raise SMDTransformError("smd_version_missing", "SMD has no version line")


def _section(
    lines: list[bytes],
    name: bytes,
    missing_code: str,
) -> tuple[int, int]:
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().lower() == name
    ]
    if not starts:
        raise SMDTransformError(missing_code, f"SMD has no {name.decode('ascii')} section")
    if len(starts) != 1:
        raise SMDTransformError(
            "smd_section_duplicate",
            f"SMD has {len(starts)} {name.decode('ascii')} sections",
        )
    start = starts[0]
    for index in range(start + 1, len(lines)):
        if lines[index].strip().lower() == b"end":
            return start, index
    raise SMDTransformError(
        "smd_section_unclosed",
        f"SMD {name.decode('ascii')} section has no end marker",
    )


__all__ = ["SMDTransformError", "build_empty_bodygroup_smd"]
