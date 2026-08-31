# Material tinting and skin-layout contracts

## Identity and reuse

- Colored-material identity is the complete logical path of the original VMT plus canonical RGB.
- Different scale variants of one model reuse the same colored materials.
- Keep material identity, generated VMT bytes, skin-family mapping, and model compilation layout separate in cache and planning.

## Generation policy

- First evaluate a generated Patch that includes the original logical VMT and inserts or replaces `$color` or `$color2` as appropriate.
- Cover an existing color key, no color key, a source Patch, proxies, different shaders, and VMT sources inside VPKs.
- If Patch semantics are not reliable for the combination, generate a complete managed VMT copy.
- Place every PSR material only under `materials/models/psr_scaled/`, preserving the source material's logical-path structure.

## StudioMDL limits and stable layout

- The compile-safe limits for the target StudioMDL are 31 unique material names and 1024 skin-family rows.
- Do not include a new colored variation that would exceed either limit in layout or a VMT generation batch. Warn and assign the entity its effective source skin instead.
- Keep colored skin-family indices stable across runs and map order. Do not compact unused rows during an ordinary compile-run.
- Any explicit project-wide compaction must follow the cleanup requirements in [pipeline-cache-and-reporting.md](pipeline-cache-and-reporting.md).
