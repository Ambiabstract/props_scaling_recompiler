"""Windows console entry point for props_scaling_recompiler 2.0."""

from __future__ import annotations

import argparse
import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from psr import __version__
from psr.pipeline import CommitError, VmfOutputError
from psr.runtime import (
    CompileRequest,
    DiagnosticReport,
    ProjectLockError,
    RuntimeExecutionError,
    execute_compile_run,
)


_DEPRECATED_FLAGS = (
    "subfolders",
    "force_recompile",
    "check_origs",
    "remove_unused",
    "debug",
)


class CliArgumentError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliArgumentError(message)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="props_scaling_recompiler",
        description="Compile Hammer++ prop_static_scalable entities for Source SDK 2013 SP.",
    )
    parser.add_argument("-game", required=True, help="Source game/mod directory")
    parser.add_argument("-vmf_in", required=True, help="input VMF path")
    parser.add_argument("-vmf_out", required=True, help="output VMF path")
    parser.add_argument(
        "-subfolders",
        type=_zero_or_one,
        default=None,
        help="deprecated compatibility argument; accepted but ignored",
    )
    parser.add_argument(
        "-force_recompile",
        type=_zero_or_one,
        default=None,
        help="deprecated compatibility argument; accepted but ignored",
    )
    parser.add_argument(
        "-check_origs",
        type=_zero_or_one,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-remove_unused",
        type=_zero_or_one,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-debug",
        type=_zero_or_one,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    report = DiagnosticReport()
    result = None
    try:
        args = build_arg_parser().parse_args(argv)
        for name in _DEPRECATED_FLAGS:
            value = getattr(args, name)
            if value is not None:
                report.add(
                    "warning",
                    "deprecated_cli_argument",
                    f"-{name}={value} is accepted for compatibility but ignored in 2.0",
                )
        _validate_platform()
        application_dir = _application_directory()
        crowbar, studiomdl = discover_tool_commands(application_dir)
        result = execute_compile_run(
            CompileRequest(
                game_directory=Path(args.game),
                vmf_input_path=Path(args.vmf_in),
                vmf_output_path=Path(args.vmf_out),
                engine_root=_engine_root(application_dir),
                crowbar_command=crowbar,
                studiomdl_command=studiomdl,
            ),
            report,
        )
    except CliArgumentError as exc:
        report.add("error", "invalid_arguments", str(exc))
        report.add(
            "recommendation",
            "cli_usage",
            "use -game <dir> -vmf_in <input.vmf> -vmf_out <output.vmf>",
        )
    except (
        RuntimeExecutionError,
        ProjectLockError,
        CommitError,
        VmfOutputError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        report.add(
            "error",
            getattr(exc, "code", "runtime_failed"),
            getattr(exc, "detail", f"{type(exc).__name__}: {exc}"),
        )
    except Exception as exc:  # Last-resort boundary: never claim success.
        report.add(
            "error",
            "internal_error",
            f"{type(exc).__name__}: {exc}",
        )
        report.add(
            "recommendation",
            "internal_traceback",
            traceback.format_exc(),
        )

    if result is not None:
        outcome = "SUCCESS" if result.success and not report.has_errors else "FAILED"
        print(
            f"PSR {__version__}: {outcome}; entities={result.active_entities}, "
            f"models={result.generated_models}, materials={result.generated_materials}, "
            f"published_files={result.published_files}"
        )
        _write_report_log(report, result.state.logs)
    report.print()
    return 1 if report.has_errors or (result is not None and not result.success) else 0


def discover_tool_commands(
    application_directory: Path,
) -> tuple[tuple[Path, ...] | None, tuple[Path, ...] | None]:
    """Locate the approved external tools without bundling either into PSR."""
    root = application_directory.resolve()
    crowbar = _first_file((
        root / "third-party" / "CrowbarCommandLineDecomp.exe",
        root / "CrowbarCommandLineDecomp.exe",
    ))
    studiomdl = _first_file((root / "studiomdl.exe",))
    return (
        None if crowbar is None else (crowbar,),
        None if studiomdl is None else (studiomdl,),
    )


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _engine_root(application_directory: Path) -> Path | None:
    candidate = application_directory.resolve().parent.parent / "Half-Life 2"
    return candidate if candidate.is_dir() else None


def _validate_platform() -> None:
    if os.name != "nt":
        raise RuntimeExecutionError(
            "unsupported_platform",
            "PSR 2.0 supports only Windows 10/11 x64",
        )
    if sys.maxsize <= 2**32 or platform.machine().casefold() not in {
        "amd64", "x86_64"
    }:
        raise RuntimeExecutionError(
            "unsupported_architecture",
            "PSR 2.0 requires a 64-bit Windows process on x64 Windows",
        )
    version = sys.getwindowsversion()
    if version.major < 10:
        raise RuntimeExecutionError(
            "unsupported_windows_version",
            "PSR 2.0 supports only Windows 10 and Windows 11",
        )


def _first_file(candidates: Sequence[Path]) -> Path | None:
    return next((item.resolve() for item in candidates if item.is_file()), None)


def _zero_or_one(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected 0 or 1") from exc
    if parsed not in {0, 1}:
        raise argparse.ArgumentTypeError("expected 0 or 1")
    return parsed


def _write_report_log(report: DiagnosticReport, directory: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        report.write_log(directory / f"run-{stamp}-{os.getpid()}.log")
    except OSError as exc:
        report.add(
            "warning",
            "report_log_write_failed",
            f"{type(exc).__name__}: {exc}",
        )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_arg_parser", "discover_tool_commands", "main"]
