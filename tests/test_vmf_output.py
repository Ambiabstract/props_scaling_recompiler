from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from psr.keyvalues import parse_vmf
from psr.pipeline import (
    EntitySkinAssignment,
    MapUsagePlan,
    OperationPlan,
    SkinLayoutOperationPlan,
    VmfFallbackAssignment,
    VmfOutputError,
    build_vmf_output,
    discover_vmf_requests,
)


ACTIVE_AND_HIDDEN = b'''// bytes outside the active entity must remain exact.\r\nentity\r\n{\r\n\t"id" "100"\r\n\t"classname" "prop_static_scalable"\r\n\t"model" "models/fixture/source.mdl"\r\n\t"modelscale" "1.5"\r\n\t"rendercolor" "190 48 148"\r\n\t"convert_prop_to_static" "1"\r\n\t"custom_repeat" "first"\r\n\t"custom_repeat" "second"\r\n\teditor\r\n\t{\r\n\t\t"classname" "nested_must_stay"\r\n\t}\r\n}\r\nhidden\r\n{\r\n\tentity\r\n\t{\r\n\t\t"id" "200"\r\n\t\t"classname" "prop_static_scalable"\r\n\t\t"model" "models/fixture/hidden.mdl"\r\n\t\t"modelscale" "2"\r\n\t}\r\n}\r\n'''


def plans(source: bytes) -> tuple[OperationPlan, SkinLayoutOperationPlan]:
    discovery = discover_vmf_requests(source, map_identity="maps/output.vmf")
    request = discovery.requests[0]
    usage = MapUsagePlan(
        request=request,
        compile_scale=Decimal("1.50"),
        geometry_scale=Decimal("1.50"),
        source_skin=0,
        render_color=(190, 48, 148),
        operation="generate_model",
        logical_output_model="models/psr_scaled/fixture/source_scaled_150.mdl",
        output_classname="prop_static",
    )
    operation = OperationPlan(
        map_identity=discovery.map_identity,
        vmf_sha256=discovery.vmf_sha256,
        source_assets=(),
        usages=(usage,),
        generated_models=(),
        colored_skins=(),
        diagnostics=(),
    )
    skin_layout = SkinLayoutOperationPlan(
        map_identity=discovery.map_identity,
        layouts=(),
        assignments=(EntitySkinAssignment(
            entity_id="100",
            logical_source_model=request.logical_model_path,
            source_skin=0,
            render_color=(190, 48, 148),
            target_skin=7,
            logical_output_model=usage.logical_output_model,
            used_color_fallback=False,
        ),),
        diagnostics=(),
    )
    return operation, skin_layout


class VmfOutputTests(unittest.TestCase):
    def test_transforms_only_active_direct_properties_and_inserts_skin(self) -> None:
        operation, skin_layout = plans(ACTIVE_AND_HIDDEN)
        hidden_bytes = ACTIVE_AND_HIDDEN[ACTIVE_AND_HIDDEN.index(b"hidden\r\n"):]

        result = build_vmf_output(ACTIVE_AND_HIDDEN, operation, skin_layout)

        self.assertEqual(result.transformed_entity_ids, ("100",))
        self.assertTrue(result.content.endswith(hidden_bytes))
        self.assertIn(b'\t"custom_repeat" "first"\r\n', result.content)
        self.assertIn(b'\t"custom_repeat" "second"\r\n', result.content)
        self.assertIn(b'\t\t"classname" "nested_must_stay"\r\n', result.content)
        document = parse_vmf(result.content)
        active = document.blocks[0]
        self.assertEqual(active.direct_values(b"classname"), (b"prop_static",))
        self.assertEqual(
            active.direct_values(b"model"),
            (b"models/psr_scaled/fixture/source_scaled_150.mdl",),
        )
        self.assertEqual(active.direct_values(b"skin"), (b"7",))
        self.assertEqual(active.direct_values(b"modelscale"), ())
        self.assertEqual(active.direct_values(b"rendercolor"), ())
        self.assertEqual(active.direct_values(b"convert_prop_to_static"), ())

    def test_existing_skin_is_replaced_without_reserialising_entity(self) -> None:
        source = ACTIVE_AND_HIDDEN.replace(
            b'\t"rendercolor" "190 48 148"\r\n',
            b'\t"skin" "0" // keep this comment\r\n'
            b'\t"rendercolor" "190 48 148"\r\n',
        )
        operation, skin_layout = plans(source)

        result = build_vmf_output(source, operation, skin_layout)

        self.assertIn(b'\t"skin" "7" // keep this comment\r\n', result.content)

    def test_dynamic_fallback_writes_prop_dynamic(self) -> None:
        source = ACTIVE_AND_HIDDEN.replace(
            b'"model" "models/fixture/source.mdl"',
            b'"model" "Models/Fixture/Source.mdl"',
        )
        operation, skin_layout = plans(source)
        source_model = operation.usages[0].request.logical_model_path
        operation = OperationPlan(
            map_identity=operation.map_identity,
            vmf_sha256=operation.vmf_sha256,
            source_assets=operation.source_assets,
            usages=(replace(
                operation.usages[0],
                operation="reuse_dynamic",
                logical_output_model=source_model,
                output_classname="prop_dynamic",
            ),),
            generated_models=operation.generated_models,
            colored_skins=operation.colored_skins,
            diagnostics=operation.diagnostics,
        )
        skin_layout = replace(
            skin_layout,
            assignments=(replace(
                skin_layout.assignments[0],
                target_skin=0,
                logical_output_model=source_model,
            ),),
        )

        result = build_vmf_output(source, operation, skin_layout)

        active = parse_vmf(result.content).blocks[0]
        self.assertEqual(active.direct_values(b"classname"), (b"prop_dynamic",))
        self.assertEqual(
            active.direct_values(b"model"),
            (b"Models/Fixture/Source.mdl",),
        )
        self.assertEqual(active.direct_values(b"modelscale"), (b"1.5",))
        self.assertEqual(active.direct_values(b"rendercolor"), (b"190 48 148",))
        self.assertEqual(active.direct_values(b"skin"), ())
        self.assertEqual(active.direct_values(b"convert_prop_to_static"), ())

    def test_dynamic_fallback_preserves_existing_skin_bytes(self) -> None:
        source = ACTIVE_AND_HIDDEN.replace(
            b'\t"rendercolor" "190 48 148"\r\n',
            b'\t"skin" "00" // preserve exactly\r\n'
            b'\t"rendercolor" "190 48 148"\r\n',
        )
        operation, skin_layout = plans(source)
        source_model = operation.usages[0].request.logical_model_path
        operation = replace(
            operation,
            usages=(replace(
                operation.usages[0],
                operation="reuse_dynamic",
                logical_output_model=source_model,
                output_classname="prop_dynamic",
            ),),
        )
        skin_layout = replace(
            skin_layout,
            assignments=(replace(
                skin_layout.assignments[0],
                target_skin=0,
                logical_output_model=source_model,
            ),),
        )

        result = build_vmf_output(source, operation, skin_layout)

        self.assertIn(b'\t"skin" "00" // preserve exactly\r\n', result.content)

    def test_general_fallback_writes_dynamic_override_and_preserves_runtime_values(self) -> None:
        source = ACTIVE_AND_HIDDEN.replace(
            b'\t"rendercolor" "190 48 148"\r\n',
            b'\t"skin" "00" // preserve exactly\r\n'
            b'\t"rendercolor" "190 48 148"\r\n',
        )
        operation, skin_layout = plans(source)
        operation = replace(operation, usages=())
        skin_layout = replace(skin_layout, assignments=())

        result = build_vmf_output(
            source,
            operation,
            skin_layout,
            fallbacks=(VmfFallbackAssignment("100"),),
        )

        active = parse_vmf(result.content).blocks[0]
        self.assertEqual(active.direct_values(b"classname"), (b"prop_dynamic_override",))
        self.assertEqual(active.direct_values(b"modelscale"), (b"1.5",))
        self.assertEqual(active.direct_values(b"rendercolor"), (b"190 48 148",))
        self.assertEqual(active.direct_values(b"skin"), (b"00",))
        self.assertEqual(active.direct_values(b"convert_prop_to_static"), ())
        self.assertIn(b'\t"skin" "00" // preserve exactly\r\n', result.content)

    def test_compile_failure_dispositions_cover_remove_static_and_scalable(self) -> None:
        operation, skin_layout = plans(ACTIVE_AND_HIDDEN)
        operation = replace(operation, usages=())
        skin_layout = replace(skin_layout, assignments=())

        removed = build_vmf_output(
            ACTIVE_AND_HIDDEN,
            operation,
            skin_layout,
            fallbacks=(VmfFallbackAssignment("100", "remove"),),
        )
        self.assertNotIn(
            b"100",
            b" ".join(
                block.direct_values(b"id")[0]
                for block in parse_vmf(removed.content).blocks
                if block.name.lower() == b"entity" and block.direct_values(b"id")
            ),
        )

        missing_static = build_vmf_output(
            ACTIVE_AND_HIDDEN,
            operation,
            skin_layout,
            fallbacks=(VmfFallbackAssignment(
                "100",
                "missing_static",
                "models/psr_scaled/props_fixture/static_scaled_150.mdl",
                2,
            ),),
        )
        active = parse_vmf(missing_static.content).blocks[0]
        self.assertEqual(active.direct_values(b"classname"), (b"prop_static",))
        self.assertEqual(
            active.direct_values(b"model"),
            (b"models/psr_scaled/props_fixture/static_scaled_150.mdl",),
        )
        self.assertEqual(active.direct_values(b"skin"), (b"2",))
        self.assertEqual(active.direct_values(b"modelscale"), ())
        self.assertEqual(active.direct_values(b"rendercolor"), ())

        scalable = build_vmf_output(
            ACTIVE_AND_HIDDEN,
            operation,
            skin_layout,
            fallbacks=(VmfFallbackAssignment("100", "scalable"),),
        )
        active = parse_vmf(scalable.content).blocks[0]
        self.assertEqual(active.direct_values(b"classname"), (b"prop_scalable",))
        self.assertEqual(active.direct_values(b"modelscale"), (b"1.5",))
        self.assertEqual(active.direct_values(b"rendercolor"), (b"190 48 148",))
        self.assertEqual(active.direct_values(b"convert_prop_to_static"), ())

    def test_noop_output_is_byte_identical(self) -> None:
        source = b'world\n{\n\t"id" "1"\n}\n'
        discovery = discover_vmf_requests(source, map_identity="maps/noop.vmf")
        operation = OperationPlan(
            map_identity=discovery.map_identity,
            vmf_sha256=discovery.vmf_sha256,
            source_assets=(),
            usages=(),
            generated_models=(),
            colored_skins=(),
            diagnostics=(),
        )
        skin_layout = SkinLayoutOperationPlan(
            map_identity=discovery.map_identity,
            layouts=(),
            assignments=(),
            diagnostics=(),
        )

        result = build_vmf_output(source, operation, skin_layout)

        self.assertEqual(result.content, source)
        self.assertEqual(result.transformed_entity_ids, ())

    def test_changed_input_is_rejected_before_editing(self) -> None:
        operation, skin_layout = plans(ACTIVE_AND_HIDDEN)

        with self.assertRaises(VmfOutputError) as raised:
            build_vmf_output(ACTIVE_AND_HIDDEN + b"// changed\r\n", operation, skin_layout)

        self.assertEqual(raised.exception.code, "vmf_input_changed")


if __name__ == "__main__":
    unittest.main()
