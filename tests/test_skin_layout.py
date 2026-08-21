from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from psr.assets import OrderedAssetFileSystem, parse_search_paths_text, plan_search_paths
from psr.cache import (
    GeneratedModelRecord,
    build_project_identity,
    empty_manifest,
    load_manifest,
    save_manifest_atomic,
)
from psr.domain import scaled_model_path
from psr.pipeline import (
    build_colored_material_plan,
    build_operation_plan,
    build_skin_layout_plan,
    commit_skin_layout_plan,
    discover_vmf_requests,
    inspect_colored_material_sources,
    inspect_map_sources,
    reconcile_generation_requirements,
)
from tests.mdl_fixture_builder import build_case_files


FIXTURES = Path(__file__).parent / "fixtures"
MDL_FIXTURE = FIXTURES / "mdl" / "synthetic_mdl_cases.json"
VMT_FIXTURES = FIXTURES / "vmt"


def load_case(name: str) -> dict[str, Any]:
    document = json.loads(MDL_FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(next(case for case in document["cases"] if case["name"] == name))


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for logical_path, data in files.items():
        destination = root / Path(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def make_gameinfo(value: str) -> str:
    return f'''GameInfo
{{
    FileSystem
    {{
        SearchPaths
        {{
            game "{value}"
        }}
    }}
}}
'''


def entity(
    entity_id: str,
    model: str,
    *,
    scale: str = "1",
    skin: int = 0,
    color: tuple[int, int, int] = (255, 255, 255),
) -> str:
    return f'''entity
{{
    "id" "{entity_id}"
    "classname" "prop_static_scalable"
    "model" "{model}"
    "modelscale" "{scale}"
    "skin" "{skin}"
    "rendercolor" "{color[0]} {color[1]} {color[2]}"
}}
'''


class StableSkinLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.content = self.root / "content"
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()
        self.gameinfo = self.root / "GameInfo.txt"
        self.gameinfo.write_text(make_gameinfo("|gameinfo_path|content"), encoding="utf-8")
        self.project = build_project_identity(self.gameinfo)
        self.case = load_case("static_multi_material")
        self.case["material_files"]["materials/models/fixture/primary/body.vmt"] = (
            VMT_FIXTURES / "vertexlit_no_color.vmt"
        ).read_text(encoding="utf-8")
        self.case["material_files"]["materials/models/fixture/fallback/accent.vmt"] = (
            VMT_FIXTURES / "vertexlit_color2_proxy.vmt"
        ).read_text(encoding="utf-8")
        write_files(self.content, build_case_files(self.case))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def filesystem(self) -> OrderedAssetFileSystem:
        specs = parse_search_paths_text(make_gameinfo("|gameinfo_path|content"))
        search_plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine_root,
        )
        self.assertFalse(search_plan.diagnostics)
        return OrderedAssetFileSystem(search_plan.mounts)

    def plans(self, map_name: str, requests: list[str]):
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(
            "".join(requests).encode("ascii"),
            map_identity=f"maps/{map_name}.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, inspection)
        self.assertTrue(operation.is_valid)
        self.assertTrue(materials.is_valid)
        return operation, materials

    def test_cold_layout_keeps_original_indices_and_sorts_new_mappings(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("cold", [
            entity("1", model, color=(190, 48, 148)),
            entity("2", model, scale="2", color=(86, 202, 181)),
            entity("3", model, skin=1),
        ])

        plan = build_skin_layout_plan(operation, materials, empty_manifest(self.project))

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.layouts), 1)
        layout = plan.layouts[0]
        self.assertEqual(layout.source_family_count, 2)
        self.assertEqual([item.target_skin for item in layout.mappings], [2, 3])
        self.assertEqual(
            [(item.source_skin, item.render_color) for item in layout.mappings],
            [(0, (86, 202, 181)), (0, (190, 48, 148))],
        )
        self.assertEqual(len(layout.families), 4)
        self.assertFalse(layout.cache_reset)
        self.assertFalse(layout.rebuild_cached_scales)
        assignments = {item.entity_id: item.target_skin for item in plan.assignments}
        self.assertEqual(assignments, {"1": 3, "2": 2, "3": 1})

    def test_material_limit_omits_color_and_falls_back_to_original_skin(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("material-limit", [
            entity(str(index), model, color=(0, 0, index))
            for index in range(1, 16)
        ])

        plan = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(self.project),
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(materials.colored_materials), 30)
        layout = plan.layouts[0]
        self.assertEqual(len(layout.families), 15)
        self.assertEqual(len(layout.mappings), 13)
        self.assertEqual(
            len({material for family in layout.families for material in family}),
            30,
        )
        warnings = [
            item
            for item in plan.diagnostics
            if item.code == "model_material_limit_reached"
        ]
        self.assertEqual(len(warnings), 2)
        self.assertEqual(
            [item.entity_id for item in warnings],
            ["14", "15"],
        )
        assignments = {item.entity_id: item for item in plan.assignments}
        self.assertEqual(assignments["14"].target_skin, 0)
        self.assertTrue(assignments["14"].used_color_fallback)
        self.assertEqual(assignments["15"].target_skin, 0)
        self.assertTrue(assignments["15"].used_color_fallback)
        self.assertTrue(all(
            not assignments[str(index)].used_color_fallback
            for index in range(1, 14)
        ))

        committed = commit_skin_layout_plan(
            empty_manifest(self.project),
            operation,
            plan,
        )
        fallback_usage = next(
            item for item in committed.map_usages if item.entity_id == "15"
        )
        self.assertEqual(fallback_usage.target_skin, 0)
        self.assertEqual(fallback_usage.render_color, (0, 0, 15))

    def test_skin_family_limit_omits_only_overflow_color(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("family-limit", [
            entity(
                str(index),
                model,
                color=(index // 256, index % 256, 1),
            )
            for index in range(1, 1024)
        ])

        # This isolates the independent row limit. Real models normally reach
        # the stricter unique-material limit first.
        with patch("psr.pipeline.skin_layout.MAX_STUDIO_MATERIALS", 10_000):
            plan = build_skin_layout_plan(
                operation,
                materials,
                empty_manifest(self.project),
            )

        self.assertTrue(plan.is_valid)
        layout = plan.layouts[0]
        self.assertEqual(len(layout.families), 1024)
        self.assertEqual(len(layout.mappings), 1022)
        warnings = [
            item
            for item in plan.diagnostics
            if item.code == "skin_family_limit_reached"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].entity_id, "1023")
        assignment = next(
            item for item in plan.assignments if item.entity_id == "1023"
        )
        self.assertEqual(assignment.target_skin, 0)
        self.assertTrue(assignment.used_color_fallback)

    def test_warm_layout_retains_unrequested_rows_and_appends_new_color(self) -> None:
        model = self.case["logical_model_path"]
        cold_operation, cold_materials = self.plans("cold", [
            entity("1", model, color=(190, 48, 148)),
            entity("2", model, color=(86, 202, 181)),
        ])
        cold_plan = build_skin_layout_plan(
            cold_operation,
            cold_materials,
            empty_manifest(self.project),
        )
        manifest = commit_skin_layout_plan(
            empty_manifest(self.project),
            cold_operation,
            cold_plan,
        )

        warm_operation, warm_materials = self.plans("warm", [
            entity("4", model, color=(228, 0, 228)),
            entity("5", model, color=(190, 48, 148)),
        ])
        warm_plan = build_skin_layout_plan(warm_operation, warm_materials, manifest)
        layout = warm_plan.layouts[0]

        self.assertEqual(
            [(item.render_color, item.target_skin) for item in layout.mappings],
            [
                ((86, 202, 181), 2),
                ((190, 48, 148), 3),
                ((228, 0, 228), 4),
            ],
        )
        self.assertEqual(len(layout.families), 5)
        assignments = {item.entity_id: item.target_skin for item in warm_plan.assignments}
        self.assertEqual(assignments, {"4": 4, "5": 3})

        committed = commit_skin_layout_plan(manifest, warm_operation, warm_plan)
        self.assertEqual(len(committed.skin_mappings), 3)
        self.assertEqual(
            sorted({item.map_identity for item in committed.map_usages}),
            ["maps/cold.vmf", "maps/warm.vmf"],
        )
        cache_path = self.root / "manifest.json"
        save_manifest_atomic(cache_path, committed)
        loaded = load_manifest(cache_path, self.project)
        self.assertEqual(loaded.status, "loaded")
        self.assertEqual(loaded.manifest, committed)

    def test_new_layout_rebuilds_cached_scales_from_other_maps(self) -> None:
        model = self.case["logical_model_path"]
        cold_operation, cold_materials = self.plans("cold-reconcile", [
            entity("1", model, scale="1.5", color=(190, 48, 148)),
        ])
        cold_layout = build_skin_layout_plan(
            cold_operation,
            cold_materials,
            empty_manifest(self.project),
        )
        manifest = commit_skin_layout_plan(
            empty_manifest(self.project),
            cold_operation,
            cold_layout,
        )
        old_fingerprint = cold_layout.layouts[0].layout_fingerprint
        cached_models = tuple(
            GeneratedModelRecord(
                logical_source_model=model,
                compile_scale_percent=percent,
                logical_output_model=scaled_model_path(
                    model,
                    Decimal(percent) / Decimal(100),
                ),
                requires_static_conversion=False,
                skin_layout_fingerprint=old_fingerprint,
                expected_files=(scaled_model_path(
                    model,
                    Decimal(percent) / Decimal(100),
                ),),
                artifact_fingerprint=hex_digit * 64,
            )
            for percent, hex_digit in ((150, "a"), (200, "b"))
        )
        manifest = replace(manifest, generated_models=cached_models)

        warm_operation, warm_materials = self.plans("warm-reconcile", [
            entity("2", model, scale="3", color=(86, 202, 181)),
        ])
        warm_layout = build_skin_layout_plan(
            warm_operation,
            warm_materials,
            manifest,
        )
        self.assertFalse(warm_layout.layouts[0].cache_reset)
        self.assertNotEqual(
            warm_layout.layouts[0].layout_fingerprint,
            old_fingerprint,
        )

        reconciled = reconcile_generation_requirements(
            warm_operation,
            warm_layout,
            manifest,
        )

        self.assertTrue(reconciled.is_valid)
        self.assertEqual(
            [item.compile_scale for item in reconciled.generated_models],
            [Decimal("1.5"), Decimal("2"), Decimal("3.00")],
        )
        self.assertEqual(
            [item.entity_ids for item in reconciled.generated_models],
            [(), (), ("2",)],
        )

    def test_artifact_layout_is_independent_of_entity_request_order(self) -> None:
        model = self.case["logical_model_path"]
        forward_operation, forward_materials = self.plans("forward", [
            entity("1", model, color=(190, 48, 148)),
            entity("2", model, color=(86, 202, 181)),
        ])
        reverse_operation, reverse_materials = self.plans("reverse", [
            entity("2", model, color=(86, 202, 181)),
            entity("1", model, color=(190, 48, 148)),
        ])

        forward = build_skin_layout_plan(
            forward_operation,
            forward_materials,
            empty_manifest(self.project),
        ).layouts[0]
        reverse = build_skin_layout_plan(
            reverse_operation,
            reverse_materials,
            empty_manifest(self.project),
        ).layouts[0]

        self.assertEqual(forward.families, reverse.families)
        self.assertEqual(forward.mappings, reverse.mappings)
        self.assertEqual(forward.layout_fingerprint, reverse.layout_fingerprint)

    def test_same_count_source_skin_change_invalidates_cached_mapping_layout(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("first", [
            entity("1", model, color=(190, 48, 148)),
        ])
        first_plan = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(self.project),
        )
        manifest = commit_skin_layout_plan(
            empty_manifest(self.project),
            operation,
            first_plan,
        )
        self.case["skin_families"][1] = ["body", "accent"]
        write_files(self.content, build_case_files(self.case))
        second_operation, second_materials = self.plans("second", [
            entity("2", model, color=(190, 48, 148)),
        ])
        changed = build_skin_layout_plan(second_operation, second_materials, manifest)

        self.assertIn(
            "source_skin_layout_changed",
            [item.code for item in changed.diagnostics],
        )
        self.assertEqual(changed.layouts[0].source_family_count, 2)
        self.assertEqual(len(changed.layouts[0].mappings), 1)
        self.assertEqual(changed.layouts[0].mappings[0].target_skin, 2)
        self.assertTrue(changed.layouts[0].cache_reset)
        self.assertFalse(changed.layouts[0].rebuild_cached_scales)
        committed = commit_skin_layout_plan(manifest, second_operation, changed)
        self.assertEqual(
            [(item.map_identity, item.entity_id) for item in committed.map_usages],
            [("maps/second.vmf", "2")],
        )

    def test_source_skin_count_increase_rebases_six_cached_colored_rows(self) -> None:
        model = self.case["logical_model_path"]
        self.case["skin_families"].append(["body", "accent"])
        self.case["material_files"][
            "materials/models/fixture/fallback/accent_alt.vmt"
        ] = (VMT_FIXTURES / "vertexlit_no_color.vmt").read_text(encoding="utf-8")
        write_files(self.content, build_case_files(self.case))
        colors = ((86, 202, 181), (190, 48, 148))
        cold_requests = [
            entity(str(skin * 2 + color_index + 1), model, skin=skin, color=color)
            for skin in range(3)
            for color_index, color in enumerate(colors)
        ]
        cold_operation, cold_materials = self.plans("three-skins", cold_requests)
        cold_layout = build_skin_layout_plan(
            cold_operation,
            cold_materials,
            empty_manifest(self.project),
        )
        self.assertEqual(
            [item.target_skin for item in cold_layout.layouts[0].mappings],
            list(range(3, 9)),
        )
        manifest = commit_skin_layout_plan(
            empty_manifest(self.project),
            cold_operation,
            cold_layout,
        )
        old_layout_fingerprint = cold_layout.layouts[0].layout_fingerprint
        manifest = replace(
            manifest,
            generated_models=tuple(
                GeneratedModelRecord(
                    logical_source_model=model,
                    compile_scale_percent=percent,
                    logical_output_model=scaled_model_path(
                        model,
                        Decimal(percent) / Decimal(100),
                    ),
                    requires_static_conversion=False,
                    skin_layout_fingerprint=old_layout_fingerprint,
                    expected_files=(scaled_model_path(
                        model,
                        Decimal(percent) / Decimal(100),
                    ),),
                    artifact_fingerprint=hex_digit * 64,
                )
                for percent, hex_digit in ((100, "a"), (150, "b"))
            ),
        )

        self.case["skin_families"].append(["body_alt", "accent"])
        write_files(self.content, build_case_files(self.case))
        warm_operation, warm_materials = self.plans("four-skins", [
            entity("10", model, scale="2", skin=1, color=colors[0]),
        ])
        warm_plan = build_skin_layout_plan(warm_operation, warm_materials, manifest)
        layout = warm_plan.layouts[0]

        self.assertTrue(warm_plan.is_valid)
        self.assertIn(
            "source_skin_count_increased",
            [item.code for item in warm_plan.diagnostics],
        )
        self.assertEqual(layout.source_family_count, 4)
        self.assertEqual(len(layout.families), 10)
        self.assertEqual(
            [(item.source_skin, item.render_color) for item in layout.mappings],
            [(skin, color) for skin in range(3) for color in colors],
        )
        self.assertEqual(
            [item.target_skin for item in layout.mappings],
            list(range(4, 10)),
        )
        self.assertEqual(warm_plan.assignments[0].target_skin, 6)
        self.assertTrue(layout.cache_reset)
        self.assertTrue(layout.rebuild_cached_scales)

        reconciled = reconcile_generation_requirements(
            warm_operation,
            warm_plan,
            manifest,
        )
        self.assertEqual(
            [item.compile_scale for item in reconciled.generated_models],
            [Decimal("1"), Decimal("1.5"), Decimal("2.00")],
        )
        self.assertEqual(
            [item.entity_ids for item in reconciled.generated_models],
            [(), (), ("10",)],
        )

        committed = commit_skin_layout_plan(manifest, warm_operation, warm_plan)
        self.assertEqual(
            [(item.map_identity, item.entity_id) for item in committed.map_usages],
            [("maps/four-skins.vmf", "10")],
        )
        self.assertEqual(
            [item.target_skin for item in committed.skin_mappings],
            list(range(4, 10)),
        )

    def test_committed_map_usage_contains_raw_and_compile_scale_but_no_effective_scale(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("usage", [
            entity("1", model, scale="1.095", color=(190, 48, 148)),
        ])
        plan = build_skin_layout_plan(operation, materials, empty_manifest(self.project))
        committed = commit_skin_layout_plan(
            empty_manifest(self.project),
            operation,
            plan,
        )

        usage = committed.map_usages[0]
        self.assertEqual(usage.raw_modelscale, "1.095")
        self.assertEqual(usage.compile_scale_percent, 110)
        self.assertFalse(hasattr(usage, "effective_scale"))

    def test_successful_no_op_reanalysis_clears_only_that_maps_usage(self) -> None:
        model = self.case["logical_model_path"]
        operation, materials = self.plans("usage", [
            entity("1", model, color=(190, 48, 148)),
        ])
        first_plan = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(self.project),
        )
        manifest = commit_skin_layout_plan(
            empty_manifest(self.project),
            operation,
            first_plan,
        )
        no_op, no_op_materials = self.plans("usage", [])
        no_op_plan = build_skin_layout_plan(no_op, no_op_materials, manifest)

        committed = commit_skin_layout_plan(manifest, no_op, no_op_plan)

        self.assertEqual(committed.map_usages, ())
        self.assertEqual(committed.skin_mappings, manifest.skin_mappings)


if __name__ == "__main__":
    unittest.main()
