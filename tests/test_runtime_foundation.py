from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path

from psr.cache import ProjectIdentity
from psr.pipeline import PipelineDiagnostic
from psr.runtime import (
    DiagnosticReport,
    ProjectLock,
    ProjectLockError,
    build_project_state_paths,
)


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
        report.add("recommendation", "retry", "inspect the retained staging directory")

        rendered = report.render(color=False)

        self.assertTrue(report.has_errors)
        self.assertEqual(rendered.count("[rounded]"), 1)
        self.assertLess(rendered.index("ERRORS"), rendered.index("WARNINGS"))
        self.assertLess(rendered.index("WARNINGS"), rendered.index("RECOMMENDATIONS"))
        stream = StringIO()
        report.print(stream)
        self.assertEqual(stream.getvalue(), rendered)


if __name__ == "__main__":
    unittest.main()
