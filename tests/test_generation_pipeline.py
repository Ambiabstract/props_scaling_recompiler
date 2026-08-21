from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from psr.assets import OrderedAssetFileSystem, parse_search_paths_text, plan_search_paths
from psr.cache import build_project_identity, empty_manifest
from psr.pipeline import (
    GenerationError,
    StagingWorkspace,
    build_colored_material_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    generate_and_validate,
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
        destination = root.joinpath(*Path(logical_path).parts)
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


SOURCE_QC = b'''$modelname "fixture/static_multi.mdl"
$staticprop
$body "body" "body.smd"
$cdmaterials "models/fixture/primary/"
$cdmaterials "models/fixture/fallback/"
$texturegroup "skinfamilies"
{
    { "body" "accent" }
    { "body_alt" "accent_alt" }
}
$collisionmodel "physics.smd"
{
    $mass 10
}
$sequence "idle" "body.smd"
$lod 40 { replacemodel "body.smd" "lod.smd" }
'''

SOURCE_DYNAMIC_QC = b'''$modelname "fixture/dynamic_v44.mdl"
$body "body" "body.smd"
$cdmaterials "models/fixture/dynamic/"
$texturegroup "skinfamilies"
{
    { "shell" }
}
$sequence "idle" "body.smd"
'''


def write_fake_crowbar(path: Path, counter: Path) -> None:
    script = f'''\
import pathlib
import sys

counter = pathlib.Path({str(counter)!r})
counter.write_text(str(int(counter.read_text() or "0") + 1))
args = sys.argv[1:]
output = pathlib.Path(args[args.index("-o") + 1])
model = pathlib.Path(args[args.index("-p") + 1])
output.mkdir(parents=True, exist_ok=True)
if model.stem == "dynamic_v44":
    (output / "dynamic_v44.qc").write_bytes({SOURCE_DYNAMIC_QC!r})
else:
    (output / "static_multi.qc").write_bytes({SOURCE_QC!r})
(output / "body.smd").write_bytes(b"body")
(output / "lod.smd").write_bytes(b"lod")
(output / "physics.smd").write_bytes(b"physics")
'''
    path.write_text(script, encoding="utf-8")


def write_fake_studiomdl(path: Path, counter: Path) -> None:
    script = f'''\
import pathlib
import re
import struct
import sys

counter = pathlib.Path({str(counter)!r})
counter.write_text(str(int(counter.read_text() or "0") + 1))
args = sys.argv[1:]
omit_sw = "--omit-sw" in args
game = pathlib.Path(args[args.index("-game") + 1])
qc = pathlib.Path(args[-1])
text = qc.read_text(encoding="ascii")
name = re.search(r'\\$modelname\\s+"([^"]+)"', text).group(1)
target = game / "models" / pathlib.PurePosixPath(name)
target.parent.mkdir(parents=True, exist_ok=True)
data = bytearray(156)
encoded = name.encode("ascii")
struct.pack_into("<4si4s64s", data, 0, b"IDST", 48, b"ABCD", encoded)
struct.pack_into("<I", data, 152, 0x10)
target.write_bytes(data)
target.with_suffix(".vvd").write_bytes(b"vvd")
target.with_suffix(".dx80.vtx").write_bytes(b"vtx80")
target.with_suffix(".dx90.vtx").write_bytes(b"vtx90")
if not omit_sw:
    target.with_suffix(".sw.vtx").write_bytes(b"vtxsw")
target.with_suffix(".phy").write_bytes(b"phy")
'''
    path.write_text(script, encoding="utf-8")


class GenerationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.content = self.root / "content"
        self.engine = self.root / "engine"
        self.engine.mkdir()
        self.staging = self.root / "staging"
        self.gameinfo = self.root / "GameInfo.txt"
        self.gameinfo.write_text(
            make_gameinfo("|gameinfo_path|content"),
            encoding="utf-8",
        )
        self.case = load_case("static_multi_material")
        self.case["material_files"]["materials/models/fixture/primary/body.vmt"] = (
            VMT_FIXTURES / "vertexlit_no_color.vmt"
        ).read_text(encoding="utf-8")
        self.case["material_files"]["materials/models/fixture/fallback/accent.vmt"] = (
            VMT_FIXTURES / "vertexlit_color2_proxy.vmt"
        ).read_text(encoding="utf-8")
        write_files(self.content, build_case_files(self.case))

        self.crowbar_counter = self.root / "crowbar-count.txt"
        self.studiomdl_counter = self.root / "studiomdl-count.txt"
        self.crowbar_counter.write_text("0")
        self.studiomdl_counter.write_text("0")
        self.crowbar = self.root / "fake_crowbar.py"
        self.studiomdl = self.root / "fake_studiomdl.py"
        write_fake_crowbar(self.crowbar, self.crowbar_counter)
        write_fake_studiomdl(self.studiomdl, self.studiomdl_counter)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def filesystem(self) -> OrderedAssetFileSystem:
        specs = parse_search_paths_text(make_gameinfo("|gameinfo_path|content"))
        plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine,
        )
        self.assertFalse(plan.diagnostics)
        return OrderedAssetFileSystem(plan.mounts)

    def plans(self):
        model = self.case["logical_model_path"]
        vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(vmf, map_identity="maps/generation.vmf")
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, inspection)
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(self.gameinfo)),
        )
        self.assertTrue(operation.is_valid)
        self.assertTrue(materials.is_valid)
        self.assertTrue(skin_layout.is_valid)
        return filesystem, operation, materials, skin_layout

    def test_generates_materials_decompiles_once_and_validates_two_models(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        staging_root: Path | None = None
        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            staging_root = workspace.root
            result = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )

            self.assertEqual(result.map_identity, operation.map_identity)
            self.assertEqual(len(result.materials), 2)
            self.assertEqual(len(result.decompilations), 1)
            self.assertEqual(len(result.qc_plan.references), 1)
            self.assertEqual(len(result.qc_plan.variants), 2)
            self.assertEqual(len(result.models), 2)
            self.assertEqual(self.crowbar_counter.read_text(), "1")
            self.assertEqual(self.studiomdl_counter.read_text(), "2")
            self.assertTrue(all(item.validation.is_static_prop for item in result.models))
            self.assertTrue(all(len(item.validation.files) == 6 for item in result.models))
            self.assertTrue(all(
                item.compile_qc.physical_path.parent
                == result.decompilations[0].qc_path.parent
                for item in result.models
            ))
            self.assertTrue(all(
                item.staged_file.relative_path.startswith(
                    "game/materials/models/psr_scaled/"
                )
                for item in result.materials
            ))
        assert staging_root is not None
        self.assertFalse(staging_root.exists())

    def test_capacity_fallback_does_not_generate_rejected_colored_materials(self) -> None:
        model = self.case["logical_model_path"]
        vmf = "".join(
            entity(str(index), model, "1.5", f"0 0 {index}")
            for index in range(1, 16)
        ).encode("ascii")
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(vmf, map_identity="maps/material-limit.vmf")
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        materials = build_colored_material_plan(
            operation,
            inspect_colored_material_sources(operation, filesystem),
        )
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(self.gameinfo)),
        )

        self.assertEqual(len(materials.colored_materials), 30)
        self.assertEqual(len(skin_layout.layouts[0].mappings), 13)
        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            result = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )

            self.assertEqual(len(result.materials), 26)
            rejected_outputs = {
                path
                for colored_skin in materials.colored_skins
                if colored_skin.render_color in {(0, 0, 14), (0, 0, 15)}
                for path in colored_skin.logical_colored_materials
            }
            self.assertTrue(rejected_outputs)
            self.assertTrue(rejected_outputs.isdisjoint(
                item.generated.logical_output_material
                for item in result.materials
            ))

    def test_missing_companion_aborts_inside_staging_and_preserves_external_files(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        sentinel = self.root / "project-owned.txt"
        sentinel.write_bytes(b"unchanged")
        staging_root: Path | None = None

        with self.assertRaises(GenerationError) as raised:
            with StagingWorkspace.create(
                self.staging,
                operation_identity=operation.map_identity,
            ) as workspace:
                staging_root = workspace.root
                generate_and_validate(
                    workspace,
                    filesystem,
                    operation,
                    materials,
                    skin_layout,
                    crowbar_command=(sys.executable, self.crowbar),
                    studiomdl_command=(sys.executable, self.studiomdl, "--omit-sw"),
                )

        self.assertEqual(raised.exception.code, "compiled_companion_missing")
        self.assertEqual(raised.exception.stage, "validate_model")
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        assert staging_root is not None
        self.assertFalse(staging_root.exists())

    def test_dynamic_scale_one_is_converted_to_validated_static_scaled_100(self) -> None:
        case = load_case("dynamic_v44")
        write_files(self.content, build_case_files(case))
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(
            entity("10", case["logical_model_path"], "1", "255 255 255").encode("ascii"),
            map_identity="maps/dynamic.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        materials = build_colored_material_plan(
            operation,
            inspect_colored_material_sources(operation, filesystem),
        )
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(self.gameinfo)),
        )

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            result = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )

            self.assertEqual(len(result.materials), 0)
            self.assertEqual(len(result.models), 1)
            model = result.models[0]
            self.assertTrue(model.requirement.requires_static_conversion)
            self.assertEqual(
                model.requirement.logical_output_model,
                "models/psr_scaled/fixture/dynamic_v44_scaled_100.mdl",
            )
            self.assertIn("insert_staticprop", result.qc_plan.references[0].mutations)
            self.assertEqual(len(model.validation.files), 5)

    def test_material_change_after_planning_aborts_before_tool_execution(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        changed = self.content / "materials/models/fixture/primary/body.vmt"
        changed.write_bytes(b'VertexLitGeneric { "$basetexture" "changed" }')

        with self.assertRaises(GenerationError) as raised:
            with StagingWorkspace.create(
                self.staging,
                operation_identity=operation.map_identity,
            ) as workspace:
                generate_and_validate(
                    workspace,
                    filesystem,
                    operation,
                    materials,
                    skin_layout,
                    crowbar_command=(sys.executable, self.crowbar),
                    studiomdl_command=(sys.executable, self.studiomdl),
                )

        self.assertEqual(raised.exception.code, "generation_material_source_changed")
        self.assertEqual(raised.exception.stage, "generate_material")
        self.assertEqual(self.crowbar_counter.read_text(), "0")

    def test_noop_plan_returns_empty_validated_result_without_starting_tools(self) -> None:
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(
            b'world\n{\n    "id" "1"\n}\n',
            map_identity="maps/noop.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        materials = build_colored_material_plan(
            operation,
            inspect_colored_material_sources(operation, filesystem),
        )
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(self.gameinfo)),
        )

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            result = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=("missing-crowbar",),
                studiomdl_command=("missing-studiomdl",),
            )

        self.assertEqual(result.materials, ())
        self.assertEqual(result.models, ())
        self.assertEqual(result.qc_plan.variants, ())
        self.assertEqual(self.crowbar_counter.read_text(), "0")
        self.assertEqual(self.studiomdl_counter.read_text(), "0")


if __name__ == "__main__":
    unittest.main()
