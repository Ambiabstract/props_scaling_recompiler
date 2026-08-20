from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from srctools.vpk import VPK

from psr.assets import (
    OrderedAssetFileSystem,
    normalize_logical_path,
    parse_search_paths_text,
    plan_search_paths,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "gameinfo"
    / "ordered_searchpaths.txt"
)


def make_gameinfo(*values: str) -> str:
    leaves = "\n".join(f'            game "{value}"' for value in values)
    return f'''GameInfo
{{
    FileSystem
    {{
        SearchPaths
        {{
{leaves}
        }}
    }}
}}
'''


def make_vpk(
    path: Path,
    files: dict[str, bytes],
    *,
    arch_index: int | None = None,
) -> None:
    with VPK(path, mode="w") as archive:
        for logical_path, data in files.items():
            archive.add_file(logical_path, data, arch_index=arch_index)


class SearchPathParsingTests(unittest.TestCase):
    def test_fixture_preserves_duplicate_keys_and_source_order(self) -> None:
        specs = parse_search_paths_text(
            FIXTURE_PATH.read_text(encoding="utf-8"),
            filename=str(FIXTURE_PATH),
        )
        self.assertEqual([spec.ordinal for spec in specs], list(range(6)))
        self.assertEqual(
            [spec.path_id for spec in specs],
            [
                "game+mod+mod_write+default_write_path",
                "game",
                "game",
                "game",
                "game",
                "game",
            ],
        )
        self.assertEqual(
            [spec.raw_value for spec in specs],
            [
                "|gameinfo_path|.",
                "|gameinfo_path|custom/*",
                "|gameinfo_path|fixture_dir.vpk",
                "|all_source_engine_paths|hl2/hl2_textures.vpk",
                "ep2/ep2_pak.vpk",
                ".",
            ],
        )

    def test_logical_path_normalisation_is_exact_and_safe(self) -> None:
        self.assertEqual(
            normalize_logical_path(r"Models\Props\Thing.MDL"),
            "models/props/thing.mdl",
        )
        with self.assertRaises(ValueError):
            normalize_logical_path("models/../outside.mdl")


class OrderedAssetFileSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build_filesystem(self, *search_paths: str) -> tuple[OrderedAssetFileSystem, object]:
        specs = parse_search_paths_text(make_gameinfo(*search_paths))
        plan = plan_search_paths(
            specs,
            gameinfo_dir=self.root,
            engine_root=self.engine_root,
        )
        return OrderedAssetFileSystem(plan.mounts), plan

    def test_first_folder_match_wins_over_later_vpk(self) -> None:
        folder = self.root / "folder"
        asset = folder / "models" / "shared" / "item.mdl"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"folder")
        make_vpk(
            self.root / "content_dir.vpk",
            {"models/shared/item.mdl": b"vpk"},
        )

        filesystem, plan = self.build_filesystem(
            "|gameinfo_path|folder",
            "|gameinfo_path|content.vpk",
        )
        resolved = filesystem.resolve("models/shared/item.mdl")

        self.assertEqual(resolved.read_bytes(), b"folder")
        self.assertEqual(resolved.provenance.kind, "folder")
        self.assertEqual(resolved.provenance.source_ordinal, 0)
        self.assertEqual(resolved.provenance.container_path, folder.resolve())
        self.assertEqual(len(plan.mounts), 2)

    def test_first_vpk_match_wins_over_later_folder(self) -> None:
        folder = self.root / "folder"
        asset = folder / "models" / "shared" / "item.mdl"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"folder")
        vpk_path = self.root / "content_dir.vpk"
        make_vpk(vpk_path, {"models/shared/item.mdl": b"vpk"})

        filesystem, _ = self.build_filesystem(
            "|gameinfo_path|content.vpk",
            "|gameinfo_path|folder",
        )
        resolved = filesystem.resolve(r"MODELS\SHARED\ITEM.MDL")

        self.assertEqual(resolved.read_bytes(), b"vpk")
        self.assertEqual(resolved.provenance.kind, "vpk")
        self.assertEqual(resolved.provenance.source_ordinal, 0)
        self.assertEqual(resolved.provenance.container_path, vpk_path.resolve())

    def test_vpk_lookup_never_falls_back_to_a_matching_basename(self) -> None:
        make_vpk(
            self.root / "content_dir.vpk",
            {"models/first/shared.mdl": b"first"},
        )
        filesystem, _ = self.build_filesystem("|gameinfo_path|content.vpk")

        with self.assertRaises(FileNotFoundError):
            filesystem.resolve("models/second/shared.mdl")
        self.assertEqual(
            filesystem.resolve("models/first/shared.mdl").read_bytes(),
            b"first",
        )

    def test_wildcard_expansion_is_deterministic(self) -> None:
        custom = self.root / "custom"
        alpha = custom / "Alpha"
        zeta = custom / "zeta"
        for folder, data in [(zeta, b"zeta"), (alpha, b"alpha")]:
            asset = folder / "models" / "ordered.mdl"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(data)

        filesystem, plan = self.build_filesystem("|gameinfo_path|custom/*")
        resolved = filesystem.resolve("models/ordered.mdl")

        self.assertEqual(resolved.read_bytes(), b"alpha")
        self.assertEqual(
            [mount.container_path.name for mount in plan.mounts],
            ["Alpha", "zeta"],
        )
        self.assertEqual(resolved.provenance.expansion_index, 0)

    def test_engine_token_bare_relative_path_and_dot_use_engine_root(self) -> None:
        hl2_vpk = self.engine_root / "hl2" / "textures_dir.vpk"
        ep2_vpk = self.engine_root / "ep2" / "pak_dir.vpk"
        hl2_vpk.parent.mkdir()
        ep2_vpk.parent.mkdir()
        make_vpk(hl2_vpk, {"materials/hl2.vmt": b"hl2"})
        make_vpk(ep2_vpk, {"models/ep2.mdl": b"ep2"})

        filesystem, plan = self.build_filesystem(
            "|all_source_engine_paths|hl2/textures.vpk",
            "ep2/pak.vpk",
            ".",
        )

        self.assertEqual(
            [(mount.kind, mount.container_path) for mount in plan.mounts],
            [
                ("vpk", hl2_vpk.resolve()),
                ("vpk", ep2_vpk.resolve()),
                ("folder", self.engine_root.resolve()),
            ],
        )
        self.assertEqual(
            filesystem.resolve("materials/hl2.vmt").read_bytes(),
            b"hl2",
        )
        self.assertEqual(
            filesystem.resolve("models/ep2.mdl").read_bytes(),
            b"ep2",
        )

    def test_wildcard_mounts_directory_vpk_but_not_numbered_chunk(self) -> None:
        packs = self.root / "packs"
        packs.mkdir()
        make_vpk(
            packs / "archive_dir.vpk",
            {"models/from_chunk.mdl": b"chunk" * 300},
            arch_index=0,
        )

        filesystem, plan = self.build_filesystem("|gameinfo_path|packs/*")

        self.assertEqual(
            [(mount.kind, mount.container_path.name) for mount in plan.mounts],
            [("vpk", "archive_dir.vpk")],
        )
        self.assertIn(
            "numbered_vpk_chunk",
            [diagnostic.reason for diagnostic in plan.diagnostics],
        )
        self.assertEqual(
            filesystem.resolve("models/from_chunk.mdl").read_bytes(),
            b"chunk" * 300,
        )

    def test_missing_optional_path_is_a_plan_diagnostic(self) -> None:
        filesystem, plan = self.build_filesystem(
            "|gameinfo_path|missing_optional.vpk"
        )

        self.assertEqual(filesystem.mounts, ())
        self.assertEqual(len(plan.diagnostics), 1)
        self.assertEqual(plan.diagnostics[0].reason, "searchpath_missing")


if __name__ == "__main__":
    unittest.main()
