from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from psr.assets import (
    OrderedAssetFileSystem,
    inspect_qc,
    parse_search_paths_text,
    plan_search_paths,
)
from psr.cache import (
    GeneratedModelRecord,
    build_project_identity,
    empty_manifest,
    load_manifest,
)
from psr.pipeline import (
    CommitError,
    GenerationError,
    StagingWorkspace,
    apply_commit_plan,
    build_colored_material_plan,
    build_commit_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    generate_and_validate,
    inspect_colored_material_sources,
    inspect_map_sources,
    plan_artifact_reuse,
    reconcile_generation_requirements,
)
from psr.runtime import CompileRequest, DiagnosticReport, execute_compile_run
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
$bodygroup "body"
{
    studio "body.smd"
}
$cdmaterials "models/fixture/dynamic/"
$texturegroup "skinfamilies"
{
    { "shell" }
}
$sequence "idle" "body.smd"
'''

SOURCE_DYNAMIC_BLANK_QC = SOURCE_DYNAMIC_QC.replace(
    b'$modelname "fixture/dynamic_v44.mdl"\n',
    b'$modelname "fixture/dynamic_blank.mdl"\n'
    b'$bodygroup "optional"\n{\n    blank\n    studio "body.smd"\n}\n',
)

SOURCE_REFERENCE_SMD = b'''version 1
nodes
  0 "root" -1
end
skeleton
  time 0
    0 0 0 0 0 0 0
end
triangles
fixture/shell
  0 0 0 0 0 0 1 0 0 1 0 1
  0 1 0 0 0 0 1 1 0 1 0 1
  0 0 1 0 0 0 1 0 1 1 0 1
end
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
elif model.stem == "dynamic_blank":
    (output / "dynamic_blank.qc").write_bytes({SOURCE_DYNAMIC_BLANK_QC!r})
else:
    (output / "static_multi.qc").write_bytes({SOURCE_QC!r})
(output / "body.smd").write_bytes({SOURCE_REFERENCE_SMD!r})
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
struct.pack_into("<I", data, 152, 0x10 if "$staticprop" in text else 0)
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

    def test_validated_generation_commits_assets_manifest_and_vmf_together(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        project = build_project_identity(self.gameinfo)
        manifest = empty_manifest(project)
        game_output = self.root / "project-game"
        game_output.mkdir()
        cache_path = self.root / "cache" / "manifest.json"
        vmf_output_path = self.root / "maps" / "generation_out.vmf"

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            generation = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )
            commit_plan = build_commit_plan(
                source_vmf,
                manifest,
                operation,
                materials,
                skin_layout,
                generation,
            )
            result = apply_commit_plan(
                commit_plan,
                game_directory=game_output,
                manifest_path=cache_path,
                vmf_output_path=vmf_output_path,
            )

        self.assertEqual(len(result.published_artifacts), 14)
        self.assertTrue(all(path.is_file() for path in result.published_artifacts))
        loaded = load_manifest(cache_path, project)
        self.assertEqual(loaded.status, "loaded")
        self.assertEqual(len(loaded.manifest.generated_models), 2)
        self.assertEqual(len(loaded.manifest.colored_materials), 2)
        self.assertEqual(len(loaded.manifest.skin_mappings), 1)
        self.assertEqual(len(loaded.manifest.map_usages), 2)
        output = vmf_output_path.read_bytes()
        self.assertEqual(output.count(b'"classname" "prop_static"'), 2)
        self.assertNotIn(b'"modelscale"', output)
        self.assertNotIn(b'"rendercolor"', output)
        self.assertIn(b'"skin" "2"', output)
        self.assertIn(b'"skin" "0"', output)
        self.assertFalse((self.content / "models/psr_scaled").exists())
        self.assertFalse((self.content / "materials/models/psr_scaled").exists())

    def test_changed_staged_artifact_aborts_commit_before_project_writes(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        game_output = self.root / "project-game"
        game_output.mkdir()
        cache_path = self.root / "cache.json"
        vmf_output_path = self.root / "output.vmf"

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            generation = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )
            generation.models[0].validation.files[0].physical_path.write_bytes(b"changed")

            with self.assertRaises(CommitError) as raised:
                build_commit_plan(
                    source_vmf,
                    empty_manifest(build_project_identity(self.gameinfo)),
                    operation,
                    materials,
                    skin_layout,
                    generation,
                )

        self.assertEqual(raised.exception.code, "commit_artifact_changed")
        self.assertEqual(list(game_output.rglob("*")), [])
        self.assertFalse(cache_path.exists())
        self.assertFalse(vmf_output_path.exists())

    def test_mid_transaction_failure_restores_every_previous_target(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        game_output = self.root / "project-game"
        game_output.mkdir()
        cache_path = self.root / "cache.json"
        vmf_output_path = self.root / "output.vmf"
        cache_path.write_bytes(b"old-cache")
        vmf_output_path.write_bytes(b"old-vmf")

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            generation = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )
            commit_plan = build_commit_plan(
                source_vmf,
                empty_manifest(build_project_identity(self.gameinfo)),
                operation,
                materials,
                skin_layout,
                generation,
            )
            first = commit_plan.artifacts[0]
            old_artifact = game_output.joinpath(
                *Path(first.logical_path).parts
            )
            old_artifact.parent.mkdir(parents=True, exist_ok=True)
            old_artifact.write_bytes(b"old-artifact")
            real_replace = os.replace

            def fail_vmf_install(source: Path, destination: Path) -> None:
                if (
                    Path(destination).resolve() == vmf_output_path.resolve()
                    and str(source).endswith(".psr-new")
                ):
                    raise OSError("synthetic VMF install failure")
                real_replace(source, destination)

            with patch("psr.pipeline.commit._replace_path", side_effect=fail_vmf_install):
                with self.assertRaises(CommitError) as raised:
                    apply_commit_plan(
                        commit_plan,
                        game_directory=game_output,
                        manifest_path=cache_path,
                        vmf_output_path=vmf_output_path,
                    )

        self.assertEqual(raised.exception.code, "commit_transaction_failed")
        self.assertEqual(old_artifact.read_bytes(), b"old-artifact")
        self.assertEqual(cache_path.read_bytes(), b"old-cache")
        self.assertEqual(vmf_output_path.read_bytes(), b"old-vmf")
        remaining_files = {
            path.resolve()
            for path in game_output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(remaining_files, {old_artifact.resolve()})
        self.assertFalse(any(self.root.rglob("*.psr-new")))
        self.assertFalse(any(self.root.rglob("*.psr-backup")))

    def test_commit_rejects_cached_scale_omitted_from_required_reconciliation(self) -> None:
        filesystem, operation, materials, skin_layout = self.plans()
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        manifest = empty_manifest(build_project_identity(self.gameinfo))
        manifest = replace(manifest, generated_models=(GeneratedModelRecord(
            logical_source_model=model,
            compile_scale_percent=300,
            logical_output_model=(
                "models/psr_scaled/fixture/static_multi_scaled_300.mdl"
            ),
            requires_static_conversion=False,
            skin_layout_fingerprint="a" * 64,
            expected_files=(
                "models/psr_scaled/fixture/static_multi_scaled_300.mdl",
            ),
            artifact_fingerprint="b" * 64,
        ),))

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            generation = generate_and_validate(
                workspace,
                filesystem,
                operation,
                materials,
                skin_layout,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            )

            with self.assertRaises(CommitError) as raised:
                build_commit_plan(
                    source_vmf,
                    manifest,
                    operation,
                    materials,
                    skin_layout,
                    generation,
                )

        self.assertEqual(raised.exception.code, "commit_reconciliation_incomplete")

    def test_runtime_coordinator_executes_full_compile_run_with_local_appdata_state(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "runtime.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "runtime.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        report = DiagnosticReport()

        result = execute_compile_run(
            CompileRequest(
                game_directory=self.root,
                vmf_input_path=vmf_input,
                vmf_output_path=vmf_output,
                engine_root=self.engine,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
                local_appdata=self.root / "localappdata",
            ),
            report,
        )

        self.assertTrue(result.success)
        self.assertFalse(report.has_errors)
        self.assertEqual(result.active_entities, 2)
        self.assertEqual(result.generated_models, 2)
        self.assertEqual(result.generated_materials, 2)
        self.assertEqual(result.published_files, 14)
        self.assertTrue(vmf_output.is_file())
        self.assertTrue(result.state.manifest.is_file())
        self.assertIn(
            "PropsScalingRecompiler/projects",
            result.state.root.as_posix(),
        )
        self.assertFalse(result.state.recovery_journal.exists())
        self.assertEqual(list(result.state.staging.iterdir()), [])

    def test_runtime_warm_cache_reuses_every_asset_without_external_tools(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "warm.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "warm.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        common = {
            "game_directory": self.root,
            "vmf_input_path": vmf_input,
            "vmf_output_path": vmf_output,
            "engine_root": self.engine,
            "local_appdata": self.root / "localappdata",
        }

        cold = execute_compile_run(
            CompileRequest(
                **common,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            ),
            DiagnosticReport(),
        )
        warm_report = DiagnosticReport()
        warm = execute_compile_run(
            CompileRequest(
                **common,
                crowbar_command=None,
                studiomdl_command=None,
            ),
            warm_report,
        )

        self.assertTrue(cold.success)
        self.assertTrue(warm.success)
        self.assertFalse(warm_report.has_errors)
        self.assertEqual(warm.generated_models, 0)
        self.assertEqual(warm.reused_models, 2)
        self.assertEqual(warm.generated_materials, 0)
        self.assertEqual(warm.reused_materials, 2)
        self.assertEqual(warm.published_files, 0)
        self.assertEqual(self.crowbar_counter.read_text(), "1")
        self.assertEqual(self.studiomdl_counter.read_text(), "2")

    def test_runtime_warm_cache_rebuilds_only_corrupt_model_variant(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "repair_model.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "repair_model.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        request = CompileRequest(
            game_directory=self.root,
            vmf_input_path=vmf_input,
            vmf_output_path=vmf_output,
            engine_root=self.engine,
            crowbar_command=(sys.executable, self.crowbar),
            studiomdl_command=(sys.executable, self.studiomdl),
            local_appdata=self.root / "localappdata",
        )
        cold = execute_compile_run(request, DiagnosticReport())
        loaded = load_manifest(cold.state.manifest, build_project_identity(self.gameinfo))
        damaged = next(
            item
            for item in loaded.manifest.generated_models
            if item.compile_scale_percent == 150
        )
        companion = next(
            path for path in damaged.expected_files if path.endswith(".vvd")
        )
        self.root.joinpath(*Path(companion).parts).write_bytes(b"corrupt")
        report = DiagnosticReport()

        repaired = execute_compile_run(request, report)

        self.assertTrue(repaired.success)
        self.assertEqual(repaired.generated_models, 1)
        self.assertEqual(repaired.reused_models, 1)
        self.assertEqual(repaired.generated_materials, 0)
        self.assertEqual(repaired.reused_materials, 2)
        self.assertEqual(repaired.published_files, 6)
        self.assertEqual(self.crowbar_counter.read_text(), "2")
        self.assertEqual(self.studiomdl_counter.read_text(), "3")
        self.assertTrue(any(
            item.code == "cached_model_artifact_invalid"
            for item in report.entries
        ))

    def test_runtime_warm_cache_regenerates_only_missing_material(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "repair_material.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "repair_material.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        common = {
            "game_directory": self.root,
            "vmf_input_path": vmf_input,
            "vmf_output_path": vmf_output,
            "engine_root": self.engine,
            "local_appdata": self.root / "localappdata",
        }
        cold = execute_compile_run(
            CompileRequest(
                **common,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
            ),
            DiagnosticReport(),
        )
        loaded = load_manifest(cold.state.manifest, build_project_identity(self.gameinfo))
        missing = loaded.manifest.colored_materials[0].logical_output_material
        self.root.joinpath(*Path(missing).parts).unlink()
        report = DiagnosticReport()

        repaired = execute_compile_run(
            CompileRequest(
                **common,
                crowbar_command=None,
                studiomdl_command=None,
            ),
            report,
        )

        self.assertTrue(repaired.success)
        self.assertEqual(repaired.generated_models, 0)
        self.assertEqual(repaired.reused_models, 2)
        self.assertEqual(repaired.generated_materials, 1)
        self.assertEqual(repaired.reused_materials, 1)
        self.assertEqual(repaired.published_files, 1)
        self.assertEqual(self.crowbar_counter.read_text(), "1")
        self.assertEqual(self.studiomdl_counter.read_text(), "2")
        self.assertTrue(any(
            item.code == "cached_material_artifact_invalid"
            for item in report.entries
        ))

    def test_runtime_source_change_invalidates_every_cached_scale(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "source_change.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "source_change.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        request = CompileRequest(
            game_directory=self.root,
            vmf_input_path=vmf_input,
            vmf_output_path=vmf_output,
            engine_root=self.engine,
            crowbar_command=(sys.executable, self.crowbar),
            studiomdl_command=(sys.executable, self.studiomdl),
            local_appdata=self.root / "localappdata",
        )
        cold = execute_compile_run(request, DiagnosticReport())
        self.assertTrue(cold.success)
        source_model = self.content.joinpath(*Path(model).parts)
        source_model.write_bytes(source_model.read_bytes() + b"source-revision")

        rebuilt = execute_compile_run(request, DiagnosticReport())

        self.assertTrue(rebuilt.success)
        self.assertEqual(rebuilt.generated_models, 2)
        self.assertEqual(rebuilt.reused_models, 0)
        self.assertEqual(rebuilt.generated_materials, 0)
        self.assertEqual(rebuilt.reused_materials, 2)
        self.assertEqual(rebuilt.published_files, 12)
        self.assertEqual(self.crowbar_counter.read_text(), "2")
        self.assertEqual(self.studiomdl_counter.read_text(), "4")

    def test_reused_artifact_changed_after_planning_aborts_commit(self) -> None:
        model = self.case["logical_model_path"]
        source_vmf = (
            entity("1", model, "1.5", "190 48 148")
            + entity("2", model, "2", "255 255 255")
        ).encode("ascii")
        vmf_input = self.root / "maps" / "reuse_race.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "reuse_race.vmf"
        vmf_input.parent.mkdir()
        vmf_input.write_bytes(source_vmf)
        cold = execute_compile_run(
            CompileRequest(
                game_directory=self.root,
                vmf_input_path=vmf_input,
                vmf_output_path=vmf_output,
                engine_root=self.engine,
                crowbar_command=(sys.executable, self.crowbar),
                studiomdl_command=(sys.executable, self.studiomdl),
                local_appdata=self.root / "localappdata",
            ),
            DiagnosticReport(),
        )
        project = build_project_identity(self.gameinfo)
        manifest = load_manifest(cold.state.manifest, project).manifest
        filesystem = self.filesystem()
        discovery = discover_vmf_requests(
            source_vmf,
            map_identity="maps/reuse_race.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        materials = build_colored_material_plan(
            operation,
            inspect_colored_material_sources(operation, filesystem),
        )
        skin_layout = build_skin_layout_plan(operation, materials, manifest)
        operation = reconcile_generation_requirements(
            operation,
            skin_layout,
            manifest,
        )
        reuse = plan_artifact_reuse(
            self.root,
            manifest,
            operation,
            materials,
            skin_layout,
        )
        self.assertEqual(len(reuse.reused_models), 2)

        with StagingWorkspace.create(
            self.staging,
            operation_identity=operation.map_identity,
        ) as workspace:
            generation = generate_and_validate(
                workspace,
                filesystem,
                reuse.generation_operation,
                reuse.generation_materials,
                skin_layout,
                crowbar_command=("unused-crowbar",),
                studiomdl_command=("unused-studiomdl",),
            )
            commit_plan = build_commit_plan(
                source_vmf,
                manifest,
                operation,
                materials,
                skin_layout,
                generation,
                reuse,
            )
            changed = reuse.reused_models[0].files[0].physical_path
            changed.write_bytes(b"changed-after-plan")

            with self.assertRaises(CommitError) as raised:
                apply_commit_plan(
                    commit_plan,
                    game_directory=self.root,
                    manifest_path=cold.state.manifest,
                    vmf_output_path=vmf_output,
                )

        self.assertEqual(raised.exception.code, "commit_existing_artifact_changed")

    def test_runtime_noop_writes_equivalent_vmf_without_external_tools(self) -> None:
        vmf_input = self.root / "maps" / "noop.vmf"
        vmf_output = self.root / "maps" / "psr_temp" / "noop.vmf"
        vmf_input.parent.mkdir()
        source = b'world\n{\n    "id" "1"\n}\n'
        vmf_input.write_bytes(source)
        report = DiagnosticReport()

        result = execute_compile_run(
            CompileRequest(
                game_directory=self.root,
                vmf_input_path=vmf_input,
                vmf_output_path=vmf_output,
                engine_root=self.engine,
                crowbar_command=None,
                studiomdl_command=None,
                local_appdata=self.root / "localappdata",
            ),
            report,
        )

        self.assertTrue(result.success)
        self.assertEqual(vmf_output.read_bytes(), source)
        self.assertEqual(result.published_files, 0)

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
            self.assertNotIn("replace_bodygroup_blanks", result.qc_plan.references[0].mutations)
            self.assertEqual(len(model.validation.files), 5)

    def test_empty_bodygroup_model_bypasses_all_asset_generation(self) -> None:
        case = load_case("dynamic_v44")
        case["logical_model_path"] = "models/fixture/dynamic_blank.mdl"
        case["internal_model_name"] = "fixture/dynamic_blank.mdl"
        case["bodyparts"] = [[[0]], [[], [0]]]
        write_files(self.content, build_case_files(case))
        filesystem = self.filesystem()
        operation = build_operation_plan(inspect_map_sources(
            discover_vmf_requests(
                entity("11", case["logical_model_path"], "1", "255 255 255").encode("ascii"),
                map_identity="maps/dynamic_blank.vmf",
            ),
            filesystem,
        ))
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
                crowbar_command=("must-not-run-crowbar",),
                studiomdl_command=("must-not-run-studiomdl",),
            )

            self.assertEqual(operation.usages[0].operation, "reuse_dynamic")
            self.assertEqual(operation.generated_models, ())
            self.assertEqual(operation.colored_skins, ())
            self.assertEqual(materials.colored_materials, ())
            self.assertEqual(materials.colored_skins, ())
            self.assertEqual(skin_layout.layouts, ())
            self.assertEqual(skin_layout.assignments[0].target_skin, 0)
            self.assertEqual(result.models, ())
            self.assertEqual(result.materials, ())
            self.assertEqual(result.qc_plan.references, ())
            self.assertEqual(result.qc_plan.variants, ())

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
