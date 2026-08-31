---
name: psr-dev-cycle
description: Complete and verify changes to props_scaling_recompiler working code, tests, runtime behavior, or build configuration. Use for implementation tasks in this repository that need scoped regression tests, release validation, or installation of the verified EXE; do not use for read-only analysis or documentation-only edits.
---

# PSR development cycle

Finish a PSR change without overwriting user work or leaving the manually tested executable stale.

## Start safely

1. Read the repository `AGENTS.md` and inspect `git status --short` plus the relevant diff before editing.
2. Treat every pre-existing modification and untracked file as user-owned. Work around unrelated changes and never revert them.
3. Read `docs/PROJECT_CONTEXT.md` in full only when `AGENTS.md` or `$psr-pipeline-contracts` requires it for the current scope.
4. Identify the production path before editing. `psr_entrypoint.py` and `psr.cli` are the 2.0 entry points; the root prototype is not.

## Implement and verify

1. For changed behavior, add the smallest fixture or regression test that demonstrates the contract before or with the implementation.
2. Read [references/verification-matrix.md](references/verification-matrix.md) and run the narrow tests for every affected area. Add adjacent tests when a dependency crosses areas.
3. Check Python syntax, structural validity of generated formats, and the final diff. Do not infer correctness only from a successful subprocess exit.
4. External SDK, Antenna, Hammer, BSP, or game checks are opt-in. If the user explicitly requests one, first read [references/external-validation.md](references/external-validation.md). Do not run them merely because a unit test would benefit from more confidence.

## Finish the change

- Documentation-only work does not require an EXE build or installation.
- After every useful change to working code, behavior, dependencies, or build configuration that passes its narrow checks, run `scripts/install-verified-release.ps1` from this skill. It runs the repository release build, the full frozen regression, copies only the permitted executable into the SDK, and verifies SHA-256 equality.
- The script does not grant filesystem access. Request the environment's required escalation immediately before running it when the SDK destination is outside the writable workspace.
- If release validation fails, do not install an older or unverified executable. Report the failing gate and preserve the last installed file.
- End with the tests run, release/install result when applicable, source and installed hashes, and any checks deliberately not run.

