from __future__ import annotations

import hashlib
import unittest
from decimal import Decimal
from pathlib import Path

from psr.assets.qc import (
    QCTransformError,
    build_reference_qc,
    build_scaled_qc,
    first_body_reference_smd,
    inspect_qc,
    static_bodygroup_empty_smd_name,
)


FIXTURES = Path(__file__).parent / "fixtures" / "qc"


class QCInspectionTests(unittest.TestCase):
    def test_token_aware_inspection_ignores_comments_strings_and_nested_commands(self) -> None:
        source = (FIXTURES / "static_formatting.qc").read_bytes()

        metadata = inspect_qc(source)

        self.assertEqual(metadata.model_name, "props/{brace}_static.mdl")
        self.assertEqual(metadata.scale, "1.0")
        self.assertTrue(metadata.is_static_prop)
        self.assertEqual(metadata.skin_families, (("body",),))
        self.assertEqual(metadata.lod_distances, ("25",))
        self.assertEqual(metadata.command_names.count("$texturegroup"), 1)
        self.assertNotIn("$mass", metadata.command_names)

    def test_unbalanced_document_is_rejected(self) -> None:
        with self.assertRaisesRegex(QCTransformError, "unclosed_brace"):
            inspect_qc(b'$modelname "props/test.mdl"\n$lod 10 {\n')

    def test_duplicate_singleton_command_is_rejected(self) -> None:
        source = (
            b'$modelname "props/a.mdl"\n'
            b'$modelname "props/b.mdl"\n'
        )
        with self.assertRaisesRegex(QCTransformError, "duplicate_modelname"):
            inspect_qc(source)

    def test_crowbar_cdmaterials_trailing_backslash_closes_normally(self) -> None:
        source = (
            b'$modelname "props/test.mdl"\n'
            b'$staticprop\n'
            b'$cdmaterials "models\\props\\"\n'
        )

        metadata = inspect_qc(source)

        self.assertEqual(metadata.model_name, "props/test.mdl")
        self.assertIn("$cdmaterials", metadata.command_names)


class ReferenceQCTransformTests(unittest.TestCase):
    def test_dynamic_static_conversion_replaces_bodygroup_blank_with_empty_smd(self) -> None:
        source = (FIXTURES / "dynamic_bodygroup_blank.qc").read_bytes()
        expected_name = static_bodygroup_empty_smd_name(
            hashlib.sha256(source).hexdigest()
        )

        result = build_reference_qc(
            source,
            expected_source_families=(("body",),),
            target_families=(("body",),),
            require_staticprop=True,
        )

        self.assertIn(b"$staticprop", result.data)
        self.assertIn(f'studio "{expected_name}"'.encode("ascii"), result.data)
        self.assertNotIn(b"\n    blank", result.data)
        self.assertEqual(first_body_reference_smd(source), "door_reference.smd")
        self.assertEqual(
            result.mutations,
            ("insert_staticprop", "replace_bodygroup_blanks"),
        )

    def test_existing_static_bodygroup_blank_is_not_rewritten(self) -> None:
        source = (
            (FIXTURES / "dynamic_bodygroup_blank.qc").read_bytes()
            .replace(
                b'$modelname "props/door.mdl"\n',
                b'$modelname "props/door.mdl"\n$staticprop\n',
            )
        )

        result = build_reference_qc(
            source,
            expected_source_families=(("body",),),
            target_families=(("body",),),
            require_staticprop=True,
        )

        self.assertIn(b"\n    blank", result.data)
        self.assertNotIn("replace_bodygroup_blanks", result.mutations)

    def test_studio_argument_named_blank_is_not_a_blank_bodygroup_option(self) -> None:
        source = (
            b'$modelname "props/word_blank.mdl"\n'
            b'$bodygroup "body"\n'
            b'{\n'
            b'    studio "mesh.smd"\n'
            b'    studio blank\n'
            b'}\n'
        )

        result = build_reference_qc(
            source,
            expected_source_families=(("body",),),
            target_families=(("body",),),
            require_staticprop=True,
        )

        self.assertIn(b"    studio blank\n", result.data)
        self.assertNotIn("replace_bodygroup_blanks", result.mutations)

    def test_dynamic_reference_adds_staticprop_and_replaces_complete_skin_table(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()
        target_families = (
            ("body", "detail"),
            ("body_alt", "detail_alt"),
            ("models/psr_scaled/body_col_ff0000", "detail"),
        )

        result = build_reference_qc(
            source,
            expected_source_families=target_families[:2],
            target_families=target_families,
            require_staticprop=True,
        )
        metadata = inspect_qc(result.data)

        self.assertEqual(
            result.mutations,
            ("insert_staticprop", "replace_skinfamilies"),
        )
        self.assertTrue(metadata.is_static_prop)
        self.assertEqual(metadata.skin_families, target_families)
        self.assertIn(b'$modelname "props/example_dynamic.mdl" // keep this comment', result.data)
        collision = source[source.index(b"$collisionjoints"):source.index(b"$sequence")]
        self.assertIn(collision, result.data)
        self.assertIn(b"Fake command: $staticprop { ignored }", result.data)
        self.assertNotEqual(result.source_sha256, result.output_sha256)

    def test_static_reference_is_idempotent_when_layout_is_unchanged(self) -> None:
        source = (FIXTURES / "static_formatting.qc").read_bytes()

        first = build_reference_qc(
            source,
            expected_source_families=(("body",),),
            target_families=(("body",),),
            require_staticprop=True,
        )
        second = build_reference_qc(
            first.data,
            expected_source_families=(("body",),),
            target_families=(("body",),),
            require_staticprop=True,
        )

        self.assertEqual(first.data, source)
        self.assertEqual(first.mutations, ())
        self.assertEqual(second.data, source)

    def test_missing_texturegroup_is_valid_for_one_source_family_and_inserted_for_color(self) -> None:
        source = b'$modelname "props/single.mdl"\n$body "b" "b.smd"\n'

        result = build_reference_qc(
            source,
            expected_source_families=(("body",),),
            target_families=(("body",), ("body_col",)),
            require_staticprop=True,
        )

        self.assertEqual(
            result.mutations,
            ("insert_staticprop", "insert_skinfamilies"),
        )
        self.assertEqual(
            inspect_qc(result.data).skin_families,
            (("body",), ("body_col",)),
        )

    def test_missing_texturegroup_is_rejected_for_multiple_source_families(self) -> None:
        source = b'$modelname "props/multi.mdl"\n'
        with self.assertRaisesRegex(QCTransformError, "source_skinfamilies_missing"):
            build_reference_qc(
                source,
                expected_source_families=(("a",), ("b",)),
                target_families=(("a",), ("b",)),
                require_staticprop=False,
            )

    def test_source_skin_mismatch_is_rejected_before_replacement(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()
        with self.assertRaisesRegex(QCTransformError, "source_skinfamilies_mismatch"):
            build_reference_qc(
                source,
                expected_source_families=(("wrong", "detail"),),
                target_families=(("wrong", "detail"),),
                require_staticprop=True,
            )

    def test_source_sdk_skin_and_material_capacity_are_defensive_limits(self) -> None:
        source = b'$modelname "props/single.mdl"\n$body "b" "b.smd"\n'
        with self.assertRaises(QCTransformError) as families_error:
            build_reference_qc(
                source,
                expected_source_families=(("body",),),
                target_families=(("body",),) * 1025,
                require_staticprop=True,
            )
        self.assertEqual(families_error.exception.code, "target_skinfamilies_limit")

        with self.assertRaises(QCTransformError) as materials_error:
            build_reference_qc(
                source,
                expected_source_families=(("body",),),
                target_families=tuple(
                    (f"material_{index}",)
                    for index in range(32)
                ),
                require_staticprop=True,
            )
        self.assertEqual(materials_error.exception.code, "target_materials_limit")


class ScaledQCTransformTests(unittest.TestCase):
    def test_scaled_variant_changes_identity_scale_and_lod_only(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()
        reference = build_reference_qc(
            source,
            expected_source_families=(
                ("body", "detail"),
                ("body_alt", "detail_alt"),
            ),
            target_families=(
                ("body", "detail"),
                ("body_alt", "detail_alt"),
            ),
            require_staticprop=True,
        )

        result = build_scaled_qc(
            reference.data,
            logical_output_model="models/psr_scaled/props/example_dynamic_scaled_150.mdl",
            compile_scale=Decimal("1.50"),
            geometry_scale=Decimal("1.50"),
        )
        metadata = inspect_qc(result.data)

        self.assertEqual(metadata.model_name, "psr_scaled/props/example_dynamic_scaled_150.mdl")
        self.assertEqual(metadata.scale, "1.5")
        self.assertEqual(metadata.lod_distances, ("60", "180"))
        self.assertTrue(metadata.is_static_prop)
        self.assertEqual(
            result.mutations,
            ("replace_modelname", "insert_scale", "scale_lod_distances"),
        )
        collision = reference.data[
            reference.data.index(b"$collisionjoints"):
            reference.data.index(b"$sequence")
        ]
        self.assertIn(collision, result.data)
        self.assertIn(b"$bbox -10 -20 -30 10 20 30", result.data)
        self.assertIn(b"$illumposition 1 2 3", result.data)

    def test_existing_scale_is_replaced_not_multiplied(self) -> None:
        source = (FIXTURES / "static_formatting.qc").read_bytes()

        result = build_scaled_qc(
            source,
            logical_output_model="models/psr_scaled/props/static_scaled_050.mdl",
            compile_scale=Decimal("0.50"),
            geometry_scale=Decimal("0.50"),
        )

        self.assertEqual(inspect_qc(result.data).scale, "0.5")
        self.assertEqual(inspect_qc(result.data).lod_distances, ("12.5",))
        self.assertIn(b'$collisionmodel "static_physics.smd"', result.data)
        self.assertEqual(
            result.mutations,
            ("replace_modelname", "replace_scale", "scale_lod_distances"),
        )

    def test_crlf_is_used_for_inserted_commands_and_unrelated_bytes_survive(self) -> None:
        source = (
            b'// cp1252:\x96\r\n'
            b'$modelname "props/crlf.mdl" // tail\r\n'
            b'$staticprop\r\n'
            b'$body "b" "b.smd"\r\n'
        )

        result = build_scaled_qc(
            source,
            logical_output_model="models/psr_scaled/props/crlf_scaled_100.mdl",
            compile_scale=Decimal("1.00"),
            geometry_scale=Decimal("1.00"),
        )

        self.assertIn(b'// cp1252:\x96\r\n', result.data)
        self.assertIn(b' // tail\r\n$scale 1\r\n', result.data)
        self.assertNotIn(b"\n$scale 1\n", result.data)

    def test_quadratic_geometry_does_not_change_managed_identity(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()
        reference = build_reference_qc(
            source,
            expected_source_families=(
                ("body", "detail"),
                ("body_alt", "detail_alt"),
            ),
            target_families=(
                ("body", "detail"),
                ("body_alt", "detail_alt"),
            ),
            require_staticprop=True,
        )

        result = build_scaled_qc(
            reference.data,
            logical_output_model="models/psr_scaled/props/example_dynamic_scaled_150.mdl",
            compile_scale=Decimal("1.50"),
            geometry_scale=Decimal("2.2500"),
        )
        metadata = inspect_qc(result.data)

        self.assertEqual(metadata.model_name, "psr_scaled/props/example_dynamic_scaled_150.mdl")
        self.assertEqual(metadata.scale, "2.25")
        self.assertEqual(metadata.lod_distances, ("90", "270"))

    def test_unsafe_or_noncanonical_inputs_are_rejected(self) -> None:
        source = b'$modelname "props/a.mdl"\n$staticprop\n'
        cases = [
            ("models/props/not_managed.mdl", Decimal("1.00"), "managed_output_model"),
            ("models/psr_scaled/props/a.mdl", Decimal("1.001"), "noncanonical_compile_scale"),
        ]
        for model, scale, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(QCTransformError, code):
                    build_scaled_qc(
                        source,
                        logical_output_model=model,
                        compile_scale=scale,
                        geometry_scale=scale,
                    )


if __name__ == "__main__":
    unittest.main()
