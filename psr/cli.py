"""Windows console entry point for props_scaling_recompiler 2.0."""

from __future__ import annotations

import argparse
import os
import sys
import time
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
    ProgressReporter,
    RuntimeExecutionError,
    deliver_passthrough_vmf,
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
        "-dynamic_fallback",
        type=_zero_or_one,
        default=1,
        help=(
            "use prop_dynamic_override for failed entities (default: 1); "
            "0 leaves them as prop_static_scalable"
        ),
    )
    parser.add_argument(
        "-debug_cleanup",
        type=_zero_one_or_two,
        default=0,
        help=(
            "destructive PSR debug cleanup before compilation: 0=off, "
            "1=current project assets/cache, 2=all caches and temporary files"
        ),
    )
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
    started = time.perf_counter()
    _print_banner()
    report = DiagnosticReport()
    result = None
    args = None
    emergency_vmf_delivered = False
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
        with ProgressReporter() as progress:
            result = execute_compile_run(
                CompileRequest(
                    game_directory=Path(args.game),
                    vmf_input_path=Path(args.vmf_in),
                    vmf_output_path=Path(args.vmf_out),
                    engine_root=_engine_root(application_dir),
                    crowbar_command=crowbar,
                    studiomdl_command=studiomdl,
                    dynamic_fallback=bool(args.dynamic_fallback),
                    debug_cleanup=args.debug_cleanup,
                ),
                report,
                progress,
            )
    except CliArgumentError as exc:
        report.add("error", "invalid_arguments", str(exc))
        report.add(
            "recommendation",
            "cli_usage",
            (
                "place props_scaling_recompiler.exe in the Source SDK 2013 SP "
                "bin directory and run it from a Hammer++ compile configuration "
                "with -game, -vmf_in, and -vmf_out"
            ),
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
        if args is not None and not isinstance(exc, ProjectLockError):
            emergency_vmf_delivered = _try_emergency_passthrough(args, report)
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
        if args is not None:
            emergency_vmf_delivered = _try_emergency_passthrough(args, report)

    if result is not None:
        project_summary = result.project_summary
        if project_summary is not None:
            missing = (
                f", missing_files={project_summary.missing_files}"
                if project_summary.missing_files else ""
            )
            report.add(
                "info",
                "project_summary",
                (
                    f"cache: source_models={project_summary.source_models}, "
                    f"model_variations={project_summary.model_variations}, "
                    f"material_variations={project_summary.material_variations}, "
                    f"skin_variations={project_summary.skin_variations}, "
                    f"maps={project_summary.maps}, "
                    f"entity_usages={project_summary.entity_usages}; "
                    f"managed_files={project_summary.managed_files}, "
                    f"size={_format_bytes(project_summary.managed_bytes)}{missing}",
                ),
            )
        outcome = (
            "SUCCESS" if result.success and not report.has_errors
            else "PARTIAL" if result.success
            else "FAILED"
        )
        report.add(
            "info",
            "session_summary",
            f"PSR {__version__}: {outcome}; entities={result.active_entities}, "
            f"models_generated={result.generated_models}, "
            f"models_reused={result.reused_models}, "
            f"materials_generated={result.generated_materials}, "
            f"materials_reused={result.reused_materials}, "
            f"published_files={result.published_files}",
        )
    elapsed = time.perf_counter() - started
    report.add("info", "elapsed_time", _format_elapsed(elapsed))
    if result is not None:
        _write_report_log(report, result.state.logs)
    report.print()
    return 0 if (result is not None and result.success) or emergency_vmf_delivered else 1


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


def _print_banner() -> None:
    print(f"props_scaling_recompiler {__version__}")
    print("Created by Ambiabstract (Sergey Shavin)")
    print("https://github.com/Ambiabstract | Discord: @Ambiabstract")
    print()


def _format_elapsed(seconds: float) -> str:
    hours, remainder = divmod(max(0.0, seconds), 3600)
    minutes, remainder = divmod(remainder, 60)
    return (
        f"elapsed {int(hours)}h {int(minutes)}m {remainder:.2f}s"
    )


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KiB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MiB"
    return f"{size / (1024 * 1024 * 1024):.2f} GiB"


def _try_emergency_passthrough(args: argparse.Namespace, report: DiagnosticReport) -> bool:
    try:
        deliver_passthrough_vmf(Path(args.vmf_in), Path(args.vmf_out))
    except (OSError, UnicodeError, ValueError) as exc:
        report.add(
            "error",
            "vmf_passthrough_failed",
            f"{type(exc).__name__}: {exc}",
        )
        return False
    report.add(
        "info",
        "vmf_passthrough_written",
        "the validated original VMF was delivered unchanged after an early failure",
    )
    return True


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
    process_architecture = os.environ.get("PROCESSOR_ARCHITECTURE", "").casefold()
    native_architecture = os.environ.get("PROCESSOR_ARCHITEW6432", "").casefold()
    if sys.maxsize <= 2**32 or (native_architecture or process_architecture) not in {
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


def _zero_one_or_two(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected 0, 1, or 2") from exc
    if parsed not in {0, 1, 2}:
        raise argparse.ArgumentTypeError("expected 0, 1, or 2")
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
