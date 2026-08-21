from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from psr.pipeline import CommitError, recover_interrupted_commit


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CommitRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.manifest = self.root / "state" / "manifest.json"
        self.vmf = self.root / "maps" / "output.vmf"
        self.journal = self.root / "state" / "recovery.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_journal(self, writes: list[dict[str, object]]) -> None:
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal.write_text(json.dumps({
            "schema_version": 1,
            "status": "installing",
            "writes": writes,
        }), encoding="utf-8")

    def record(
        self,
        target: Path,
        temporary: Path,
        backup: Path | None,
        *,
        original_existed: bool,
        installed_content: bytes,
    ) -> dict[str, object]:
        return {
            "target": str(target.resolve()),
            "temporary": str(temporary.resolve()),
            "backup": None if backup is None else str(backup.resolve()),
            "original_existed": original_existed,
            "sha256": sha256(installed_content),
        }

    def test_recovery_restores_backups_and_removes_new_targets(self) -> None:
        model = self.game / "models/psr_scaled/fixture/model_scaled_150.mdl"
        material = self.game / "materials/models/psr_scaled/fixture/color.vmt"
        model.parent.mkdir(parents=True)
        material.parent.mkdir(parents=True)
        self.manifest.parent.mkdir(parents=True)
        self.vmf.parent.mkdir(parents=True)

        model_backup = model.with_name(f".{model.name}.token.psr-backup")
        manifest_backup = self.manifest.with_name(
            f".{self.manifest.name}.token.psr-backup"
        )
        model.write_bytes(b"new-model")
        model_backup.write_bytes(b"old-model")
        material.write_bytes(b"new-material")
        self.manifest.write_bytes(b"new-manifest")
        manifest_backup.write_bytes(b"old-manifest")
        self.vmf.write_bytes(b"old-vmf")
        model_temp = model.with_name(f".{model.name}.left.psr-new")
        material_temp = material.with_name(f".{material.name}.left.psr-new")
        manifest_temp = self.manifest.with_name(
            f".{self.manifest.name}.left.psr-new"
        )
        vmf_temp = self.vmf.with_name(f".{self.vmf.name}.left.psr-new")
        for temporary in (model_temp, material_temp, manifest_temp, vmf_temp):
            temporary.write_bytes(b"temporary")

        self.write_journal([
            self.record(
                model,
                model_temp,
                model_backup,
                original_existed=True,
                installed_content=b"new-model",
            ),
            self.record(
                material,
                material_temp,
                None,
                original_existed=False,
                installed_content=b"new-material",
            ),
            self.record(
                self.manifest,
                manifest_temp,
                manifest_backup,
                original_existed=True,
                installed_content=b"new-manifest",
            ),
            self.record(
                self.vmf,
                vmf_temp,
                self.vmf.with_name(f".{self.vmf.name}.token.psr-backup"),
                original_existed=True,
                installed_content=b"new-vmf",
            ),
        ])

        result = recover_interrupted_commit(
            self.journal,
            game_directory=self.game,
            manifest_path=self.manifest,
            vmf_output_path=self.vmf,
        )

        self.assertTrue(result.recovered)
        self.assertEqual(model.read_bytes(), b"old-model")
        self.assertFalse(material.exists())
        self.assertEqual(self.manifest.read_bytes(), b"old-manifest")
        self.assertEqual(self.vmf.read_bytes(), b"old-vmf")
        self.assertFalse(self.journal.exists())
        self.assertFalse(any(self.root.rglob("*.psr-new")))
        self.assertFalse(any(self.root.rglob("*.psr-backup")))

    def test_tampered_journal_cannot_touch_unmanaged_file(self) -> None:
        outside = self.root / "user-owned.txt"
        outside.write_bytes(b"keep")
        temporary = self.root / ".user-owned.txt.left.psr-new"
        temporary.write_bytes(b"temporary")
        self.write_journal([
            self.record(
                outside,
                temporary,
                None,
                original_existed=False,
                installed_content=b"keep",
            ),
        ])

        with self.assertRaises(CommitError) as raised:
            recover_interrupted_commit(
                self.journal,
                game_directory=self.game,
                manifest_path=self.manifest,
                vmf_output_path=self.vmf,
            )

        self.assertEqual(raised.exception.code, "commit_recovery_target_unmanaged")
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertTrue(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
