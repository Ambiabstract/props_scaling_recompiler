# Pipeline, cache, cleanup, and reporting contracts

## Transaction boundary

- Preserve the pipeline shape `discover -> plan -> generate -> validate -> commit`.
- Discovery and planning are read-only. Generation publishes only to operation staging. Permanent managed assets, manifest/cache, and VMF change only through a validated commit plan.
- Never write a VMF reference to an unvalidated model. Do not delete an older artifact before resolving and preparing its original source.
- A subprocess success code is not artifact validation. Re-check the generated format, identity, provenance, companion files, and fingerprints required by that artifact type.

## Project and cache identity

- Share cache between maps only inside one project. The normalized identity of `GameInfo.txt` is the project boundary.
- Never merge cache data, `MapUsage`, cleanup, locks, or recovery state across projects, even when map names and logical asset paths match.
- Version and migrate the cache and write it atomically. Corrupt or incompatible cache input must recover safely rather than break the build.
- Store a generated result as a concrete parameter combination, not independent scale/color/skin lists.
- Keep source assets, compiled scale models, colored materials, skin mappings, and map usage as separate concepts.
- Assign colored skin-family indices deterministically. Never make set or unsorted-dictionary iteration part of a persistent format.

## Cleanup and reconciliation

- `models/psr_scaled/` and `materials/models/psr_scaled/` are PSR-managed spaces. Normal cleanup may update them aggressively only from a valid manifest/cache and a completed operation plan.
- Never treat managed output as a source asset.
- Do not remove legacy `_scaled_XXX` or `_static` files outside `psr_scaled` during normal cleanup. A separate migration mode requires reference inventory and a report before mutation.
- Ordinary compile-runs do not delete or compact unused colored mappings.
- Project-wide compaction requires an explicit cleanup operation, dry-run, proof of absent `MapUsage`, a warning about index shifts, and the list of maps and scale variants that must be rebuilt.

## Outcomes and reports

- Ordinary compile-runs must not call interactive `input()`.
- End every run, including no-op and failure, with one deduplicated report containing exactly three severity groups:
  - `ERROR`: a required static result could not be created or supplied, even if the entity safely fell back to dynamic;
  - `WARNING`: a static result exists, but the request/result was normalized, clamped, limited, or has a known nuance;
  - `INFO`: project/session statistics, elapsed time, and optional short facts.
- Put repair guidance into its associated error or warning instead of inventing another severity.
- Preserve textual severity labels; color cannot be the only carrier of meaning.
- Compute process exit code independently from report severity and only after the report is printed.

