from __future__ import annotations

import unittest
from decimal import Decimal

from psr.keyvalues import parse_vmf
from psr.pipeline import (
    EntitySkinAssignment,
    MapUsagePlan,
    OperationPlan,
    SkinLayoutOperationPlan,
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
