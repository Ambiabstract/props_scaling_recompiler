# VMF, KeyValues, and asset resolution contracts

## Structured Valve KeyValues

- Treat VMF, GameInfo, and VMT as structured Valve KeyValues, not JSON or independent lines.
- Never edit VMF entity or brace-delimited blocks with regular expressions.
- Preserve order, repeated keys and blocks, comments, newline style, encoding, and untouched bytes as far as possible.
- Validate input before editing. Parse and validate output afterward, including the identity and count of affected entities.

## VMF entity output

- Process direct properties of active entities only. Do not process hidden entities.
- When no active PSR entity exists, still produce `vmf_out` equivalent to the input; the established no-op behavior is byte preservation.
- For final `prop_static`, assign final mapped `skin` and remove PSR-only/runtime-inapplicable keys including raw `modelscale`, `rendercolor`, and legacy `convert_prop_to_static`.
- For the approved dynamic fallback, preserve original `model`, `modelscale`, `rendercolor`, and `skin`; remove only `convert_prop_to_static` and future PSR service keys that are not dynamic runtime properties.
- Never write a generated-model reference whose compiled artifacts have not been validated.

## GameInfo and SearchPaths

- Resolve SearchPaths strictly in GameInfo order. The first existing exact logical path wins; stop after successful resolution.
- Support `|gameinfo_path|`, `|all_source_engine_paths|`, `.`, `*`, explicit VPK entries, and relative engine paths.
- Keep the logical Hammer path distinct from the physical folder or VPK provenance.
- Search VPKs by the complete normalized logical path, never by basename or a substring of a textual tree.

