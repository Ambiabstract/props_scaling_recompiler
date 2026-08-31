# External validation protocol

Read this file only after the user explicitly requests a concrete SDK, Antenna, Hammer, BSP, or game test.

## Boundaries

- Treat the installed Source SDK 2013 SP and Antenna as external user environments.
- Work on isolated copies or dedicated fixtures. Do not modify source maps or original assets.
- Do not run force-cleanup or mass recompilation without separate explicit authorization.
- Priority read-only Antenna maps are `aa_models_color_tint_test_01a.vmf`, `aa_models_static_convert_test_01a.vmf`, and `psr_test_01a.vmf`. Their expected observations are in `docs/PROJECT_CONTEXT.md`.
- Integration maps include legacy and Hammer++ compatibility cases; do not treat all entities as ordinary happy-path input.

## Hammer or game launch

Before every automated launch:

1. Snapshot the exact existing Source registry values `ScreenWidth`, `ScreenHeight`, `ScreenWindowed`, and `ScreenNoBorder`.
2. Snapshot every CFG or video file the run can modify, preserving missing-file state as well as bytes and metadata needed for restoration.
3. Avoid `-w`, `-h`, `-windowed`, `-fullscreen`, and `-noborder` unless the test specifically requires them.

Place restoration in `finally` so normal exit, forced termination, timeout, and crash follow the same path. After restoration, read registry values and files again and compare them with the snapshot. A restoration attempt without this proof is not sufficient.

## Runtime evidence

- Use a fully compiled `VBSP -> VVIS -> VRAD` BSP.
- Observe for a sufficient window for the generated assets and relevant scenes to render.
- Sign-on, a short dump-free launch, or successful VBSP alone does not prove runtime safety.
- Record the exact map copy, tool versions, build stages, observation duration, process outcome, new dumps, and restoration proof.

The opt-in test entry points are documented in `tests/README.md` and the matching files under `docs/research/`. Use their existing temporary-copy protections; do not recreate a looser ad hoc flow.

