"""One deterministic deduplicated end-of-run diagnostic report."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO


Severity = Literal["error", "warning", "info"]
InputSeverity = Literal["error", "warning", "info", "recommendation"]


@dataclass(frozen=True, slots=True)
class ReportEntry:
    severity: Severity
    code: str
    detail: str
    entity_id: str | None = None
    source_line: int | None = None


class DiagnosticReport:
    """Accumulate diagnostics and render them once in stable grouped order."""

    def __init__(self) -> None:
        self._entries: dict[tuple[object, ...], ReportEntry] = {}

    @property
    def entries(self) -> tuple[ReportEntry, ...]:
        order = {"error": 0, "warning": 1, "info": 2}
        return tuple(sorted(
            self._entries.values(),
            key=lambda item: (
                order[item.severity],
                item.code,
                item.entity_id or "",
                item.source_line or -1,
                item.detail,
            ),
        ))

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self._entries.values())

    def add(
        self,
        severity: InputSeverity,
        code: str,
        detail: str,
        *,
        entity_id: str | None = None,
        source_line: int | None = None,
    ) -> None:
        normalised: Severity = "info" if severity == "recommendation" else severity
        entry = ReportEntry(normalised, code, detail, entity_id, source_line)
        key = (normalised, code, detail, entity_id, source_line)
        self._entries.setdefault(key, entry)

    def extend_pipeline(self, diagnostics: object) -> None:
        for item in diagnostics:
            self.add(
                item.severity,
                item.code,
                item.detail,
                entity_id=item.entity_id,
                source_line=item.source_line,
            )

    def render(self, *, color: bool = False) -> str:
        groups = (
            ("error", "ERRORS", "31"),
            ("warning", "WARNINGS", "33"),
            ("info", "INFO", "36"),
        )
        lines = ["", "=== props_scaling_recompiler summary ==="]
        for severity, title, ansi in groups:
            entries = [item for item in self.entries if item.severity == severity]
            if not entries:
                continue
            grouped: dict[tuple[str, str], list[ReportEntry]] = {}
            for item in entries:
                grouped.setdefault((item.code, item.detail), []).append(item)
            heading = f"{title} ({len(grouped)})"
            if color:
                heading = f"\x1b[{ansi}m{heading}\x1b[0m"
            lines.append(heading)
            for (code, detail), items in grouped.items():
                entity_ids = sorted(
                    {item.entity_id for item in items if item.entity_id is not None},
                    key=lambda value: (
                        (0, int(value)) if value.isdecimal() else (1, value)
                    ),
                )
                source_lines = sorted({
                    item.source_line for item in items if item.source_line is not None
                })
                location: list[str] = []
                if entity_ids:
                    label = "entity" if len(entity_ids) == 1 else "entities"
                    location.append(f"{label} {', '.join(entity_ids)}")
                if source_lines:
                    label = "line" if len(source_lines) == 1 else "lines"
                    location.append(f"{label} {', '.join(map(str, source_lines))}")
                suffix = f" ({', '.join(location)})" if location else ""
                lines.append(f"  [{code}]{suffix} {detail}")
        if len(lines) == 2:
            lines.append("No errors or warnings.")
        return "\n".join(lines) + "\n"

    def print(self, stream: TextIO | None = None) -> None:
        if stream is None:
            stream = sys.stdout
        color = _enable_console_color(stream)
        stream.write(self.render(color=color))
        stream.flush()

    def write_log(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(color=False), encoding="utf-8")


def _enable_console_color(stream: TextIO) -> bool:
    if not getattr(stream, "isatty", lambda: False)():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


__all__ = ["DiagnosticReport", "InputSeverity", "ReportEntry", "Severity"]
