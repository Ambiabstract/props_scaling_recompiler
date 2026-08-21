from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srctools.vpk import VPK

from psr.assets import (
    OrderedAssetFileSystem,
    SourceAssetInspectionError,
    inspect_source_model,
    parse_search_paths_text,
    plan_search_paths,
)
from tests.mdl_fixture_builder import build_case_files, build_mdl


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "mdl"
    / "synthetic_mdl_cases.json"
)


def load_cases() -> dict[str, dict[str, Any]]:
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if document["schema_version"] != 1:
        raise AssertionError("unsupported synthetic MDL fixture schema")
    return {case["name"]: case for case in document["cases"]}


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


def write_folder_files(root: Path, files: dict[str, bytes]) -> None:
    for logical_path, data in files.items():
        destination = root / Path(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def write_vpk(path: Path, files: dict[str, bytes]) -> None:
    with VPK(path, mode="w") as archive:
        for logical_path, data in files.items():
            archive.add_file(logical_path, data, arch_index=None)


class SourceModelInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()
        self.cases = load_cases()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def filesystem(self, search_path: str) -> OrderedAssetFileSystem:
        specs = parse_search_paths_text(make_gameinfo(search_path))
        plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine_root,
        )
        self.assertFalse(plan.diagnostics)
        return OrderedAssetFileSystem(plan.mounts)

    def test_folder_model_produces_normalised_deterministic_metadata(self) -> None:
        case = self.cases["static_multi_material"]
        files = build_case_files(case)
        content = self.root / "content"
        write_folder_files(content, files)

        metadata = inspect_source_model(
            self.filesystem("|gameinfo_path|content"),
            r"MODELS\FIXTURE\STATIC_MULTI.MDL",
        )

        self.assertEqual(metadata.logical_model_path, case["logical_model_path"])
        self.assertEqual(metadata.model_provenance.kind, "folder")
        self.assertEqual(metadata.internal_model_name, case["internal_model_name"])
        self.assertEqual(metadata.mdl_version, 48)
        self.assertEqual(metadata.mdl_header_checksum, "11223344")
        self.assertTrue(metadata.is_static_prop)
        self.assertEqual(metadata.bone_count, 1)
        self.assertEqual(metadata.mdl_flags, 16)
        self.assertEqual(metadata.surface_property, "default")
        self.assertEqual(metadata.total_vertices, 0)
        self.assertEqual(
            metadata.cdmaterials,
            ("models/fixture/primary/", "models/fixture/fallback/", ""),
        )
        self.assertEqual(
            metadata.skin_families,
            (("body", "accent"), ("body_alt", "accent_alt")),
        )
        self.assertEqual(
            metadata.material_names,
            ("body", "accent", "body_alt", "accent_alt"),
        )
        self.assertEqual(
            [(material.material_name, material.logical_path) for material in metadata.materials],
            [
                ("body", "materials/models/fixture/primary/body.vmt"),
                ("accent", "materials/models/fixture/fallback/accent.vmt"),
                ("body_alt", "materials/models/fixture/primary/body_alt.vmt"),
                ("accent_alt", None),
            ],
        )
        self.assertEqual(
            [file.logical_path for file in metadata.files],
            [
                "models/fixture/static_multi.mdl",
                "models/fixture/static_multi.phy",
                "models/fixture/static_multi.vvd",
                "models/fixture/static_multi.dx90.vtx",
            ],
        )
        self.assertEqual(metadata.files[0].size, len(files[case["logical_model_path"]]))
        self.assertEqual(
            metadata.files[0].sha256,
            hashlib.sha256(files[case["logical_model_path"]]).hexdigest(),
        )
        self.assertTrue(metadata.has_physics)

    def test_vpk_model_preserves_vpk_provenance(self) -> None:
        case = self.cases["dynamic_v44"]
        vpk_path = self.root / "content_dir.vpk"
        write_vpk(vpk_path, build_case_files(case))

        metadata = inspect_source_model(
            self.filesystem("|gameinfo_path|content.vpk"),
            case["logical_model_path"],
        )

        self.assertEqual(metadata.mdl_version, 44)
        self.assertFalse(metadata.is_static_prop)
        self.assertEqual(metadata.bone_count, 3)
        self.assertEqual(metadata.skin_families, (("shell",),))
        self.assertFalse(metadata.has_physics)
        self.assertTrue(all(file.provenance.kind == "vpk" for file in metadata.files))
        self.assertEqual(metadata.materials[0].provenance.kind, "vpk")
        self.assertEqual(metadata.model_provenance.container_path, vpk_path.resolve())

    def test_corrupt_texture_offset_is_a_categorised_error(self) -> None:
        case = self.cases["dynamic_v44"]
        corrupt = bytearray(build_mdl(case))
        struct.pack_into("<i", corrupt, 208, len(corrupt) + 4096)
        files = build_case_files(case)
        files[case["logical_model_path"]] = bytes(corrupt)
        content = self.root / "content"
        write_folder_files(content, files)

        with self.assertRaises(SourceAssetInspectionError) as raised:
            inspect_source_model(
                self.filesystem("|gameinfo_path|content"),
                case["logical_model_path"],
            )

        self.assertEqual(raised.exception.code, "invalid_mdl")
        self.assertEqual(raised.exception.logical_path, case["logical_model_path"])

    def test_managed_output_is_rejected_before_resolution(self) -> None:
        with self.assertRaises(SourceAssetInspectionError) as raised:
            inspect_source_model(
                self.filesystem("|gameinfo_path|."),
                "models/psr_scaled/fixture/item.mdl",
            )
        self.assertEqual(raised.exception.code, "managed_source_asset")

    def test_unsafe_logical_path_is_a_categorised_error(self) -> None:
        with self.assertRaises(SourceAssetInspectionError) as raised:
            inspect_source_model(
                self.filesystem("|gameinfo_path|."),
                "models/../outside.mdl",
            )
        self.assertEqual(raised.exception.code, "invalid_model_path")


if __name__ == "__main__":
    unittest.main()
