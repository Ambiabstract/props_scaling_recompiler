"""Production runtime state, locking, reporting, and CLI orchestration."""

from .reporting import DiagnosticReport, ReportEntry
from .app import (
    CompileRequest,
    CompileRunResult,
    RuntimeExecutionError,
    execute_compile_run,
)
from .state import (
    ProjectLock,
    ProjectLockError,
    ProjectStatePaths,
    build_project_state_paths,
)

__all__ = [
    "DiagnosticReport",
    "CompileRequest",
    "CompileRunResult",
    "ProjectLock",
    "ProjectLockError",
    "ProjectStatePaths",
    "ReportEntry",
    "RuntimeExecutionError",
    "build_project_state_paths",
    "execute_compile_run",
]
