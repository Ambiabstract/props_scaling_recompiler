# Scale and model contracts

## Generated identity

- The only derived-model suffix is `_scaled_XXX`. Derive `XXX` from final linear compile scale using decimal `ROUND_HALF_UP` to hundredths and exact integer-percent conversion. Pad to minimum width three (`1 -> 001`, `50 -> 050`) without truncating longer values (`1000`, `5500`).
- Do not interpret an existing `_static` suffix. `foo_static.mdl` at scale 1.5 becomes `foo_static_scaled_150.mdl`.
- Use the original model directly only when it is already a static prop, compile scale is `1.0`, and color is `255 255 255`, or when the approved empty-bodygroup dynamic fallback applies.
- Otherwise generate a derivative, including ordinary dynamic models at scale `1.0` and tinted static models at scale `1.0`; these use `_scaled_100`.
- Never accept `models/psr_scaled/**` as an original source asset. Diagnose the recursive request instead.

## Hammer-compatible scale

- Visible Hammer++ behavior is the source of truth: `blablabla -> 1.0`, `1,0 -> 1.0`, and `3,0 -> 3.0`.
- Parse a valid unsigned decimal prefix. An unusable or non-positive result becomes `1.0`. Apply the `0.01` lower clamp and then decimal `ROUND_HALF_UP` to hundredths.
- Preserve raw `modelscale` only for provenance and diagnostics. Store final PSR compile scale for identity. Do not persist Hammer-compatible effective scale in the production model or cache.
- Different raw strings may collapse to one artifact. `1.095`, `1.1`, and `1.104` map to compile scale `1.10` and `_scaled_110`; `1.105` maps to `_scaled_111`.
- Treat the below-`0.01` clamp as an intentional warning-worthy divergence from Hammer-visible scale. Extend unresearched input forms through regression observations rather than guesses.

## Geometry scale

- Generated identity and suffix always use linear compile scale.
- For an original MDL with exactly one bone and no static-prop flag, QC geometry scale is `compile_scale²`.
- For a static model or a multi-bone model, QC geometry scale is linear `compile_scale`.
- After calculating geometry scale, apply the `0.01` lower product clamp and emit an explicit diagnostic.
- `prop_data`, PHY presence, and entity classname do not select the quadratic mode.

## Source skin normalization

- Preserve raw `skin` in the request. A non-decimal value is a malformed request.
- Normalize a parsed integer outside the current source skin-family range to effective source skin `0`. Use `0` for downstream identities, tinting, and VMF assignment, and issue a warning.
- Re-read raw skin from source VMF on every run. If the original MDL skin table changes and a previously invalid index becomes valid, invalidate and rebuild every scale variant of that model.

## Empty bodygroup fallback

- Never use a zero-triangle SMD as a production placeholder; runtime testing showed such an MDL can compile and still crash in `shaderapidx9.dll` while rendering.
- Detect the approved fallback from `studiohdr`: a dynamic source MDL has `nummodels > 1` and at least one option with `nummeshes == 0`.
- Do not send this model through Crowbar or StudioMDL and do not create managed MDL/VMT/skin-layout records for it.
- Preserve original `model`, `modelscale`, `rendercolor`, and `skin`; change classname to `prop_dynamic` and remove only PSR service keys such as legacy `convert_prop_to_static`.
- Full static conversion remains a future SMD-aware task and must not reintroduce a zero-triangle placeholder.

