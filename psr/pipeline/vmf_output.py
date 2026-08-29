"""Source-preserving transformation of validated PSR entity assignments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from psr.keyvalues import Block, Property, parse_vmf

from .planning import MapUsagePlan, OperationPlan
from .skin_layout import EntitySkinAssignment, SkinLayoutOperationPlan


_STATIC_PSR_ONLY_KEYS = {
    b"modelscale",
    b"rendercolor",
    b"convert_prop_to_static",
}
_DYNAMIC_PSR_ONLY_KEYS = {b"convert_prop_to_static"}


class VmfOutputError(RuntimeError):
    """A categorised refusal to produce an unsafe or stale VMF output."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class VmfOutput:
    """Structurally validated VMF bytes and their deterministic identity."""

    map_identity: str
    content: bytes
    sha256: str
    transformed_entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VmfFallbackAssignment:
    """One failed request retained as a runtime-scaled dynamic override."""

    entity_id: str


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    replacement: bytes


def build_vmf_output(
    source: bytes,
    operation: OperationPlan,
    skin_layout: SkinLayoutOperationPlan,
    *,
    fallbacks: tuple[VmfFallbackAssignment, ...] = (),
    value_encoding: str = "cp1252",
) -> VmfOutput:
    """Apply final model/skin assignments without reserialising unrelated VMF.

    The input hash and every original entity span are checked before edits.
    The result is parsed again and every transformed direct property is
    verified before bytes may proceed to the commit stage.
    """
    if not operation.is_valid or not skin_layout.is_valid:
        raise VmfOutputError(
            "vmf_output_plan_invalid",
            "operation and skin-layout plans must both be valid",
        )
    if operation.map_identity != skin_layout.map_identity:
        raise VmfOutputError(
            "vmf_output_map_identity_mismatch",
            "operation and skin-layout plans belong to different maps",
        )
    source_hash = hashlib.sha256(source).hexdigest()
    if source_hash != operation.vmf_sha256:
        raise VmfOutputError(
            "vmf_input_changed",
            f"input SHA-256 is {source_hash}, planned {operation.vmf_sha256}",
        )

    assignment_by_id = {item.entity_id: item for item in skin_layout.assignments}
    usage_by_id = {item.request.entity_id: item for item in operation.usages}
    fallback_ids = {item.entity_id for item in fallbacks}
    if len(fallback_ids) != len(fallbacks):
        raise VmfOutputError(
            "vmf_fallback_duplicate",
            "fallback ledger contains duplicate entity IDs",
        )
    if fallback_ids & set(usage_by_id):
        raise VmfOutputError(
            "vmf_fallback_assignment_overlap",
            "an entity cannot have both a static assignment and runtime fallback",
        )
    if len(assignment_by_id) != len(skin_layout.assignments):
        raise VmfOutputError(
            "vmf_assignment_duplicate",
            "skin-layout plan contains duplicate entity assignments",
        )
    if set(assignment_by_id) != set(usage_by_id):
        raise VmfOutputError(
            "vmf_assignment_mismatch",
            "final skin assignments do not exactly cover operation usages",
        )

    document = parse_vmf(source)
    blocks_by_id: dict[str, Block] = {}
    for block in document.blocks:
        if block.name.lower() != b"entity":
            continue
        values = block.direct_values(b"id")
        if len(values) != 1:
            continue
        try:
            entity_id = values[0].decode(value_encoding)
        except UnicodeError:
            continue
        if entity_id in usage_by_id or entity_id in fallback_ids:
            if entity_id in blocks_by_id:
                raise VmfOutputError(
                    "vmf_target_duplicate",
                    f"entity ID {entity_id!r} is not unique in the current VMF",
                )
            blocks_by_id[entity_id] = block

    target_ids = set(usage_by_id) | fallback_ids
    if set(blocks_by_id) != target_ids:
        missing = sorted(target_ids - set(blocks_by_id), key=int)
        raise VmfOutputError(
            "vmf_target_missing",
            f"planned entity IDs are absent from the current VMF: {missing!r}",
        )

    edits: list[_Edit] = []
    preserved_dynamic: dict[str, dict[bytes, tuple[bytes, ...]]] = {}
    for entity_id in sorted(target_ids, key=int):
        if entity_id in fallback_ids:
            block = blocks_by_id[entity_id]
            properties: dict[bytes, list[Property]] = {}
            for prop in block.properties:
                properties.setdefault(prop.key.lower(), []).append(prop)
            classname = _one_property(properties, b"classname", entity_id)
            preserved_dynamic[entity_id] = {
                key: block.direct_values(key)
                for key in (b"model", b"modelscale", b"rendercolor", b"skin")
            }
            edits.append(_replace_value(
                classname,
                "prop_dynamic_override",
                value_encoding,
            ))
            for key in _DYNAMIC_PSR_ONLY_KEYS:
                for prop in properties.get(key, []):
                    start, end = _property_removal_span(source, prop)
                    edits.append(_Edit(start, end, b""))
            continue
        usage = usage_by_id[entity_id]
        assignment = assignment_by_id[entity_id]
        block = blocks_by_id[entity_id]
        request = usage.request
        if (block.start, block.end) != (request.source_start, request.source_end):
            raise VmfOutputError(
                "vmf_target_span_changed",
                f"entity {entity_id}: parser span differs from discovery",
            )
        if (
            assignment.logical_source_model != request.logical_model_path
            or assignment.logical_output_model != usage.logical_output_model
            or assignment.source_skin != usage.source_skin
            or assignment.render_color != usage.render_color
        ):
            raise VmfOutputError(
                "vmf_assignment_identity_mismatch",
                f"entity {entity_id}: assignment differs from operation usage",
            )

        properties: dict[bytes, list[Property]] = {}
        for prop in block.properties:
            properties.setdefault(prop.key.lower(), []).append(prop)
        classname = _one_property(properties, b"classname", entity_id)
        model = _one_property(properties, b"model", entity_id)
        skin_values = properties.get(b"skin", [])
        if len(skin_values) > 1:
            raise VmfOutputError(
                "vmf_target_property_duplicate",
                f"entity {entity_id}: duplicate direct 'skin' properties",
            )

        edits.append(_replace_value(classname, usage.output_classname, value_encoding))
        if usage.operation == "reuse_dynamic":
            preserved_dynamic[entity_id] = {
                key: block.direct_values(key)
                for key in (b"model", b"modelscale", b"rendercolor", b"skin")
            }
        else:
            edits.append(_replace_value(
                model,
                assignment.logical_output_model,
                value_encoding,
            ))
            if skin_values:
                edits.append(_replace_value(
                    skin_values[0],
                    str(assignment.target_skin),
                    value_encoding,
                ))
            else:
                edits.append(_insert_skin(source, block, assignment.target_skin))

        for key in _removed_keys(usage):
            for prop in properties.get(key, []):
                start, end = _property_removal_span(source, prop)
                edits.append(_Edit(start, end, b""))

    content = _apply_edits(source, edits)
    result = parse_vmf(content)
    _validate_result(
        result.blocks,
        usage_by_id,
        assignment_by_id,
        preserved_dynamic,
        fallback_ids,
        value_encoding=value_encoding,
    )
    ids = tuple(sorted(target_ids, key=int))
    return VmfOutput(
        map_identity=operation.map_identity,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        transformed_entity_ids=ids,
    )


def _one_property(
    properties: dict[bytes, list[Property]],
    key: bytes,
    entity_id: str,
) -> Property:
    values = properties.get(key, [])
    if len(values) != 1:
        raise VmfOutputError(
            "vmf_target_property_count",
            f"entity {entity_id}: expected one direct {key.decode('ascii')!r}, "
            f"found {len(values)}",
        )
    return values[0]


def _replace_value(prop: Property, value: str, encoding: str) -> _Edit:
    return _Edit(
        prop.value_token.start,
        prop.value_token.end,
        _quote(value, encoding),
    )


def _quote(value: str, encoding: str) -> bytes:
    try:
        encoded = value.encode(encoding)
    except UnicodeError as exc:
        raise VmfOutputError(
            "vmf_output_encoding",
            f"value {value!r} is not representable as {encoding}",
        ) from exc
    if any(byte < 0x20 for byte in encoded):
        raise VmfOutputError(
            "vmf_output_control_character",
            f"value {value!r} contains a control character",
        )
    return b'"' + encoded.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'


def _insert_skin(source: bytes, block: Block, target_skin: int) -> _Edit:
    newline = _newline_style(source)
    line_start = max(
        source.rfind(b"\n", 0, block.close_token.start),
        source.rfind(b"\r", 0, block.close_token.start),
    ) + 1
    before_close = source[line_start:block.close_token.start]
    direct_indent = _property_indent(source, block.properties[0]) if block.properties else b"\t"
    if before_close.strip():
        insertion_at = block.close_token.start
        content = newline + direct_indent + b'"skin" ' + _quote(str(target_skin), "ascii") + newline
    else:
        insertion_at = line_start
        content = direct_indent + b'"skin" ' + _quote(str(target_skin), "ascii") + newline
    return _Edit(insertion_at, insertion_at, content)


def _property_indent(source: bytes, prop: Property) -> bytes:
    line_start = max(
        source.rfind(b"\n", 0, prop.key_token.start),
        source.rfind(b"\r", 0, prop.key_token.start),
    ) + 1
    indent = source[line_start:prop.key_token.start]
    return indent if not indent.strip() else b"\t"


def _newline_style(source: bytes) -> bytes:
    if b"\r\n" in source:
        return b"\r\n"
    if b"\n" in source:
        return b"\n"
    if b"\r" in source:
        return b"\r"
    return b"\n"


def _property_removal_span(source: bytes, prop: Property) -> tuple[int, int]:
    """Remove the whole clean property line, or only its tokens near comments."""
    line_start = max(
        source.rfind(b"\n", 0, prop.key_token.start),
        source.rfind(b"\r", 0, prop.key_token.start),
    ) + 1
    newline_at = source.find(b"\n", prop.value_token.end)
    carriage_at = source.find(b"\r", prop.value_token.end)
    candidates = [item for item in (newline_at, carriage_at) if item >= 0]
    line_end = min(candidates) if candidates else len(source)
    if not source[line_start:prop.key_token.start].strip() and not source[
        prop.value_token.end:line_end
    ].strip():
        if source[line_end:line_end + 2] == b"\r\n":
            return line_start, line_end + 2
        if line_end < len(source):
            return line_start, line_end + 1
        return line_start, line_end
    return prop.key_token.start, prop.value_token.end


def _apply_edits(source: bytes, edits: list[_Edit]) -> bytes:
    ordered = sorted(edits, key=lambda item: (item.start, item.end))
    previous_end = 0
    parts: list[bytes] = []
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= len(source):
            raise VmfOutputError("vmf_edit_span_invalid", repr(edit))
        if edit.start < previous_end:
            raise VmfOutputError("vmf_edit_overlap", repr(edit))
        parts.append(source[previous_end:edit.start])
        parts.append(edit.replacement)
        previous_end = edit.end
    parts.append(source[previous_end:])
    return b"".join(parts)


def _validate_result(
    blocks: tuple[Block, ...],
    usages: dict[str, MapUsagePlan],
    assignments: dict[str, EntitySkinAssignment],
    preserved_dynamic: dict[str, dict[bytes, tuple[bytes, ...]]],
    fallback_ids: set[str],
    *,
    value_encoding: str,
) -> None:
    found: set[str] = set()
    for block in blocks:
        if block.name.lower() != b"entity":
            continue
        ids = block.direct_values(b"id")
        if len(ids) != 1:
            continue
        try:
            entity_id = ids[0].decode(value_encoding)
        except UnicodeError:
            continue
        if entity_id not in usages and entity_id not in fallback_ids:
            continue
        if entity_id in found:
            raise VmfOutputError(
                "vmf_output_target_duplicate",
                f"entity ID {entity_id!r} occurs more than once after editing",
            )
        found.add(entity_id)
        if entity_id in fallback_ids:
            if block.direct_values(b"classname") != (b"prop_dynamic_override",):
                raise VmfOutputError(
                    "vmf_output_validation_failed",
                    f"entity {entity_id}: general fallback classname was not written",
                )
            if block.direct_values(b"convert_prop_to_static"):
                raise VmfOutputError(
                    "vmf_output_psr_key_retained",
                    f"entity {entity_id}: retained fallback service keys",
                )
            for key, value in preserved_dynamic[entity_id].items():
                if block.direct_values(key) != value:
                    raise VmfOutputError(
                        "vmf_output_dynamic_property_changed",
                        f"entity {entity_id}: {key!r} changed during general fallback",
                    )
            continue
        assignment = assignments[entity_id]
        expected = {
            b"classname": usages[entity_id].output_classname.encode("ascii"),
        }
        if usages[entity_id].operation != "reuse_dynamic":
            expected.update({
                b"model": assignment.logical_output_model.encode(value_encoding),
                b"skin": str(assignment.target_skin).encode("ascii"),
            })
        for key, value in expected.items():
            actual = block.direct_values(key)
            if actual != (value,):
                raise VmfOutputError(
                    "vmf_output_validation_failed",
                    f"entity {entity_id}: {key!r} is {actual!r}, expected {(value,)!r}",
                )
        remaining = sorted(
            key.decode("ascii")
            for key in _removed_keys(usages[entity_id])
            if block.direct_values(key)
        )
        if remaining:
            raise VmfOutputError(
                "vmf_output_psr_key_retained",
                f"entity {entity_id}: retained PSR-only keys {remaining!r}",
            )
        if usages[entity_id].operation == "reuse_dynamic":
            for key, value in preserved_dynamic[entity_id].items():
                actual = block.direct_values(key)
                if actual != value:
                    raise VmfOutputError(
                        "vmf_output_dynamic_property_changed",
                        f"entity {entity_id}: {key!r} is {actual!r}, "
                        f"expected preserved value {value!r}",
                    )
    expected_ids = set(usages) | fallback_ids
    if found != expected_ids:
        missing = sorted(expected_ids - found, key=int)
        raise VmfOutputError(
            "vmf_output_target_missing",
            f"transformed entity IDs missing after editing: {missing!r}",
        )


def _removed_keys(usage: MapUsagePlan) -> set[bytes]:
    if usage.operation == "reuse_dynamic":
        return _DYNAMIC_PSR_ONLY_KEYS
    return _STATIC_PSR_ONLY_KEYS


__all__ = [
    "VmfOutput",
    "VmfOutputError",
    "VmfFallbackAssignment",
    "build_vmf_output",
]
