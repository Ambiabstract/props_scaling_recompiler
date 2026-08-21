"""Deterministic generation and validation of PSR-owned colored VMT files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import StringIO
from typing import Literal

from srctools.keyvalues import Keyvalues
from srctools.vmt import Material

from .searchpaths import normalize_logical_path
from .vmt import ColorParameter, MaterialBlockMetadata, SourceMaterialMetadata


ColorAssignment = Literal["insert", "replace"]
MaterialGenerationMode = Literal["patch", "full_copy"]


class MaterialGenerationError(ValueError):
    """A categorised failure to render or validate one generated VMT."""

    def __init__(self, code: str, logical_path: str, detail: str) -> None:
        self.code = code
        self.logical_path = logical_path
        self.detail = detail
        super().__init__(f"{code}: {logical_path}: {detail}")


@dataclass(frozen=True, slots=True)
class GeneratedMaterialContent:
    """Validated bytes for one deterministic managed colored material."""

    logical_source_material: str
    logical_output_material: str
    render_color: tuple[int, int, int]
    color_parameter: ColorParameter
    color_assignment: ColorAssignment
    generation_mode: MaterialGenerationMode
    source_fingerprint: str
    content: bytes
    sha256: str


def generate_colored_material(
    source: SourceMaterialMetadata,
    *,
    logical_output_material: str,
    render_color: tuple[int, int, int],
    color_parameter: ColorParameter,
    color_assignment: ColorAssignment,
    generation_mode: MaterialGenerationMode,
) -> GeneratedMaterialContent:
    """Render one colored VMT and prove the generated text is semantically valid."""
    output_path = _validate_output_path(logical_output_material)
    color = _validate_color(render_color, output_path)
    if color_parameter not in {"$color", "$color2"}:
        raise MaterialGenerationError(
            "material_color_parameter_invalid",
            output_path,
            repr(color_parameter),
        )
    if color_assignment not in {"insert", "replace"}:
        raise MaterialGenerationError(
            "material_color_assignment_invalid",
            output_path,
            repr(color_assignment),
        )

    color_value = "{" + " ".join(str(channel) for channel in color) + "}"
    if generation_mode == "patch":
        material = Material(
            "Patch",
            {"include": source.logical_material_path},
            [Keyvalues(color_assignment, [Keyvalues(color_parameter, color_value)])],
        )
    elif generation_mode == "full_copy":
        parameters = dict(source.parameters)
        parameters[color_parameter] = color_value
        material = Material(
            source.effective_shader,
            parameters,
            [_build_keyvalues(block) for block in source.blocks],
            [_build_keyvalues(proxy) for proxy in source.proxies],
        )
    else:
        raise MaterialGenerationError(
            "material_generation_mode_invalid",
            output_path,
            repr(generation_mode),
        )

    stream = StringIO()
    material.export(stream)
    content = stream.getvalue().encode("utf-8")
    _validate_rendered_material(
        content,
        logical_output_material=output_path,
        logical_source_material=source.logical_material_path,
        color_parameter=color_parameter,
        color_assignment=color_assignment,
        color_value=color_value,
        generation_mode=generation_mode,
        effective_shader=source.effective_shader,
    )
    return GeneratedMaterialContent(
        logical_source_material=source.logical_material_path,
        logical_output_material=output_path,
        render_color=color,
        color_parameter=color_parameter,
        color_assignment=color_assignment,
        generation_mode=generation_mode,
        source_fingerprint=source.dependency_fingerprint,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _validate_output_path(value: str) -> str:
    try:
        logical_path = normalize_logical_path(value)
    except ValueError as exc:
        raise MaterialGenerationError(
            "generated_material_path_invalid", value, str(exc)
        ) from exc
    if (
        not logical_path.startswith("materials/models/psr_scaled/")
        or not logical_path.endswith(".vmt")
    ):
        raise MaterialGenerationError(
            "generated_material_path_unmanaged",
            logical_path,
            "output must be under materials/models/psr_scaled/**/*.vmt",
        )
    return logical_path


def _validate_color(
    value: tuple[int, int, int],
    logical_path: str,
) -> tuple[int, int, int]:
    if (
        len(value) != 3
        or any(isinstance(channel, bool) or not isinstance(channel, int) for channel in value)
        or any(not 0 <= channel <= 255 for channel in value)
    ):
        raise MaterialGenerationError(
            "generated_material_color_invalid",
            logical_path,
            f"RGB channels must be integers within 0..255, got {value!r}",
        )
    return value


def _build_keyvalues(block: MaterialBlockMetadata) -> Keyvalues:
    if block.value is not None:
        if block.children:
            raise ValueError(f"material block {block.name!r} has value and children")
        return Keyvalues(block.name, block.value)
    return Keyvalues(block.name, [_build_keyvalues(child) for child in block.children])


def _validate_rendered_material(
    content: bytes,
    *,
    logical_output_material: str,
    logical_source_material: str,
    color_parameter: ColorParameter,
    color_assignment: ColorAssignment,
    color_value: str,
    generation_mode: MaterialGenerationMode,
    effective_shader: str,
) -> None:
    try:
        text = content.decode("utf-8")
        parsed = Material.parse(
            text.splitlines(keepends=True),
            logical_output_material,
        )
    except Exception as exc:
        raise MaterialGenerationError(
            "generated_material_invalid",
            logical_output_material,
            f"{type(exc).__name__}: {exc}",
        ) from exc

    if generation_mode == "patch":
        if parsed.shader.casefold() != "patch":
            raise MaterialGenerationError(
                "generated_material_shader_mismatch",
                logical_output_material,
                f"expected Patch, got {parsed.shader!r}",
            )
        if parsed.get("include", "") != logical_source_material:
            raise MaterialGenerationError(
                "generated_material_include_mismatch",
                logical_output_material,
                f"expected include {logical_source_material!r}",
            )
        blocks = [block for block in parsed.blocks if block.name == color_assignment]
        try:
            rendered_color = blocks[0][color_parameter] if len(blocks) == 1 else ""
        except LookupError:
            rendered_color = ""
        if rendered_color != color_value:
            raise MaterialGenerationError(
                "generated_material_color_mismatch",
                logical_output_material,
                f"missing {color_assignment}/{color_parameter}={color_value}",
            )
    else:
        if parsed.shader.casefold() != effective_shader.casefold():
            raise MaterialGenerationError(
                "generated_material_shader_mismatch",
                logical_output_material,
                f"expected {effective_shader!r}, got {parsed.shader!r}",
            )
        if parsed.get(color_parameter, "") != color_value:
            raise MaterialGenerationError(
                "generated_material_color_mismatch",
                logical_output_material,
                f"expected {color_parameter}={color_value}",
            )


__all__ = [
    "ColorAssignment",
    "GeneratedMaterialContent",
    "MaterialGenerationError",
    "MaterialGenerationMode",
    "generate_colored_material",
]
