from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from psr.cache import (
    ColoredMaterialRecord,
    GeneratedModelRecord,
    ProjectIdentity,
    ProjectManifest,
    SCHEMA_VERSION,
)
from psr.pipeline import PipelineDiagnostic
from psr.runtime import (
    DiagnosticReport,
    ProjectCacheSummary,
    ProjectLock,
    ProjectLockError,
    ProgressReporter,
    build_project_cache_summary,
    build_project_state_paths,
    perform_debug_cleanup,
)
from psr.runtime.reporting import _enable_console_color


class ProjectStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = ProjectIdentity("a" * 64, "c:/project/gameinfo.txt", "b" * 64)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_state_is_project_scoped_under_local_appdata(self) -> None:
        paths = build_project_state_paths(
            self.project,
            local_appdata=self.root,
        )

        self.assertEqual(
            paths.root,
            self.root.resolve()
            / "PropsScalingRecompiler"
            / "projects"
            / ("a" * 64),
        )
        self.assertEqual(paths.manifest.parent, paths.root)
        self.assertEqual(paths.lock.parent, paths.root)
        self.assertEqual(paths.recovery_journal.parent, paths.root)
        self.assertIn("project", paths.logs.name)
        self.assertIn("a" * 8, paths.logs.name)
        self.assertEqual(
            paths.staging,
            self.root.resolve()
            / "PropsScalingRecompiler"
            / "work"
            / ("a" * 16),
        )
        self.assertFalse(paths.root.exists())

        paths.ensure_directories()

        self.assertTrue(paths.logs.is_dir())
        self.assertTrue(paths.staging.is_dir())
        self.assertTrue(paths.failed_runs.is_dir())

    def test_second_project_lock_is_rejected_and_release_is_automatic(self) -> None:
        paths = build_project_state_paths(self.project, local_appdata=self.root)
        first = ProjectLock(paths.lock, map_identity="maps/first.vmf")
        second = ProjectLock(paths.lock, map_identity="maps/second.vmf")

        with first:
            with self.assertRaises(ProjectLockError) as raised:
                second.acquire()
            self.assertEqual(raised.exception.code, "project_locked")
            self.assertIn("maps/first.vmf", raised.exception.detail)

        with second:
            self.assertTrue(paths.lock.is_file())


class DiagnosticReportTests(unittest.TestCase):
    def test_report_deduplicates_and_groups_all_severities(self) -> None:
        report = DiagnosticReport()
        warning = PipelineDiagnostic(
            "warning",
            "rounded",
            "scale rounded",
            entity_id="12",
            source_line=8,
        )
        report.extend_pipeline((warning, warning))
        report.add("error", "failed", "compile failed")
        report.add("error", "failed", "compile failed", entity_id="12")
        report.add("error", "failed", "compile failed", entity_id="14")
        report.add("recommendation", "retry", "inspect the retained staging directory")

        rendered = report.render(color=False)

        self.assertTrue(report.has_errors)
        self.assertEqual(rendered.count("[rounded]"), 1)
        self.assertEqual(rendered.count("[failed]"), 1)
        self.assertIn("entities 12, 14", rendered)
        self.assertLess(rendered.index("ERRORS"), rendered.index("WARNINGS"))
        self.assertLess(rendered.index("WARNINGS"), rendered.index("INFO"))
        stream = StringIO()
        report.print(stream)
        self.assertEqual(stream.getvalue(), rendered)

    def test_windows_console_color_enables_virtual_terminal_processing(self) -> None:
        class TtyStream(StringIO):
            def isatty(self) -> bool:
                return True

        class UInt32:
            def __init__(self) -> None:
                self.value = 0

        enabled_modes: list[int] = []

        def get_console_mode(_handle: int, mode: UInt32) -> int:
            mode.value = 0x0001
            return 1

        def set_console_mode(_handle: int, mode: int) -> int:
            enabled_modes.append(mode)
            return 1

        fake_ctypes = SimpleNamespace(
            windll=SimpleNamespace(kernel32=SimpleNamespace(
                GetStdHandle=lambda _identifier: 123,
                GetConsoleMode=get_console_mode,
                SetConsoleMode=set_console_mode,
            )),
            c_uint32=UInt32,
            byref=lambda value: value,
        )

        with patch("psr.runtime.reporting.os.name", "nt"), patch.dict(
            "sys.modules", {"ctypes": fake_ctypes}
        ):
            enabled = _enable_console_color(TtyStream())

        self.assertTrue(enabled)
        self.assertEqual(enabled_modes, [0x0001 | 0x0004])

    def test_progress_reporter_emits_each_completed_batch_immediately(self) -> None:
        stream = StringIO()
        reporter = ProgressReporter(stream, heartbeat_seconds=60.0)

        reporter.start("Compiling models", total=4, unit="tasks")
        reporter.update(1, detail="models/fixture/one_scaled_150.mdl")
        first_update = stream.getvalue()
        reporter.update(4, detail="models/fixture/four_scaled_200.mdl")
        reporter.finish()

        output = stream.getvalue()
        self.assertIn("[PROGRESS] Compiling models", output)
        self.assertIn("1/4 tasks (25%)", first_update)
        self.assertIn("current: models/fixture/one_scaled_150.mdl", first_update)
        self.assertIn("ETA", first_update)
        self.assertIn("4/4 tasks (100%)", output)


class ProjectSummaryAndCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.local = self.root / "local"
        self.game = self.root / "game"
        self.game.mkdir()
        self.project = ProjectIdentity("a" * 64, "c:/project/gameinfo.txt", "b" * 64)
        self.state = build_project_state_paths(self.project, local_appdata=self.local)
        self.state.ensure_directories()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_project_summary_counts_cached_variations_and_actual_managed_bytes(self) -> None:
        model = self.game / "models/psr_scaled/item_scaled_150.mdl"
        material = self.game / "materials/models/psr_scaled/item_col_001_002_003.vmt"
        model.parent.mkdir(parents=True)
        material.parent.mkdir(parents=True)
        model.write_bytes(b"model")
        material.write_bytes(b"material")
        manifest = ProjectManifest(
            schema_version=SCHEMA_VERSION,
            project=self.project,
            source_assets=(),
            generated_models=(GeneratedModelRecord(
                logical_source_model="models/item.mdl",
                compile_scale_percent=150,
                logical_output_model="models/psr_scaled/item_scaled_150.mdl",
                requires_static_conversion=False,
                skin_layout_fingerprint="1" * 64,
                expected_files=(
                    "models/psr_scaled/item_scaled_150.mdl",
                    "models/psr_scaled/item_scaled_150.vvd",
                ),
                artifact_fingerprint="2" * 64,
            ),),
            colored_materials=(ColoredMaterialRecord(
                logical_source_material="materials/models/item.vmt",
                render_color=(1, 2, 3),
                color_parameter="$color2",
                generation_mode="patch",
                logical_output_material=(
                    "materials/models/psr_scaled/item_col_001_002_003.vmt"
                ),
                source_fingerprint="3" * 64,
                artifact_sha256="4" * 64,
            ),),
            skin_mappings=(),
            map_usages=(),
        )

        summary = build_project_cache_summary(self.game, manifest)

        self.assertEqual(summary, ProjectCacheSummary(
            source_models=0,
            model_variations=1,
            material_variations=1,
            skin_variations=0,
            maps=0,
            entity_usages=0,
            managed_files=2,
            managed_bytes=13,
            missing_files=1,
        ))

    def test_cleanup_mode_one_only_resets_current_project_cache_and_managed_roots(self) -> None:
        original = self.game / "models/original.mdl"
        generated_model = self.game / "models/psr_scaled/item.mdl"
        generated_material = self.game / "materials/models/psr_scaled/item.vmt"
        original.parent.mkdir(parents=True, exist_ok=True)
        generated_model.parent.mkdir(parents=True)
        generated_material.parent.mkdir(parents=True)
        original.write_bytes(b"keep")
        generated_model.write_bytes(b"delete")
        generated_material.write_bytes(b"delete")
        self.state.manifest.write_text("{}", encoding="utf-8")
        self.state.recovery_journal.write_text("{}", encoding="utf-8")
        work_file = self.state.staging / "old" / "temp.txt"
        work_file.parent.mkdir(parents=True)
        work_file.write_text("keep in mode one", encoding="utf-8")

        result = perform_debug_cleanup(1, game_directory=self.game, state=self.state)

        self.assertFalse(generated_model.parent.exists())
        self.assertFalse(generated_material.parent.exists())
        self.assertFalse(self.state.manifest.exists())
        self.assertFalse(self.state.recovery_journal.exists())
        self.assertTrue(original.exists())
        self.assertTrue(work_file.exists())
        self.assertGreaterEqual(result.removed_files, 4)

    def test_cleanup_mode_two_resets_all_project_caches_and_psr_temporary_files(self) -> None:
        other = self.local / "PropsScalingRecompiler/projects" / ("c" * 64)
        other.mkdir(parents=True)
        (other / "manifest.json").write_text("{}", encoding="utf-8")
        (other / "manifest.corrupt.old.json").write_text("{}", encoding="utf-8")
        (other / "logs").mkdir()
        (other / "logs/keep.log").write_text("log", encoding="utf-8")
        self.state.manifest.write_text("{}", encoding="utf-8")
        failed = self.state.failed_runs / "old" / "artifact.bin"
        failed.parent.mkdir(parents=True)
        failed.write_bytes(b"temp")
        work = self.state.staging / "old" / "artifact.bin"
        work.parent.mkdir(parents=True)
        work.write_bytes(b"temp")

        perform_debug_cleanup(2, game_directory=self.game, state=self.state)

        self.assertFalse(self.state.manifest.exists())
        self.assertFalse((other / "manifest.json").exists())
        self.assertFalse((other / "manifest.corrupt.old.json").exists())
        self.assertFalse(failed.exists())
        self.assertFalse(work.exists())
        self.assertTrue((other / "logs/keep.log").exists())


if __name__ == "__main__":
    unittest.main()
