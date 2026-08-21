from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from psr.assets import AssetProvenance, SourceAssetMetadata, inspect_qc
from psr.domain import resolve_geometry_scale
from psr.pipeline import (
    GeneratedModelRequirement,
    ModelSkinLayoutPlan,
    OperationPlan,
    SkinLayoutOperationPlan,
    build_qc_operation_plan,
)


FIXTURES = Path(__file__).parent / "fixtures" / "qc"
MODEL = "models/props/example_dynamic.mdl"


def source_asset(*, is_static: bool = False) -> SourceAssetMetadata:
    provenance = AssetProvenance(
        logical_path=MODEL,
        mount_index=0,
        source_ordinal=0,
        expansion_index=0,
        path_id="game",
        raw_value="fixture",
        kind="folder",
        container_path=Path("C:/synthetic"),
    )
    return SourceAssetMetadata(
        logical_model_path=MODEL,
        model_provenance=provenance,
        internal_model_name="props/example_dynamic.mdl",
        mdl_version=48,
        mdl_header_checksum="11223344",
        mdl_flags=0,
        is_static_prop=is_static,
        bone_count=1,
        surface_property="default",
        total_vertices=12,
        cdmaterials=("models/props/example/",),
        skin_families=(("body", "detail"), ("body_alt", "detail_alt")),
        material_names=("body", "detail", "body_alt", "detail_alt"),
        materials=(),
        files=(),
    )


def operation(asset: SourceAssetMetadata | None = None) -> OperationPlan:
    asset = source_asset() if asset is None else asset
    requirements = tuple(
        GeneratedModelRequirement(
            logical_source_model=MODEL,
            logical_output_model=(
                "models/psr_scaled/props/example_dynamic_scaled_050.mdl"
                if scale == Decimal("0.50")
                else "models/psr_scaled/props/example_dynamic_scaled_150.mdl"
            ),
            compile_scale=scale,
            geometry_scale=resolve_geometry_scale(
                scale,
                bone_count=asset.bone_count,
                is_static_prop=asset.is_static_prop,
            ).geometry_scale,
            requires_static_conversion=not asset.is_static_prop,
            entity_ids=(str(index),),
        )
        for index, scale in enumerate((Decimal("1.50"), Decimal("0.50")), start=1)
    )
    return OperationPlan(
        map_identity="maps/qc_test.vmf",
        vmf_sha256="a" * 64,
        source_assets=(asset,),
        usages=(),
        generated_models=requirements,
        colored_skins=(),
        diagnostics=(),
    )


def skin_layout() -> SkinLayoutOperationPlan:
    layout = ModelSkinLayoutPlan(
        logical_source_model=MODEL,
        source_family_count=2,
        source_skin_families_fingerprint="b" * 64,
        families=(
            ("body", "detail"),
            ("body_alt", "detail_alt"),
            ("models/psr_scaled/body_col_ff0000", "detail"),
        ),
        mappings=(),
        layout_fingerprint="c" * 64,
        cache_reset=False,
    )
    return SkinLayoutOperationPlan(
        map_identity="maps/qc_test.vmf",
        layouts=(layout,),
        assignments=(),
        diagnostics=(),
    )


class QCOperationPlanningTests(unittest.TestCase):
    def test_one_reference_drives_sorted_scale_variants(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()

        plan = build_qc_operation_plan(
            operation(),
            skin_layout(),
            {MODEL: source},
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.references), 1)
        reference = plan.references[0]
        self.assertTrue(reference.requires_static_conversion)
        self.assertEqual(reference.staging_relative_path, "reference/props/example_dynamic.qc")
        self.assertEqual(
            inspect_qc(reference.content).skin_families,
            skin_layout().layouts[0].families,
        )
        self.assertEqual(
            [variant.compile_scale for variant in plan.variants],
            [Decimal("0.50"), Decimal("1.50")],
        )
        self.assertEqual(
            [variant.geometry_scale for variant in plan.variants],
            [Decimal("0.2500"), Decimal("2.2500")],
        )
        self.assertEqual(
            [inspect_qc(variant.content).scale for variant in plan.variants],
            ["0.25", "2.25"],
        )
        self.assertEqual(
            [variant.staging_relative_path for variant in plan.variants],
            [
                "variants/psr_scaled/props/example_dynamic_scaled_050.qc",
                "variants/psr_scaled/props/example_dynamic_scaled_150.qc",
            ],
        )
        self.assertTrue(all(
            variant.reference_qc_sha256 == reference.output_qc_sha256
            for variant in plan.variants
        ))
        self.assertEqual(
            inspect_qc(plan.variants[1].content).model_name,
            "psr_scaled/props/example_dynamic_scaled_150.mdl",
        )

    def test_missing_staged_qc_is_a_diagnostic_and_produces_no_artifacts(self) -> None:
        plan = build_qc_operation_plan(operation(), skin_layout(), {})

        self.assertFalse(plan.is_valid)
        self.assertEqual([item.code for item in plan.diagnostics], ["source_qc_missing"])
        self.assertEqual(plan.references, ())
        self.assertEqual(plan.variants, ())

    def test_mdl_qc_static_flag_disagreement_blocks_generation(self) -> None:
        source = (FIXTURES / "dynamic_physics.qc").read_bytes()
        static_asset = replace(source_asset(), is_static_prop=True)

        plan = build_qc_operation_plan(
            operation(static_asset),
            skin_layout(),
            {MODEL: source},
        )

        self.assertFalse(plan.is_valid)
        self.assertEqual(
            [item.code for item in plan.diagnostics],
            ["qc_static_flag_mismatch"],
        )

    def test_stale_qc_skin_table_is_reported_with_transform_code(self) -> None:
        source = (FIXTURES / "static_formatting.qc").read_bytes()
        plan = build_qc_operation_plan(
            operation(replace(source_asset(), is_static_prop=True)),
            skin_layout(),
            {MODEL: source},
        )

        self.assertFalse(plan.is_valid)
        self.assertEqual(
            [item.code for item in plan.diagnostics],
            ["qc_source_skinfamilies_mismatch"],
        )


if __name__ == "__main__":
    unittest.main()
