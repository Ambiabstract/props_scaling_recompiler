"""Hammer-compatible modelscale resolution for the confirmed PSR 2.0 matrix."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import PurePosixPath


MINIMUM_COMPILE_SCALE = Decimal("0.01")
DEFAULT_COMPILE_SCALE = Decimal("1")
COMPILE_SCALE_QUANTUM = Decimal("0.01")

# The confirmed Hammer++ cases behave like an unsigned decimal-prefix parser:
# commas and other suffix bytes terminate the numeric prefix, while an absent
# or non-positive prefix falls back to 1. Signs/exponents remain regression
# candidates and are intentionally not included in this first contract.
_DECIMAL_PREFIX = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


@dataclass(frozen=True, slots=True)
class ScaleDiagnostic:
    """One non-fatal explanation of Hammer/PSR scale normalisation."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ScaleResolution:
    """Production scale state: provenance raw value and final compile scale."""

    raw_modelscale: str | None
    compile_scale: Decimal
    diagnostics: tuple[ScaleDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class GeometryScaleResolution:
    """Model-dependent QC geometry scale derived from compile identity."""

    geometry_scale: Decimal
    is_quadratic: bool
    diagnostics: tuple[ScaleDiagnostic, ...]


def resolve_compile_scale(raw_modelscale: str | None) -> ScaleResolution:
    """Resolve a raw Hammer value using the confirmed compatibility contract.

    Hammer-visible effective scale is deliberately not returned or stored. The
    only production result is the final PSR compile scale after the minimum
    clamp, alongside the untouched raw value for provenance and diagnostics.
    """
    diagnostics: list[ScaleDiagnostic] = []
    text = "" if raw_modelscale is None else raw_modelscale.strip()
    match = _DECIMAL_PREFIX.match(text)
    parsed: Decimal | None = None
    if match is not None:
        try:
            parsed = Decimal(match.group(0))
        except InvalidOperation:
            parsed = None
        suffix = text[match.end():]
        if suffix:
            diagnostics.append(ScaleDiagnostic(
                "hammer_scale_numeric_prefix",
                f"Hammer-compatible parsing ignores trailing text {suffix!r}",
            ))

    if parsed is None or not parsed.is_finite() or parsed <= 0:
        diagnostics.append(ScaleDiagnostic(
            "hammer_scale_fallback",
            f"raw modelscale {raw_modelscale!r} resolves to Hammer default 1.0",
        ))
        compile_scale = DEFAULT_COMPILE_SCALE
    else:
        compile_scale = parsed

    if compile_scale < MINIMUM_COMPILE_SCALE:
        diagnostics.append(ScaleDiagnostic(
            "psr_minimum_scale_clamp",
            f"scale {compile_scale} is compiled as {MINIMUM_COMPILE_SCALE}",
        ))
        compile_scale = MINIMUM_COMPILE_SCALE

    rounded = _round_compile_scale(compile_scale)
    if rounded != compile_scale:
        diagnostics.append(ScaleDiagnostic(
            "psr_scale_rounding",
            f"scale {compile_scale} is rounded half-up to {rounded}",
        ))
    compile_scale = rounded

    return ScaleResolution(raw_modelscale, compile_scale, tuple(diagnostics))


def resolve_geometry_scale(
    compile_scale: Decimal,
    *,
    bone_count: int,
    is_static_prop: bool,
) -> GeometryScaleResolution:
    """Resolve the actual QC scale required to match Hammer++ geometry.

    Source applies model scale twice to one-bone models compiled without the
    static-prop flag. Generated PSR models are static, so their QC must bake
    that quadratic viewport size explicitly while retaining the linear
    compile identity used by the managed filename.
    """
    canonical_scale_percent(compile_scale)
    if not isinstance(bone_count, int) or isinstance(bone_count, bool):
        raise TypeError("bone_count must be an integer")
    if bone_count < 0:
        raise ValueError(f"bone_count must be non-negative: {bone_count}")

    is_quadratic = bone_count == 1 and not is_static_prop
    if is_quadratic:
        with localcontext() as context:
            context.prec = _precision_for(compile_scale) * 2
            geometry_scale = compile_scale * compile_scale
    else:
        geometry_scale = compile_scale

    diagnostics: list[ScaleDiagnostic] = []
    if geometry_scale < MINIMUM_COMPILE_SCALE:
        diagnostics.append(ScaleDiagnostic(
            "psr_minimum_geometry_scale_clamp",
            f"geometry scale {geometry_scale} is compiled as {MINIMUM_COMPILE_SCALE}",
        ))
        geometry_scale = MINIMUM_COMPILE_SCALE
    return GeometryScaleResolution(
        geometry_scale,
        is_quadratic,
        tuple(diagnostics),
    )


def canonical_scale_percent(compile_scale: Decimal) -> int:
    """Return the exact integer-percent key for a canonical compile scale."""
    if not compile_scale.is_finite() or compile_scale < MINIMUM_COMPILE_SCALE:
        raise ValueError(f"invalid PSR compile scale: {compile_scale!r}")
    rounded = _round_compile_scale(compile_scale)
    if rounded != compile_scale:
        raise ValueError(f"compile scale is not canonical to hundredths: {compile_scale}")
    with localcontext() as context:
        context.prec = _precision_for(rounded) + 2
        return int(rounded * 100)


def format_scale_percent(compile_scale: Decimal) -> str:
    """Format a canonical scale percent with a minimum width of three."""
    return f"{canonical_scale_percent(compile_scale):03d}"


def scaled_model_path(logical_source_model: str, compile_scale: Decimal) -> str:
    """Build the deterministic managed model path for one source and scale."""
    logical_path = logical_source_model.replace("\\", "/").casefold()
    if any(part in {"", ".", ".."} for part in logical_path.split("/")):
        raise ValueError(f"unsafe logical source model path: {logical_source_model!r}")
    if not logical_path.startswith("models/") or not logical_path.endswith(".mdl"):
        raise ValueError(f"invalid logical source model path: {logical_source_model!r}")
    if logical_path.startswith("models/psr_scaled/"):
        raise ValueError("PSR managed output cannot be used as a source model")
    relative = PurePosixPath(logical_path.removeprefix("models/"))
    percent = format_scale_percent(compile_scale)
    filename = f"{relative.stem}_scaled_{percent}.mdl"
    return str(PurePosixPath("models/psr_scaled", relative.parent, filename))


def _round_compile_scale(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _precision_for(value)
        return value.quantize(COMPILE_SCALE_QUANTUM, rounding=ROUND_HALF_UP)


def _precision_for(value: Decimal) -> int:
    digits = len(value.as_tuple().digits)
    integer_digits = max(value.adjusted() + 1, 1)
    return max(28, digits + 2, integer_digits + 2)


__all__ = [
    "DEFAULT_COMPILE_SCALE",
    "COMPILE_SCALE_QUANTUM",
    "MINIMUM_COMPILE_SCALE",
    "GeometryScaleResolution",
    "ScaleDiagnostic",
    "ScaleResolution",
    "canonical_scale_percent",
    "format_scale_percent",
    "resolve_compile_scale",
    "resolve_geometry_scale",
    "scaled_model_path",
]
