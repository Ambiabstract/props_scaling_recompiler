from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from psr.assets import parse_gameinfo_search_paths, plan_search_paths
from psr.cache import build_project_identity, load_manifest
from psr.keyvalues import parse_vmf
from psr.pipeline import discover_vmf_requests
from psr.runtime import CompileRequest, DiagnosticReport, execute_compile_run

try:
    import pytest
except ImportError:  # pragma: no cover - unittest-only environment
    pytestmark = ()
else:
    pytestmark = pytest.mark.external_sdk


RUN_EXTERNAL_RUNTIME = os.environ.get("PSR_RUN_EXTERNAL_RUNTIME") == "1"
SDK_ROOT = Path(os.environ.get(
    "PSR_SDK_ROOT",
    r"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer",
))
ANTENNA_ROOT = Path(os.environ.get(
    "PSR_ANTENNA_ROOT",
    r"C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013",
))
MAP_NAME = "aa_models_color_tint_test_01a.vmf"
SOURCE_MAP = ANTENNA_ROOT / "maps" / MAP_NAME
STUDIOMDL = SDK_ROOT / "bin" / "studiomdl.exe"
CROWBAR = SDK_ROOT / "bin" / "CrowbarCommandLineDecomp.exe"
EXPECTED_MAP_SHA256 = (
    "18aede35a65477a3cecd00b6e063de3e5807f5fb7388dd77c37f80958f57b69d"
)
EXPECTED_STUDIOMDL_SHA256 = (
    "e6c4ea7477b8ce31de878ff53ca640cb222c4978f3ba33c4715de3de1c7a6416"
)
EXPECTED_CROWBAR_SHA256 = (
    "4b5fc8f5092448c1f8fe12f6849bf8ee3996406f02109ec90ab800c6cf145b2a"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _isolated_gameinfo() -> bytes:
    source_gameinfo = ANTENNA_ROOT / "GameInfo.txt"
    plan = plan_search_paths(
        parse_gameinfo_search_paths(source_gameinfo),
        gameinfo_dir=ANTENNA_ROOT,
        engine_root=SDK_ROOT,
    )
    mounts = "\n".join(
        f'            game "{mount.container_path.resolve().as_posix()}"'
        for mount in plan.mounts
    )
    return f'''"GameInfo"
{{
    game "PSR isolated production runtime"
    type singleplayer_only
    FileSystem
    {{
        SteamAppId 243730
        SearchPaths
        {{
            game+mod+mod_write+default_write_path "|gameinfo_path|."
{mounts}
        }}
    }}
}}
'''.encode("utf-8")


def _tree_snapshot(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size, _sha256(path))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


@unittest.skipUnless(
    RUN_EXTERNAL_RUNTIME,
    "set PSR_RUN_EXTERNAL_RUNTIME=1 for the isolated production runtime test",
)
class ExternalProductionRuntimeTests(unittest.TestCase):
    def test_priority_color_map_cold_then_toolless_warm_run(self) -> None:
        for path in (SOURCE_MAP, CROWBAR, STUDIOMDL):
            self.assertTrue(path.is_file(), path)
        self.assertEqual(_sha256(SOURCE_MAP), EXPECTED_MAP_SHA256)
        self.assertEqual(_sha256(CROWBAR), EXPECTED_CROWBAR_SHA256)
        self.assertEqual(_sha256(STUDIOMDL), EXPECTED_STUDIOMDL_SHA256)
        source = SOURCE_MAP.read_bytes()
        parse_vmf(source)
        source_discovery = discover_vmf_requests(
            source,
            map_identity=f"maps/{MAP_NAME}",
        )
        self.assertEqual(len(source_discovery.requests), 27)

        antenna_models_before = _tree_snapshot(ANTENNA_ROOT / "models/psr_scaled")
        antenna_materials_before = _tree_snapshot(
            ANTENNA_ROOT / "materials/models/psr_scaled"
        )

        with tempfile.TemporaryDirectory(prefix="psr-external-runtime-") as temporary:
            root = Path(temporary)
            game = root / "game"
            maps = game / "maps"
            maps.mkdir(parents=True)
            gameinfo = game / "GameInfo.txt"
            gameinfo.write_bytes(_isolated_gameinfo())
            vmf_input = maps / MAP_NAME
            vmf_output = maps / "psr_temp" / MAP_NAME
            vmf_input.write_bytes(source)
            local_appdata = root / "localappdata"
            cold_report = DiagnosticReport()

            cold_started = time.perf_counter()
            cold = execute_compile_run(
                CompileRequest(
                    game_directory=game,
                    vmf_input_path=vmf_input,
                    vmf_output_path=vmf_output,
                    engine_root=SDK_ROOT,
                    crowbar_command=(CROWBAR,),
                    studiomdl_command=(STUDIOMDL,),
                    local_appdata=local_appdata,
                ),
                cold_report,
            )
            cold_seconds = time.perf_counter() - cold_started

            self.assertTrue(cold.success, cold_report.render())
            self.assertFalse(cold_report.has_errors, cold_report.render())
            self.assertEqual(cold.active_entities, 27)
            self.assertEqual(cold.generated_models, 17)
            self.assertEqual(cold.reused_models, 0)
            self.assertEqual(cold.generated_materials, 8)
            self.assertEqual(cold.reused_materials, 0)
            cold_output = vmf_output.read_bytes()
            output_document = parse_vmf(cold_output)
            output_entities = {
                block.direct_values(b"id")[0].decode("ascii"): block
                for block in output_document.blocks
                if block.name.lower() == b"entity" and block.direct_values(b"id")
            }
            for request in source_discovery.requests:
                block = output_entities[request.entity_id]
                self.assertEqual(block.direct_values(b"classname"), (b"prop_static",))
                self.assertEqual(block.direct_values(b"modelscale"), ())
                self.assertEqual(block.direct_values(b"rendercolor"), ())
                self.assertEqual(block.direct_values(b"convert_prop_to_static"), ())
                self.assertEqual(len(block.direct_values(b"skin")), 1)
            self.assertFalse(discover_vmf_requests(
                cold_output,
                map_identity=f"maps/psr_temp/{MAP_NAME}",
            ).requests)

            project = build_project_identity(gameinfo)
            loaded = load_manifest(cold.state.manifest, project)
            self.assertEqual(loaded.status, "loaded")
            self.assertEqual(len(loaded.manifest.generated_models), 17)
            self.assertEqual(len(loaded.manifest.colored_materials), 8)
            expected_artifacts = sum(
                len(item.expected_files)
                for item in loaded.manifest.generated_models
            ) + len(loaded.manifest.colored_materials)
            self.assertEqual(cold.published_files, expected_artifacts)
            manifest_before_warm = cold.state.manifest.read_bytes()
            warm_report = DiagnosticReport()

            warm_started = time.perf_counter()
            warm = execute_compile_run(
                CompileRequest(
                    game_directory=game,
                    vmf_input_path=vmf_input,
                    vmf_output_path=vmf_output,
                    engine_root=SDK_ROOT,
                    crowbar_command=None,
                    studiomdl_command=None,
                    local_appdata=local_appdata,
                ),
                warm_report,
            )
            warm_seconds = time.perf_counter() - warm_started

            self.assertTrue(warm.success, warm_report.render())
            self.assertFalse(warm_report.has_errors, warm_report.render())
            self.assertEqual(warm.generated_models, 0)
            self.assertEqual(warm.reused_models, 17)
            self.assertEqual(warm.generated_materials, 0)
            self.assertEqual(warm.reused_materials, 8)
            self.assertEqual(warm.published_files, 0)
            self.assertEqual(vmf_output.read_bytes(), cold_output)
            self.assertEqual(cold.state.manifest.read_bytes(), manifest_before_warm)
            self.assertLess(warm_seconds, cold_seconds)
            self.assertEqual(list(cold.state.staging.iterdir()), [])

            print("PSR_EXTERNAL_RUNTIME_METRICS=" + json.dumps({
                "active_entities": cold.active_entities,
                "cold_seconds": round(cold_seconds, 3),
                "generated_materials": cold.generated_materials,
                "generated_models": cold.generated_models,
                "published_files": cold.published_files,
                "warm_seconds": round(warm_seconds, 3),
            }, sort_keys=True))

        self.assertEqual(_sha256(SOURCE_MAP), EXPECTED_MAP_SHA256)
        self.assertEqual(
            _tree_snapshot(ANTENNA_ROOT / "models/psr_scaled"),
            antenna_models_before,
        )
        self.assertEqual(
            _tree_snapshot(ANTENNA_ROOT / "materials/models/psr_scaled"),
            antenna_materials_before,
        )


if __name__ == "__main__":
    unittest.main()
