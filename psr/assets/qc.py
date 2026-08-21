"""Token-aware, source-preserving inspection and transformation of QC scripts.

QC is not Valve KeyValues.  This module therefore keeps a deliberately small
lexer for the syntax PSR needs, validates strings/comments/braces, and applies
edits only to known token spans.  Unrelated source bytes are never reserialised.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Literal

from psr.domain import canonical_scale_percent


TokenKind = Literal["word", "string", "lbrace", "rbrace"]


class QCTransformError(ValueError):
    """A categorised structural or transformation failure."""

    def __init__(self, code: str, detail: str, offset: int | None = None) -> None:
        self.code = code
        self.detail = detail
        self.offset = offset
        location = "" if offset is None else f" at byte {offset}"
        super().__init__(f"{code}{location}: {detail}")


@dataclass(frozen=True, slots=True)
class SourceQCMetadata:
    """Normalised facts required by deterministic QC planning."""

    source_sha256: str
    model_name: str
    scale: str | None
    is_static_prop: bool
    skin_families: tuple[tuple[str, ...], ...] | None
    lod_distances: tuple[str, ...]
    command_names: tuple[str, ...]
    newline: bytes


@dataclass(frozen=True, slots=True)
class QCTransformResult:
    """In-memory generated QC plus reproducible content identity."""

    data: bytes
    source_sha256: str
    output_sha256: str
    mutations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: TokenKind
    start: int
    end: int
    raw: bytes
    depth: int


@dataclass(frozen=True, slots=True)
class _Command:
    name: str
    token_index: int
    argument_indexes: tuple[int, ...]
    block_open_index: int | None
    block_close_index: int | None
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Document:
    source: bytes
    tokens: tuple[_Token, ...]
    commands: tuple[_Command, ...]
    brace_pairs: dict[int, int]


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    replacement: bytes


def inspect_qc(source: bytes) -> SourceQCMetadata:
    """Validate and inspect the top-level QC commands PSR depends on."""
    document = _parse_document(source)
    model_commands = _commands_named(document, "$modelname")
    if not model_commands:
        raise QCTransformError("modelname_missing", "QC has no top-level $modelname")
    if len(model_commands) != 1:
        raise QCTransformError(
            "duplicate_modelname",
            f"QC has {len(model_commands)} top-level $modelname commands",
            model_commands[1].start,
        )
    model_name = _required_argument(document, model_commands[0], 0, "modelname_value")

    scale_commands = _commands_named(document, "$scale")
    if len(scale_commands) > 1:
        raise QCTransformError(
            "duplicate_scale",
            f"QC has {len(scale_commands)} top-level $scale commands",
            scale_commands[1].start,
        )
    scale = (
        _required_argument(document, scale_commands[0], 0, "scale_value")
        if scale_commands
        else None
    )

    static_commands = _commands_named(document, "$staticprop")
    if len(static_commands) > 1:
        raise QCTransformError(
            "duplicate_staticprop",
            f"QC has {len(static_commands)} top-level $staticprop commands",
            static_commands[1].start,
        )

    target_groups = tuple(
        command
        for command in _commands_named(document, "$texturegroup")
        if _optional_argument(document, command, 0).casefold() == "skinfamilies"
    )
    if len(target_groups) > 1:
        raise QCTransformError(
            "duplicate_skinfamilies",
            f"QC has {len(target_groups)} top-level skinfamilies texture groups",
            target_groups[1].start,
        )
    skin_families = (
        _parse_skinfamilies(document, target_groups[0])
        if target_groups
        else None
    )

    lod_commands = _commands_named(document, "$lod")
    lod_distances = tuple(
        _required_argument(document, command, 0, "lod_distance")
        for command in lod_commands
    )
    newline = b"\r\n" if b"\r\n" in source else b"\n"
    return SourceQCMetadata(
        source_sha256=_sha256(source),
        model_name=model_name,
        scale=scale,
        is_static_prop=bool(static_commands),
        skin_families=skin_families,
        lod_distances=lod_distances,
        command_names=tuple(command.name for command in document.commands),
        newline=newline,
    )


def build_reference_qc(
    source: bytes,
    *,
    expected_source_families: tuple[tuple[str, ...], ...],
    target_families: tuple[tuple[str, ...], ...],
    require_staticprop: bool,
) -> QCTransformResult:
    """Create the shared reference QC without touching the input bytes.

    The input skin table must describe the current MDL before it is replaced.
    This prevents a stale decompile or cache entry from silently shifting skin
    indexes.  A single-family MDL may legitimately omit ``$texturegroup``.
    """
    source_sha256 = _sha256(source)
    expected = _normalise_families(expected_source_families, "expected_source_families")
    target = _normalise_families(target_families, "target_families")
    if target[:len(expected)] != expected or len(target) < len(expected):
        raise QCTransformError(
            "target_skinfamilies_invalid",
            "target skin table must retain every source family as an unchanged prefix",
        )

    metadata = inspect_qc(source)
    existing = (
        None
        if metadata.skin_families is None
        else _normalise_families(metadata.skin_families, "source_qc_families")
    )
    if existing is None:
        if len(expected) != 1:
            raise QCTransformError(
                "source_skinfamilies_missing",
                "QC omits $texturegroup but the source MDL has multiple skin families",
            )
    elif existing != expected:
        raise QCTransformError(
            "source_skinfamilies_mismatch",
            f"QC families {existing!r} do not match source MDL families {expected!r}",
        )

    data = source
    mutations: list[str] = []
    if require_staticprop and not metadata.is_static_prop:
        document = _parse_document(data)
        anchor = _commands_named(document, "$modelname")[0]
        insertion_at, prefix = _line_insertion_after(data, anchor.end, metadata.newline)
        data = _apply_edits(data, [
            _Edit(
                insertion_at,
                insertion_at,
                prefix + _indent_at(data, anchor.start) + b"$staticprop" + metadata.newline,
            ),
        ])
        mutations.append("insert_staticprop")

    current = inspect_qc(data)
    current_families = (
        None
        if current.skin_families is None
        else _normalise_families(current.skin_families, "source_qc_families")
    )
    if current_families != target and not (
        current_families is None and len(target) == 1
    ):
        document = _parse_document(data)
        group = _find_skinfamilies_command(document)
        if group is not None:
            indent = _indent_at(data, group.start)
            replacement = _render_skinfamilies(target, indent, current.newline)
            data = _apply_edits(data, [_Edit(group.start, group.end, replacement)])
            mutations.append("replace_skinfamilies")
        else:
            anchors = _commands_named(document, "$cdmaterials")
            if not anchors:
                anchors = _commands_named(document, "$staticprop")
            if not anchors:
                anchors = _commands_named(document, "$modelname")
            anchor = anchors[-1]
            insertion_at, prefix = _line_insertion_after(data, anchor.end, current.newline)
            rendered = _render_skinfamilies(
                target,
                _indent_at(data, anchor.start),
                current.newline,
            ) + current.newline
            data = _apply_edits(
                data,
                [_Edit(insertion_at, insertion_at, prefix + rendered)],
            )
            mutations.append("insert_skinfamilies")

    # Reparse the exact result so generated documents never leave this function
    # structurally invalid.
    final = inspect_qc(data)
    final_families = (
        None
        if final.skin_families is None
        else _normalise_families(final.skin_families, "generated_families")
    )
    if len(target) > 1 and final_families != target:
        raise QCTransformError(
            "generated_skinfamilies_mismatch",
            "generated reference QC does not contain the requested complete skin table",
        )
    return QCTransformResult(data, source_sha256, _sha256(data), tuple(mutations))


def build_scaled_qc(
    reference_source: bytes,
    *,
    logical_output_model: str,
    compile_scale: Decimal,
) -> QCTransformResult:
    """Create one compile-ready scaled QC from the shared reference QC."""
    source_sha256 = _sha256(reference_source)
    output_model = _validate_output_model(logical_output_model)
    try:
        canonical_scale_percent(compile_scale)
    except (ValueError, InvalidOperation) as exc:
        raise QCTransformError(
            "noncanonical_compile_scale",
            f"compile scale must be a positive canonical hundredth: {compile_scale!r}",
        ) from exc
    scale_text = format(compile_scale, ".2f")

    metadata = inspect_qc(reference_source)
    if not metadata.is_static_prop:
        raise QCTransformError(
            "scaled_qc_not_static",
            "reference QC must contain a top-level $staticprop",
        )

    data = reference_source
    mutations: list[str] = []
    target_modelname = output_model.removeprefix("models/")
    if _normalise_modelname(metadata.model_name) != target_modelname:
        document = _parse_document(data)
        command = _commands_named(document, "$modelname")[0]
        argument = _argument_token(document, command, 0, "modelname_value")
        data = _apply_edits(data, [
            _Edit(argument.start, argument.end, _quote_ascii(target_modelname)),
        ])
        mutations.append("replace_modelname")

    metadata = inspect_qc(data)
    if metadata.scale is None:
        document = _parse_document(data)
        anchor = _commands_named(document, "$modelname")[0]
        insertion_at, prefix = _line_insertion_after(data, anchor.end, metadata.newline)
        data = _apply_edits(data, [
            _Edit(
                insertion_at,
                insertion_at,
                prefix + _indent_at(data, anchor.start)
                + b"$scale " + scale_text.encode("ascii") + metadata.newline,
            ),
        ])
        mutations.append("insert_scale")
    elif metadata.scale != scale_text:
        document = _parse_document(data)
        command = _commands_named(document, "$scale")[0]
        argument = _argument_token(document, command, 0, "scale_value")
        data = _apply_edits(data, [
            _Edit(argument.start, argument.end, scale_text.encode("ascii")),
        ])
        mutations.append("replace_scale")

    document = _parse_document(data)
    lod_edits: list[_Edit] = []
    for command in _commands_named(document, "$lod"):
        argument = _argument_token(document, command, 0, "lod_distance")
        raw_distance = _token_value(argument)
        try:
            distance = Decimal(raw_distance)
        except InvalidOperation as exc:
            raise QCTransformError(
                "invalid_lod_distance",
                f"$lod distance is not decimal: {raw_distance!r}",
                argument.start,
            ) from exc
        if not distance.is_finite():
            raise QCTransformError(
                "invalid_lod_distance",
                f"$lod distance is not finite: {raw_distance!r}",
                argument.start,
            )
        replacement = _format_decimal(distance * compile_scale).encode("ascii")
        if replacement != argument.raw:
            lod_edits.append(_Edit(argument.start, argument.end, replacement))
    if lod_edits:
        data = _apply_edits(data, lod_edits)
        mutations.append("scale_lod_distances")

    final = inspect_qc(data)
    if _normalise_modelname(final.model_name) != target_modelname:
        raise QCTransformError("generated_modelname_mismatch", target_modelname)
    if final.scale != scale_text:
        raise QCTransformError("generated_scale_mismatch", scale_text)
    return QCTransformResult(data, source_sha256, _sha256(data), tuple(mutations))


def _parse_document(source: bytes) -> _Document:
    raw_tokens = _lex(source)
    tokens: list[_Token] = []
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for kind, start, end, raw in raw_tokens:
        depth = len(stack)
        token_index = len(tokens)
        token = _Token(kind, start, end, raw, depth)
        tokens.append(token)
        if kind == "lbrace":
            stack.append(token_index)
        elif kind == "rbrace":
            if not stack:
                raise QCTransformError("unexpected_closing_brace", "unmatched '}'", start)
            opening = stack.pop()
            pairs[opening] = token_index
            pairs[token_index] = opening
    if stack:
        opening = tokens[stack[-1]]
        raise QCTransformError("unclosed_brace", "unmatched '{'", opening.start)

    top_commands = [
        index
        for index, token in enumerate(tokens)
        if token.depth == 0
        and token.kind == "word"
        and token.raw.startswith(b"$")
    ]
    commands: list[_Command] = []
    for position, token_index in enumerate(top_commands):
        next_index = (
            top_commands[position + 1]
            if position + 1 < len(top_commands)
            else len(tokens)
        )
        token = tokens[token_index]
        block_open: int | None = None
        for index in range(token_index + 1, next_index):
            candidate = tokens[index]
            if candidate.kind == "lbrace" and candidate.depth == 0:
                block_open = index
                break
        block_close = pairs[block_open] if block_open is not None else None
        args_end = block_open if block_open is not None else next_index
        arguments = tuple(
            index
            for index in range(token_index + 1, args_end)
            if tokens[index].depth == 0
            and tokens[index].kind in {"word", "string"}
        )
        if block_close is not None:
            command_end = tokens[block_close].end
        elif arguments:
            command_end = tokens[arguments[-1]].end
        else:
            command_end = token.end
        commands.append(_Command(
            name=token.raw.decode("ascii").casefold(),
            token_index=token_index,
            argument_indexes=arguments,
            block_open_index=block_open,
            block_close_index=block_close,
            start=token.start,
            end=command_end,
        ))
    return _Document(source, tuple(tokens), tuple(commands), pairs)


def _lex(source: bytes) -> list[tuple[TokenKind, int, int, bytes]]:
    tokens: list[tuple[TokenKind, int, int, bytes]] = []
    index = 0
    size = len(source)
    whitespace = b" \t\r\n\v\f"
    while index < size:
        byte = source[index]
        if byte in whitespace:
            index += 1
            continue
        if source.startswith(b"//", index):
            newline = source.find(b"\n", index + 2)
            index = size if newline == -1 else newline + 1
            continue
        if source.startswith(b"/*", index):
            close = source.find(b"*/", index + 2)
            if close == -1:
                raise QCTransformError(
                    "unterminated_block_comment",
                    "block comment has no closing */",
                    index,
                )
            index = close + 2
            continue
        if byte == ord('"'):
            start = index
            index += 1
            while index < size:
                if source[index] == ord('"'):
                    index += 1
                    tokens.append(("string", start, index, source[start:index]))
                    break
                index += 1
            else:
                raise QCTransformError(
                    "unterminated_string",
                    "quoted string has no closing quote",
                    start,
                )
            continue
        if byte == ord("{"):
            tokens.append(("lbrace", index, index + 1, b"{"))
            index += 1
            continue
        if byte == ord("}"):
            tokens.append(("rbrace", index, index + 1, b"}"))
            index += 1
            continue
        start = index
        while index < size:
            if source[index] in whitespace or source[index] in b'{}"':
                break
            if source.startswith(b"//", index) or source.startswith(b"/*", index):
                break
            index += 1
        if index == start:
            index += 1
        else:
            tokens.append(("word", start, index, source[start:index]))
    return tokens


def _commands_named(document: _Document, name: str) -> tuple[_Command, ...]:
    folded = name.casefold()
    return tuple(command for command in document.commands if command.name == folded)


def _find_skinfamilies_command(document: _Document) -> _Command | None:
    for command in _commands_named(document, "$texturegroup"):
        if _optional_argument(document, command, 0).casefold() == "skinfamilies":
            return command
    return None


def _argument_token(
    document: _Document,
    command: _Command,
    index: int,
    code: str,
) -> _Token:
    if index >= len(command.argument_indexes):
        raise QCTransformError(
            code,
            f"{command.name} is missing argument {index + 1}",
            command.start,
        )
    return document.tokens[command.argument_indexes[index]]


def _required_argument(
    document: _Document,
    command: _Command,
    index: int,
    code: str,
) -> str:
    return _token_value(_argument_token(document, command, index, code))


def _optional_argument(document: _Document, command: _Command, index: int) -> str:
    if index >= len(command.argument_indexes):
        return ""
    return _token_value(document.tokens[command.argument_indexes[index]])


def _token_value(token: _Token) -> str:
    raw = token.raw
    if token.kind != "string":
        return raw.decode("cp1252")
    # Valve's QC lexer treats a backslash as an ordinary path separator, even
    # immediately before the closing quote. Crowbar consequently emits common
    # forms such as $cdmaterials "models\props\".
    return raw[1:-1].decode("cp1252")


def _parse_skinfamilies(
    document: _Document,
    command: _Command,
) -> tuple[tuple[str, ...], ...]:
    if command.block_open_index is None or command.block_close_index is None:
        raise QCTransformError(
            "skinfamilies_block_missing",
            "$texturegroup skinfamilies must have a brace block",
            command.start,
        )
    outer_depth = document.tokens[command.block_open_index].depth
    families: list[tuple[str, ...]] = []
    index = command.block_open_index + 1
    while index < command.block_close_index:
        token = document.tokens[index]
        if token.kind == "lbrace" and token.depth == outer_depth + 1:
            close = document.brace_pairs[index]
            values = tuple(
                _token_value(document.tokens[item])
                for item in range(index + 1, close)
                if document.tokens[item].depth == outer_depth + 2
                and document.tokens[item].kind in {"word", "string"}
            )
            if not values:
                raise QCTransformError(
                    "empty_skinfamily",
                    "skinfamilies contains an empty family row",
                    token.start,
                )
            families.append(values)
            index = close + 1
            continue
        if token.kind in {"word", "string"} and token.depth == outer_depth + 1:
            raise QCTransformError(
                "invalid_skinfamilies_token",
                "material tokens must be inside a family row",
                token.start,
            )
        index += 1
    if not families:
        raise QCTransformError(
            "empty_skinfamilies",
            "skinfamilies has no family rows",
            command.start,
        )
    width = len(families[0])
    if any(len(family) != width for family in families):
        raise QCTransformError(
            "inconsistent_skinfamily_width",
            "all skin family rows must contain the same material count",
            command.start,
        )
    return tuple(families)


def _normalise_families(
    families: tuple[tuple[str, ...], ...],
    label: str,
) -> tuple[tuple[str, ...], ...]:
    if not families:
        raise QCTransformError("empty_skinfamilies", f"{label} has no rows")
    result = tuple(tuple(_normalise_material(item) for item in row) for row in families)
    if any(not row or any(not item for item in row) for row in result):
        raise QCTransformError("empty_skinfamily", f"{label} contains an empty value")
    width = len(result[0])
    if any(len(row) != width for row in result):
        raise QCTransformError(
            "inconsistent_skinfamily_width",
            f"{label} rows do not have one stable material count",
        )
    return result


def _normalise_material(value: str) -> str:
    item = value.strip().replace("\\", "/").lstrip("/").casefold()
    if item.startswith("materials/"):
        item = item.removeprefix("materials/")
    if item.endswith(".vmt"):
        item = item[:-4]
    return item


def _normalise_modelname(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("/").casefold()


def _validate_output_model(value: str) -> str:
    normalised = _normalise_modelname(value)
    parts = normalised.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise QCTransformError("unsafe_output_model", repr(value))
    if not normalised.startswith("models/psr_scaled/") or not normalised.endswith(".mdl"):
        raise QCTransformError(
            "managed_output_model",
            "generated QC model must be under models/psr_scaled/**/*.mdl",
        )
    # PurePosixPath makes the intended path semantics explicit and catches
    # accidental platform-dependent changes during future refactors.
    return str(PurePosixPath(normalised))


def _render_skinfamilies(
    families: tuple[tuple[str, ...], ...],
    indent: bytes,
    newline: bytes,
) -> bytes:
    child = indent + b"\t"
    lines = [
        indent + b'$texturegroup "skinfamilies"',
        indent + b"{",
    ]
    for family in families:
        values = b" ".join(_quote_ascii(item) for item in family)
        lines.append(child + b"{ " + values + b" }")
    lines.append(indent + b"}")
    return newline.join(lines)


def _quote_ascii(value: str) -> bytes:
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise QCTransformError(
            "non_ascii_generated_value",
            f"generated QC value is not ASCII: {value!r}",
        ) from exc
    if b'"' in raw:
        raise QCTransformError(
            "unsupported_quote_in_generated_value",
            f"QC quoted values cannot contain a literal quote: {value!r}",
        )
    return b'"' + raw + b'"'


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _indent_at(source: bytes, offset: int) -> bytes:
    line_start = source.rfind(b"\n", 0, offset) + 1
    cursor = line_start
    while cursor < offset and source[cursor] in b" \t":
        cursor += 1
    return source[line_start:cursor]


def _line_insertion_after(source: bytes, offset: int, newline: bytes) -> tuple[int, bytes]:
    line_feed = source.find(b"\n", offset)
    if line_feed != -1:
        return line_feed + 1, b""
    prefix = b"" if not source or source.endswith((b"\n", b"\r")) else newline
    return len(source), prefix


def _apply_edits(source: bytes, edits: list[_Edit]) -> bytes:
    ordered = sorted(edits, key=lambda item: (item.start, item.end))
    previous_end = 0
    chunks: list[bytes] = []
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= len(source):
            raise QCTransformError("invalid_edit_span", repr(edit))
        if edit.start < previous_end:
            raise QCTransformError("overlapping_edits", repr(edit))
        chunks.append(source[previous_end:edit.start])
        chunks.append(edit.replacement)
        previous_end = edit.end
    chunks.append(source[previous_end:])
    return b"".join(chunks)


def _sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


__all__ = [
    "QCTransformError",
    "QCTransformResult",
    "SourceQCMetadata",
    "build_reference_qc",
    "build_scaled_qc",
    "inspect_qc",
]
