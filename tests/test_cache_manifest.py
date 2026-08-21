from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from psr.cache import (
    SCHEMA_VERSION,
    ColoredMaterialRecord,
    GeneratedModelRecord,
    MapUsageRecord,
    ProjectManifest,
    SkinMappingRecord,
    SourceAssetRecord,
    build_project_identity,
    empty_manifest,
    load_manifest,
    migrate_manifest_document,
    manifest_to_document,
    save_manifest_atomic,
)


FIXTURES = Path(__file__).parent / "fixtures" / "cache"


class ProjectIdentityTests(unittest.TestCase):
    def test_identity_is_project_path_scoped_while_content_hash_is_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first" / "GameInfo.txt"
            second = root / "second" / "GameInfo.txt"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("GameInfo {}\n", encoding="utf-8")
            second.write_text("GameInfo {}\n", encoding="utf-8")

            first_identity = build_project_identity(first)
            second_identity = build_project_identity(second)

            self.assertNotEqual(first_identity.project_id, second_identity.project_id)
            self.assertEqual(first_identity.gameinfo_sha256, second_identity.gameinfo_sha256)

            first.write_text("GameInfo { FileSystem {} }\n", encoding="utf-8")
            modified_identity = build_project_identity(first)
            self.assertEqual(first_identity.project_id, modified_identity.project_id)
            self.assertNotEqual(first_identity.gameinfo_sha256, modified_identity.gameinfo_sha256)


class ManifestStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.gameinfo = self.root / "GameInfo.txt"
        self.gameinfo.write_text("GameInfo {}\n", encoding="utf-8")
        self.project = build_project_identity(self.gameinfo)
        self.path = self.root / "cache" / "manifest.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_cache_returns_empty_project_manifest(self) -> None:
        result = load_manifest(self.path, self.project)

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.manifest, empty_manifest(self.project))

    def test_atomic_round_trip_is_canonical_and_contains_separate_tables(self) -> None:
        manifest = empty_manifest(self.project)
        save_manifest_atomic(self.path, manifest)
        first_bytes = self.path.read_bytes()
        save_manifest_atomic(self.path, manifest)

        self.assertEqual(first_bytes, self.path.read_bytes())
        self.assertTrue(first_bytes.endswith(b"\n"))
        document = json.loads(first_bytes)
        self.assertEqual(document["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            set(document),
            {
                "schema_version",
                "project",
                "source_assets",
                "generated_models",
                "colored_materials",
                "skin_mappings",
                "map_usages",
            },
        )
        result = load_manifest(self.path, self.project)
        self.assertEqual(result.status, "loaded")
        self.assertEqual(result.manifest, manifest)

    def test_non_empty_records_round_trip_without_collapsing_table_identities(self) -> None:
        manifest = ProjectManifest(
            schema_version=SCHEMA_VERSION,
            project=self.project,
            source_assets=(SourceAssetRecord(
                logical_model_path="models/fixture/item.mdl",
                source_fingerprint="1" * 64,
                skin_families_fingerprint="2" * 64,
            ),),
            generated_models=(GeneratedModelRecord(
                logical_source_model="models/fixture/item.mdl",
                compile_scale_percent=150,
                logical_output_model="models/psr_scaled/fixture/item_scaled_150.mdl",
                requires_static_conversion=True,
                skin_layout_fingerprint="3" * 64,
                expected_files=(
                    "models/psr_scaled/fixture/item_scaled_150.mdl",
                    "models/psr_scaled/fixture/item_scaled_150.vvd",
                ),
                artifact_fingerprint="4" * 64,
            ),),
            colored_materials=(ColoredMaterialRecord(
                logical_source_material="materials/models/fixture/item.vmt",
                render_color=(1, 2, 3),
                color_parameter="$color2",
                generation_mode="patch",
                logical_output_material=(
                    "materials/models/psr_scaled/fixture/item_col_001_002_003.vmt"
                ),
                source_fingerprint="5" * 64,
                artifact_sha256="6" * 64,
            ),),
            skin_mappings=(SkinMappingRecord(
                logical_source_model="models/fixture/item.mdl",
                source_skin=0,
                render_color=(1, 2, 3),
                target_skin=1,
                source_skin_families_fingerprint="2" * 64,
                layout_fingerprint="3" * 64,
            ),),
            map_usages=(MapUsageRecord(
                map_identity="maps/fixture.vmf",
                entity_id="10",
                logical_source_model="models/fixture/item.mdl",
                raw_modelscale="1.50",
                compile_scale_percent=150,
                source_skin=0,
                render_color=(1, 2, 3),
                logical_output_model="models/psr_scaled/fixture/item_scaled_150.mdl",
                target_skin=1,
            ),),
        )

        save_manifest_atomic(self.path, manifest)
        loaded = load_manifest(self.path, self.project)

        self.assertEqual(loaded.status, "loaded")
        self.assertEqual(loaded.manifest, manifest)
        self.assertNotIn("effective_scale", self.path.read_text(encoding="utf-8"))

    def test_corrupt_cache_recovers_without_breaking_the_build(self) -> None:
        self.path.parent.mkdir()
        self.path.write_bytes((FIXTURES / "corrupt.json").read_bytes())

        result = load_manifest(self.path, self.project)

        self.assertEqual(result.status, "corrupt")
        self.assertEqual(result.manifest, empty_manifest(self.project))
        self.assertIn("JSON", result.detail)

    def test_wrong_json_value_types_are_recovered_as_corrupt(self) -> None:
        manifest = ProjectManifest(
            schema_version=SCHEMA_VERSION,
            project=self.project,
            source_assets=(),
            generated_models=(),
            colored_materials=(),
            skin_mappings=(SkinMappingRecord(
                logical_source_model="models/fixture/item.mdl",
                source_skin=0,
                render_color=(1, 2, 3),
                target_skin=1,
                source_skin_families_fingerprint="c" * 64,
                layout_fingerprint="d" * 64,
            ),),
            map_usages=(),
        )
        document = manifest_to_document(manifest)
        document["skin_mappings"][0]["render_color"] = ["bad", 2, 3]
        self.path.parent.mkdir()
        self.path.write_text(json.dumps(document), encoding="utf-8")

        result = load_manifest(self.path, self.project)

        self.assertEqual(result.status, "corrupt")
        self.assertEqual(result.manifest, empty_manifest(self.project))

    def test_newer_schema_and_other_project_are_never_merged(self) -> None:
        self.path.parent.mkdir()
        newer = {
            "schema_version": SCHEMA_VERSION + 1,
            "project": {
                "project_id": self.project.project_id,
                "normalized_gameinfo_path": self.project.normalized_gameinfo_path,
                "gameinfo_sha256": self.project.gameinfo_sha256,
            },
        }
        self.path.write_text(json.dumps(newer), encoding="utf-8")
        incompatible = load_manifest(self.path, self.project)
        self.assertEqual(incompatible.status, "incompatible")

        other_gameinfo = self.root / "other" / "GameInfo.txt"
        other_gameinfo.parent.mkdir()
        other_gameinfo.write_text("GameInfo {}\n", encoding="utf-8")
        save_manifest_atomic(self.path, empty_manifest(build_project_identity(other_gameinfo)))
        mismatch = load_manifest(self.path, self.project)
        self.assertEqual(mismatch.status, "project_mismatch")
        self.assertEqual(mismatch.manifest.project, self.project)

    def test_schema_v0_migrates_to_current_explicit_tables(self) -> None:
        document = json.loads((FIXTURES / "schema_v0.json").read_text(encoding="utf-8"))
        migrated = migrate_manifest_document(document)

        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(migrated["source_assets"], [])
        self.assertEqual(migrated["generated_models"], [])
        self.assertEqual(migrated["colored_materials"], [])
        self.assertEqual(migrated["map_usages"], [])
        self.assertNotIn("final_skin_index", migrated["skin_mappings"][0])
        self.assertEqual(migrated["skin_mappings"][0]["target_skin"], 1)

    def test_failed_replace_preserves_previous_manifest_and_removes_temp_file(self) -> None:
        save_manifest_atomic(self.path, empty_manifest(self.project))
        original = self.path.read_bytes()
        manifest = ProjectManifest(
            schema_version=SCHEMA_VERSION,
            project=self.project,
            source_assets=(),
            generated_models=(),
            colored_materials=(),
            skin_mappings=(SkinMappingRecord(
                logical_source_model="models/fixture/item.mdl",
                source_skin=0,
                render_color=(1, 2, 3),
                target_skin=1,
                source_skin_families_fingerprint="c" * 64,
                layout_fingerprint="d" * 64,
            ),),
            map_usages=(),
        )

        with mock.patch("psr.cache.manifest.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                save_manifest_atomic(self.path, manifest)

        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
