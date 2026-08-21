from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srctools.vpk import VPK
from srctools.vmt import Material

from psr.assets import (
    OrderedAssetFileSystem,
    SourceMaterialInspectionError,
    colored_material_path,
    generate_colored_material,
    inspect_source_material,
    parse_search_paths_text,
    plan_search_paths,
    select_color_parameter,
)
from psr.cache import ProjectIdentity, empty_manifest
from psr.pipeline import (
    build_colored_material_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    inspect_colored_material_sources,
    inspect_map_sources,
)
from tests.mdl_fixture_builder import build_case_files


FIXTURES = Path(__file__).parent / "fixtures"
VMT_FIXTURES = FIXTURES / "vmt"
MDL_FIXTURE = FIXTURES / "mdl" / "synthetic_mdl_cases.json"


def fixture_bytes(name: str) -> bytes:
    return (VMT_FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return (VMT_FIXTURES / name).read_text(encoding="utf-8")


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


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for logical_path, data in files.items():
        destination = root / Path(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def write_vpk(path: Path, files: dict[str, bytes]) -> None:
    with VPK(path, mode="w") as archive:
        for logical_path, data in files.items():
            archive.add_file(logical_path, data, arch_index=None)


def load_mdl_case(name: str) -> dict[str, Any]:
    document = json.loads(MDL_FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(next(case for case in document["cases"] if case["name"] == name))


def entity(entity_id: str, model: str, scale: str, color: str) -> str:
    return f'''entity
{{
    "id" "{entity_id}"
    "classname" "prop_static_scalable"
    "model" "{model}"
    "modelscale" "{scale}"
    "skin" "0"
    "rendercolor" "{color}"
}}
'''


class SourceMaterialInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def filesystem(self, value: str) -> OrderedAssetFileSystem:
        specs = parse_search_paths_text(make_gameinfo(value))
        plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine_root,
        )
        self.assertFalse(plan.diagnostics)
        return OrderedAssetFileSystem(plan.mounts)

    def test_regular_vmt_and_existing_color_proxy_are_normalised(self) -> None:
        content = self.root / "content"
        files = {
            "materials/models/fixture/body.vmt": fixture_bytes("vertexlit_no_color.vmt"),
            "materials/models/fixture/accent.vmt": fixture_bytes("vertexlit_color2_proxy.vmt"),
            "materials/models/fixture/legacy_tint.vmt": fixture_bytes("vertexlit_color.vmt"),
        }
        write_files(content, files)
        filesystem = self.filesystem("|gameinfo_path|content")

        body = inspect_source_material(filesystem, "materials/models/fixture/body.vmt")
        accent = inspect_source_material(filesystem, "materials/models/fixture/accent.vmt")
        legacy_tint = inspect_source_material(
            filesystem,
            "materials/models/fixture/legacy_tint.vmt",
        )

        self.assertEqual(body.effective_shader, "VertexLitGeneric")
        self.assertEqual(select_color_parameter(body), "$color2")
        self.assertEqual(body.dependencies, ())
        self.assertEqual(body.sha256, hashlib.sha256(files[body.logical_material_path]).hexdigest())
        self.assertEqual(select_color_parameter(accent), "$color2")
        self.assertIn(("$color2", "{255 255 255}"), accent.parameters)
        self.assertEqual(len(accent.proxies), 1)
        self.assertEqual(accent.proxies[0].name, "Sine")
        self.assertEqual(accent.proxies[0].children[1].value, "$alpha")
        self.assertEqual(select_color_parameter(legacy_tint), "$color")
        self.assertIn(("$color", "[0.25 0.5 0.75]"), legacy_tint.parameters)

    def test_patch_is_expanded_and_all_dependencies_are_fingerprinted(self) -> None:
        content = self.root / "content"
        files = {
            "materials/models/fixture/source_patch.vmt": fixture_bytes("source_patch.vmt"),
            "materials/models/fixture/base_for_patch.vmt": fixture_bytes("base_for_patch.vmt"),
        }
        write_files(content, files)

        metadata = inspect_source_material(
            self.filesystem("|gameinfo_path|content"),
            "materials/models/fixture/source_patch.vmt",
        )

        self.assertTrue(metadata.is_patch)
        self.assertEqual(metadata.effective_shader, "UnlitGeneric")
        self.assertIn(("$basetexture", "models/fixture/patched"), metadata.parameters)
        self.assertIn(("$color2", "[0.5 0.5 0.5]"), metadata.parameters)
        self.assertEqual(len(metadata.proxies), 1)
        self.assertEqual(
            [item.logical_path for item in metadata.dependencies],
            ["materials/models/fixture/base_for_patch.vmt"],
        )
        self.assertEqual(metadata.dependencies[0].provenance.kind, "folder")
        self.assertNotEqual(metadata.dependency_fingerprint, metadata.sha256)

    def test_patch_inside_vpk_preserves_dependency_provenance(self) -> None:
        vpk_path = self.root / "content_dir.vpk"
        write_vpk(vpk_path, {
            "materials/models/fixture/source_patch.vmt": fixture_bytes("source_patch.vmt"),
            "materials/models/fixture/base_for_patch.vmt": fixture_bytes("base_for_patch.vmt"),
        })

        metadata = inspect_source_material(
            self.filesystem("|gameinfo_path|content.vpk"),
            "materials/models/fixture/source_patch.vmt",
        )

        self.assertEqual(metadata.provenance.kind, "vpk")
        self.assertEqual(metadata.provenance.container_path, vpk_path.resolve())
        self.assertTrue(all(item.provenance.kind == "vpk" for item in metadata.dependencies))

    def test_invalid_patch_is_a_categorised_error(self) -> None:
        content = self.root / "content"
        write_files(content, {
            "materials/models/fixture/broken.vmt": b'Patch { "include" "materials/missing.vmt" }',
        })

        with self.assertRaises(SourceMaterialInspectionError) as raised:
            inspect_source_material(
                self.filesystem("|gameinfo_path|content"),
                "materials/models/fixture/broken.vmt",
            )
        self.assertEqual(raised.exception.code, "invalid_vmt_patch")

    def test_managed_material_is_rejected_before_resolution(self) -> None:
        with self.assertRaises(SourceMaterialInspectionError) as raised:
            inspect_source_material(
                self.filesystem("|gameinfo_path|."),
                "materials/models/psr_scaled/fixture/item.vmt",
            )
        self.assertEqual(raised.exception.code, "managed_source_material")

    def test_colored_path_is_deterministic_and_collision_safe(self) -> None:
        self.assertEqual(
            colored_material_path(
                "materials/models/props_lab/cactus_sheet.vmt",
                (114, 191, 102),
            ),
            "materials/models/psr_scaled/props_lab/cactus_sheet_col_114_191_102.vmt",
        )
        self.assertEqual(
            colored_material_path("materials/shared/item.vmt", (0, 7, 255)),
            "materials/models/psr_scaled/_material_root/shared/item_col_000_007_255.vmt",
        )


class ColoredMaterialPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()
        self.content = self.root / "content"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def filesystem(self) -> OrderedAssetFileSystem:
        specs = parse_search_paths_text(make_gameinfo("|gameinfo_path|content"))
        plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine_root,
        )
        self.assertFalse(plan.diagnostics)
        return OrderedAssetFileSystem(plan.mounts)

    def operation(self, case: dict[str, Any]):
        write_files(self.content, build_case_files(case))
        source = (
            entity("1", case["logical_model_path"], "1", "190 48 148")
            + entity("2", case["logical_model_path"], "2", "190 48 148")
        ).encode("ascii")
        discovery = discover_vmf_requests(source, map_identity="maps/materials.vmf")
        inspected = inspect_map_sources(discovery, self.filesystem())
        return build_operation_plan(inspected)

    def test_plan_deduplicates_materials_across_scales_and_selects_insert_replace(self) -> None:
        case = load_mdl_case("static_multi_material")
        case["material_files"]["materials/models/fixture/primary/body.vmt"] = fixture_text(
            "vertexlit_no_color.vmt"
        )
        case["material_files"]["materials/models/fixture/fallback/accent.vmt"] = fixture_text(
            "vertexlit_color2_proxy.vmt"
        )
        operation = self.operation(case)
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        plan = build_colored_material_plan(operation, inspection)

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.source_materials), 2)
        self.assertEqual(len(plan.colored_materials), 2)
        by_source = {item.logical_source_material: item for item in plan.colored_materials}
        body = by_source["materials/models/fixture/primary/body.vmt"]
        accent = by_source["materials/models/fixture/fallback/accent.vmt"]
        self.assertEqual((body.color_parameter, body.color_assignment), ("$color2", "insert"))
        self.assertEqual((accent.color_parameter, accent.color_assignment), ("$color2", "replace"))
        self.assertEqual(body.generation_mode, "patch")
        self.assertEqual(accent.generation_mode, "patch")
        self.assertEqual(len(plan.colored_skins), 1)
        self.assertEqual(plan.colored_skins[0].material_slots, (0, 1))
        self.assertEqual(plan.colored_skins[0].entity_ids, ("1", "2"))
        self.assertEqual(
            plan.colored_skins[0].logical_colored_materials,
            (
                "materials/models/psr_scaled/fixture/primary/body_col_190_048_148.vmt",
                "materials/models/psr_scaled/fixture/fallback/accent_col_190_048_148.vmt",
            ),
        )

    def test_source_patch_uses_full_copy_until_sdk_patch_chain_is_validated(self) -> None:
        case = load_mdl_case("static_multi_material")
        body_path = "materials/models/fixture/primary/body.vmt"
        case["material_files"][body_path] = fixture_text("source_patch.vmt")
        case["material_files"]["materials/models/fixture/base_for_patch.vmt"] = fixture_text(
            "base_for_patch.vmt"
        )
        operation = self.operation(case)
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        plan = build_colored_material_plan(operation, inspection)

        body = next(
            item for item in plan.colored_materials
            if item.logical_source_material == body_path
        )
        self.assertEqual(body.generation_mode, "full_copy")
        self.assertEqual(body.color_assignment, "replace")
        self.assertEqual(
            body.generation_reason,
            "source_is_patch_pending_sdk_patch_chain_validation",
        )

    def test_unused_skin_slots_are_preserved_but_do_not_require_colored_vmt(self) -> None:
        case = load_mdl_case("static_unused_material_slot")
        operation = self.operation(case)
        requirement = operation.colored_skins[0]

        self.assertEqual(requirement.material_slots, (0,))
        self.assertEqual(requirement.source_materials, ("body",))
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        materials = build_colored_material_plan(operation, inspection)
        self.assertTrue(materials.is_valid)
        self.assertEqual(len(materials.colored_materials), 1)
        self.assertEqual(materials.colored_skins[0].material_slots, (0,))

        project = ProjectIdentity("a" * 64, "C:/fixture/GameInfo.txt", "b" * 64)
        layout = build_skin_layout_plan(operation, materials, empty_manifest(project))
        self.assertTrue(layout.is_valid)
        self.assertEqual(
            layout.layouts[0].families[-1],
            (
                "models/psr_scaled/fixture/unused/body_col_190_048_148",
                "unused_accent",
            ),
        )

    def test_unsupported_shader_is_an_explicit_planning_error(self) -> None:
        case = load_mdl_case("static_multi_material")
        case["material_files"]["materials/models/fixture/primary/body.vmt"] = fixture_text(
            "unsupported_shader.vmt"
        )
        operation = self.operation(case)
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        plan = build_colored_material_plan(operation, inspection)

        self.assertFalse(plan.is_valid)
        self.assertIn("unsupported_color_shader", [item.code for item in plan.diagnostics])
        self.assertEqual(plan.colored_skins, ())

    def test_planned_patch_is_rendered_deterministically_and_reparseable(self) -> None:
        case = load_mdl_case("static_multi_material")
        case["material_files"]["materials/models/fixture/primary/body.vmt"] = fixture_text(
            "vertexlit_no_color.vmt"
        )
        case["material_files"]["materials/models/fixture/fallback/accent.vmt"] = fixture_text(
            "vertexlit_color2_proxy.vmt"
        )
        operation = self.operation(case)
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        plan = build_colored_material_plan(operation, inspection)
        planned = next(
            item for item in plan.colored_materials
            if item.logical_source_material == "materials/models/fixture/primary/body.vmt"
        )
        metadata = next(
            item for item in inspection.source_materials
            if item.logical_material_path == planned.logical_source_material
        )

        first = generate_colored_material(
            metadata,
            logical_output_material=planned.logical_output_material,
            render_color=planned.render_color,
            color_parameter=planned.color_parameter,
            color_assignment=planned.color_assignment,
            generation_mode=planned.generation_mode,
        )
        second = generate_colored_material(
            metadata,
            logical_output_material=planned.logical_output_material,
            render_color=planned.render_color,
            color_parameter=planned.color_parameter,
            color_assignment=planned.color_assignment,
            generation_mode=planned.generation_mode,
        )
        parsed = Material.parse(
            first.content.decode("utf-8").splitlines(keepends=True),
            first.logical_output_material,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.sha256, hashlib.sha256(first.content).hexdigest())
        self.assertEqual(parsed.shader, "Patch")
        self.assertEqual(parsed["include"], metadata.logical_material_path)
        self.assertEqual(parsed.blocks[0]["$color2"], "{190 48 148}")

        replace_plan = next(
            item for item in plan.colored_materials
            if item.logical_source_material == "materials/models/fixture/fallback/accent.vmt"
        )
        replace_source = next(
            item for item in inspection.source_materials
            if item.logical_material_path == replace_plan.logical_source_material
        )
        replacement = generate_colored_material(
            replace_source,
            logical_output_material=replace_plan.logical_output_material,
            render_color=replace_plan.render_color,
            color_parameter=replace_plan.color_parameter,
            color_assignment=replace_plan.color_assignment,
            generation_mode=replace_plan.generation_mode,
        )
        parsed_replacement = Material.parse(
            replacement.content.decode("utf-8").splitlines(keepends=True),
            replacement.logical_output_material,
        )
        self.assertEqual(parsed_replacement.blocks[0].name, "replace")
        self.assertEqual(parsed_replacement.blocks[0]["$color2"], "{190 48 148}")

    def test_source_patch_full_copy_preserves_effective_proxy_and_overrides_color(self) -> None:
        case = load_mdl_case("static_multi_material")
        body_path = "materials/models/fixture/primary/body.vmt"
        case["material_files"][body_path] = fixture_text("source_patch.vmt")
        case["material_files"]["materials/models/fixture/base_for_patch.vmt"] = fixture_text(
            "base_for_patch.vmt"
        )
        operation = self.operation(case)
        inspection = inspect_colored_material_sources(operation, self.filesystem())
        plan = build_colored_material_plan(operation, inspection)
        planned = next(
            item for item in plan.colored_materials
            if item.logical_source_material == body_path
        )
        metadata = next(
            item for item in inspection.source_materials
            if item.logical_material_path == body_path
        )

        generated = generate_colored_material(
            metadata,
            logical_output_material=planned.logical_output_material,
            render_color=planned.render_color,
            color_parameter=planned.color_parameter,
            color_assignment=planned.color_assignment,
            generation_mode=planned.generation_mode,
        )
        parsed = Material.parse(
            generated.content.decode("utf-8").splitlines(keepends=True),
            generated.logical_output_material,
        )

        self.assertEqual(parsed.shader, "UnlitGeneric")
        self.assertEqual(parsed["$basetexture"], "models/fixture/patched")
        self.assertEqual(parsed["$color2"], "{190 48 148}")
        self.assertEqual(len(parsed.proxies), 1)
        self.assertEqual(parsed.proxies[0].real_name, "TextureScroll")


if __name__ == "__main__":
    unittest.main()
