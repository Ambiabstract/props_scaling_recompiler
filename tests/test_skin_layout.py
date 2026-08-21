from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from psr.assets import OrderedAssetFileSystem, parse_search_paths_text, plan_search_paths
from psr.cache import (
    build_project_identity,
    empty_manifest,
    load_manifest,
    save_manifest_atomic,
)
from psr.pipeline import (
    build_colored_material_plan,
    build_operation_plan,
    build_skin_layout_plan,
    commit_skin_layout_plan,
    discover_vmf_requests,
    inspect_colored_material_sources,
    inspect_map_sources,
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
        assignments = {item.entity_id: item.target_skin for item in plan.assignments}
        self.assertEqual(assignments, {"1": 3, "2": 2, "3": 1})

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

    def test_changed_source_skin_table_invalidates_cached_mapping_layout(self) -> None:
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
        second_operation, second_materials = self.plans("second", [
            entity("2", model, color=(190, 48, 148)),
        ])
        asset = second_operation.source_assets[0]
        changed_asset = replace(
            asset,
            skin_families=asset.skin_families + (("body", "accent"),) * 2,
        )
        changed_operation = replace(second_operation, source_assets=(changed_asset,))

        changed = build_skin_layout_plan(changed_operation, second_materials, manifest)

        self.assertIn(
            "source_skin_layout_changed",
            [item.code for item in changed.diagnostics],
        )
        self.assertEqual(changed.layouts[0].source_family_count, 4)
        self.assertEqual(len(changed.layouts[0].mappings), 1)
        self.assertEqual(changed.layouts[0].mappings[0].target_skin, 4)
        self.assertTrue(changed.layouts[0].cache_reset)
        committed = commit_skin_layout_plan(manifest, changed_operation, changed)
        self.assertEqual(
            [(item.map_identity, item.entity_id) for item in committed.map_usages],
            [("maps/second.vmf", "2")],
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
