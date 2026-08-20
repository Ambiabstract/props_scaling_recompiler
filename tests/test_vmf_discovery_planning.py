from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

from psr.assets import (
    OrderedAssetFileSystem,
    parse_search_paths_text,
    plan_search_paths,
)
from psr.keyvalues import VmfParseError, parse_vmf
from psr.pipeline import (
    build_operation_plan,
    discover_vmf_requests,
    inspect_map_sources,
)
from tests.mdl_fixture_builder import build_case_files


FIXTURES = Path(__file__).parent / "fixtures"
VMF_FIXTURE = FIXTURES / "vmf" / "active_and_hidden_psr.vmf"
NO_PSR_FIXTURE = FIXTURES / "vmf" / "no_psr_entities.vmf"
MDL_FIXTURE = FIXTURES / "mdl" / "synthetic_mdl_cases.json"


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


def load_mdl_case(name: str) -> dict[str, Any]:
    document = json.loads(MDL_FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(next(case for case in document["cases"] if case["name"] == name))


def entity(
    entity_id: str,
    model: str,
    scale: str,
    skin: str = "0",
    color: str = "255 255 255",
) -> str:
    return f'''entity
{{
    "id" "{entity_id}"
    "classname" "prop_static_scalable"
    "model" "{model}"
    "modelscale" "{scale}"
    "skin" "{skin}"
    "rendercolor" "{color}"
}}
'''


class VmfParserTests(unittest.TestCase):
    def test_parser_preserves_source_spans_repeats_and_direct_scope(self) -> None:
        source = VMF_FIXTURE.read_bytes()
        document = parse_vmf(source)
        active = [block for block in document.blocks if block.name == b"entity"]

        self.assertEqual(document.source, source)
        self.assertEqual(len(active), 2)
        self.assertEqual(
            active[0].direct_values(b"custom_repeat"),
            (b"first", b"second"),
        )
        self.assertEqual(active[1].direct_values(b"classname"), (b"prop_static_scalable",))
        self.assertEqual(active[1].children[0].direct_values(b"classname"), (b"prop_static",))
        self.assertIs(active[1].members[1], active[1].children[0])
        self.assertEqual(source[active[0].start:active[0].end].splitlines()[0], b"entity")

    def test_quoted_braces_comments_and_bare_tokens_are_structural(self) -> None:
        source = b'entity { id 1 "note" "{ // text }" // comment\n child { key value } }'
        document = parse_vmf(source)
        block = document.blocks[0]

        self.assertEqual(block.direct_values(b"note"), (b"{ // text }",))
        self.assertEqual(block.children[0].direct_values(b"key"), (b"value",))

    def test_malformed_vmf_is_rejected(self) -> None:
        with self.assertRaises(VmfParseError):
            parse_vmf(b'entity { "id" "1"')


class DiscoveryPlanningTests(unittest.TestCase):
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

    def install_case(self, case: dict[str, Any]) -> None:
        write_files(self.content, build_case_files(case))

    def test_active_requests_link_to_sources_and_build_expected_plan(self) -> None:
        static = load_mdl_case("static_multi_material")
        static["logical_model_path"] = "models/props_fixture/already_static.mdl"
        dynamic = load_mdl_case("dynamic_v44")
        dynamic["logical_model_path"] = "models/props_fixture/dynamic.mdl"
        self.install_case(static)
        self.install_case(dynamic)

        discovery = discover_vmf_requests(
            VMF_FIXTURE.read_bytes(),
            map_identity="maps/fixture/active_and_hidden_psr.vmf",
        )
        inspected = inspect_map_sources(discovery, self.filesystem())
        plan = build_operation_plan(
            inspected,
            {"100": Decimal("1.0"), "101": Decimal("0.01")},
        )

        self.assertEqual([request.entity_id for request in discovery.requests], ["100", "101"])
        self.assertEqual(discovery.hidden_psr_entities, 1)
        self.assertEqual(discovery.requests[1].raw_modelscale, "0.001")
        self.assertEqual(len(plan.source_assets), 2)
        self.assertTrue(plan.is_valid)
        self.assertTrue(plan.requires_vmf_output)
        self.assertEqual(
            [(usage.request.entity_id, usage.operation) for usage in plan.usages],
            [("100", "reuse_original"), ("101", "generate_model")],
        )
        self.assertEqual(len(plan.generated_models), 1)
        self.assertEqual(
            plan.generated_models[0].logical_source_model,
            dynamic["logical_model_path"],
        )
        self.assertTrue(plan.generated_models[0].requires_static_conversion)
        self.assertEqual(plan.colored_skins, ())

    def test_model_and_color_requirements_are_aggregated_independently(self) -> None:
        static = load_mdl_case("static_multi_material")
        model = static["logical_model_path"]
        self.install_case(static)
        source = (
            entity("1", model, "1")
            + entity("2", model, "1", color="190 48 148")
            + entity("3", model, "2", color="190 48 148")
            + entity("4", model, "2")
        ).encode("ascii")
        discovery = discover_vmf_requests(source, map_identity="maps/colors.vmf")
        inspected = inspect_map_sources(discovery, self.filesystem())
        plan = build_operation_plan(
            inspected,
            {
                "1": Decimal("1"),
                "2": Decimal("1"),
                "3": Decimal("2"),
                "4": Decimal("2"),
            },
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(
            [(item.compile_scale, item.entity_ids) for item in plan.generated_models],
            [(Decimal("1"), ("2",)), (Decimal("2"), ("3", "4"))],
        )
        self.assertEqual(len(plan.colored_skins), 1)
        self.assertEqual(plan.colored_skins[0].entity_ids, ("2", "3"))
        self.assertEqual(plan.colored_skins[0].render_color, (190, 48, 148))
        self.assertEqual(plan.colored_skins[0].source_materials, ("body", "accent"))

    def test_invalid_inputs_are_aggregated_as_plan_diagnostics(self) -> None:
        static = load_mdl_case("static_multi_material")
        model = static["logical_model_path"]
        self.install_case(static)
        source = (
            entity("10", model, "1", skin="")
            + entity("11", model, "1", color="300 0 0")
            + entity("12", model, "1", skin="1", color="1 2 3")
            + entity("13", model, "unknown")
        ).encode("ascii")
        discovery = discover_vmf_requests(source, map_identity="maps/invalid.vmf")
        inspected = inspect_map_sources(discovery, self.filesystem())
        plan = build_operation_plan(
            inspected,
            {
                "10": Decimal("1"),
                "11": Decimal("1"),
                "12": Decimal("1"),
            },
        )

        self.assertFalse(plan.is_valid)
        self.assertEqual(
            [diagnostic.code for diagnostic in plan.diagnostics],
            [
                "invalid_skin",
                "rendercolor_out_of_range",
                "material_not_found",
                "compile_scale_unresolved",
            ],
        )
        self.assertEqual(plan.usages, ())

    def test_no_psr_entities_still_require_equivalent_vmf_output(self) -> None:
        discovery = discover_vmf_requests(
            NO_PSR_FIXTURE.read_bytes(),
            map_identity="maps/no_psr_entities.vmf",
        )
        inspected = inspect_map_sources(discovery, OrderedAssetFileSystem(()))
        plan = build_operation_plan(inspected, {})

        self.assertTrue(plan.is_valid)
        self.assertEqual(plan.usages, ())
        self.assertEqual(plan.source_assets, ())
        self.assertTrue(plan.requires_vmf_output)

    def test_duplicate_core_property_is_not_silently_collapsed(self) -> None:
        source = b'''entity
{
    "id" "1"
    "id" "2"
    "classname" "prop_static_scalable"
    "model" "models/example.mdl"
}
'''
        discovery = discover_vmf_requests(source, map_identity="maps/duplicate.vmf")

        self.assertEqual(discovery.requests, ())
        self.assertEqual(
            [diagnostic.code for diagnostic in discovery.diagnostics],
            ["duplicate_entity_property"],
        )


if __name__ == "__main__":
    unittest.main()
