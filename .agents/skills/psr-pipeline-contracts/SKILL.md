---
name: psr-pipeline-contracts
description: Apply PSR 2.0 domain contracts when designing, implementing, diagnosing, or reviewing VMF, GameInfo/SearchPaths, MDL/QC, VMT/material, scale/skin, cache, cleanup, generation, validation, or commit behavior. Do not use for packaging-only, generic CLI presentation, or documentation-only tasks that do not change these contracts.
---

# PSR pipeline contracts

Protect the established Source SDK 2013 SP behavior while changing the PSR pipeline.

## Load only the needed contracts

Read every reference whose boundary the task touches:

- Scale parsing, generated model identity, source skin normalization, geometry scale, or dynamic bodygroup fallback: [references/scale-and-models.md](references/scale-and-models.md).
- VMF edits, hidden entities, KeyValues preservation, GameInfo, SearchPaths, folders, or VPK resolution: [references/vmf-and-resolution.md](references/vmf-and-resolution.md).
- Discovery/planning, artifact reuse, cache schema, commit, cleanup, recovery, outcomes, or reports: [references/pipeline-cache-and-reporting.md](references/pipeline-cache-and-reporting.md).
- Tinting, VMT/Patch generation, material identity, skin-family layout, or StudioMDL material limits: [references/materials-and-skins.md](references/materials-and-skins.md).

Do not load unrelated references merely because they exist. If a change crosses boundaries, read all affected references before deciding the design.

## Architecture gate

Before an architectural change, data-format change, VMF/QC/VMT pipeline change, or asset-generation rule change, read `docs/PROJECT_CONTEXT.md` completely. Treat it as design context, then confirm current behavior in production code and regression tests; documentation alone is not proof that an incomplete path works.

`props_scaling_recompiler_v1.1.2.py` is a behavioral reference, not an architectural template. The root `props_scaling_recompiler.py` is an unfinished prototype. Implement production behavior through the `psr` package and the complete `discover -> plan -> generate -> validate -> commit` path.

Use `$psr-dev-cycle` for regression selection, implementation verification, release building, and EXE installation after an actual code or build change.

