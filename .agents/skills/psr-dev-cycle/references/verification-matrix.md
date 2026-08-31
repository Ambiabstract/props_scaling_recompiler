# Verification matrix

Use the repository Python environment when present. Start with the narrowest relevant tests; the release helper later runs the full suite again.

| Changed area | Primary regression tests |
|---|---|
| `psr/domain/scale.py`, scale parsing, suffix naming, geometry scale | `tests/test_scale_fixture.py`, `tests/test_vmf_discovery_planning.py` |
| `psr/keyvalues`, VMF discovery or output | `tests/test_vmf_output.py`, `tests/test_vmf_discovery_planning.py` |
| GameInfo or SearchPaths | `tests/test_srctools_searchpaths.py`, `tests/test_staging_gameinfo.py` |
| MDL metadata or bodygroups | `tests/test_srctools_mdl.py`, `tests/test_vmf_discovery_planning.py`, `tests/test_skin_layout.py` |
| QC planning or transformation | `tests/test_qc_planning.py`, `tests/test_qc_transformer.py`, `tests/test_staging_toolchain.py` |
| VMT generation, tinting, or skin layout | `tests/test_srctools_vmt.py`, `tests/test_skin_layout.py`, `tests/test_qc_planning.py` |
| Generation, staging, reuse, or reconciliation | `tests/test_generation_pipeline.py`, `tests/test_staging_toolchain.py`, `tests/test_skin_layout.py` |
| Manifest/cache or commit/recovery | `tests/test_cache_manifest.py`, `tests/test_commit_recovery.py`, `tests/test_generation_pipeline.py` |
| Runtime coordination, outcomes, progress, reporting, cleanup, summary | `tests/test_runtime_foundation.py`, `tests/test_outcomes.py`, `tests/test_generation_pipeline.py` |
| CLI, entry point, version, or argument compatibility | `tests/test_cli.py`, `tests/test_project_foundation.py` |
| PyInstaller spec, hooks, dependencies, or frozen behavior | `tests/test_frozen_executable.py`, `tests/test_fixture_inventory.py` plus the release build |

Useful commands:

```powershell
py -m pytest -q <selected-test-files>
py -m pytest -q
```

Tests marked `external_sdk` are not part of ordinary verification and require explicit user authorization for the concrete run. Never enable `PSR_RUN_EXTERNAL_SDK` or `PSR_RUN_EXTERNAL_RUNTIME` implicitly.

When output formats change, validate more than textual equality:

- VMF: parse before and after, confirm affected entity identity/count, and preserve unrelated bytes.
- MDL: validate the header, internal model name, static flag, and required companion set.
- VMT: parse generated output and validate dependency and generation fingerprints.
- Cache: exercise migration, corrupt/incompatible recovery, atomic replacement, and project isolation.
- Commit: confirm that no VMF or manifest reference can precede validated artifact publication.

