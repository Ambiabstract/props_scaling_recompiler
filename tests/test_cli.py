from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from psr.cli import build_arg_parser, discover_tool_commands, main
from psr.runtime import CompileRunResult, DiagnosticReport, build_project_state_paths
from psr.cache import ProjectIdentity


class CliContractTests(unittest.TestCase):
    def test_main_arguments_remain_compatible_and_legacy_values_are_accepted(self) -> None:
        args = build_arg_parser().parse_args([
            "-game", "game",
            "-vmf_in", "input.vmf",
            "-vmf_out", "output.vmf",
            "-subfolders", "1",
            "-force_recompile", "0",
            "-dynamic_fallback", "0",
        ])

        self.assertEqual(args.game, "game")
        self.assertEqual(args.vmf_in, "input.vmf")
        self.assertEqual(args.vmf_out, "output.vmf")
        self.assertEqual(args.subfolders, 1)
        self.assertEqual(args.force_recompile, 0)
        self.assertEqual(args.dynamic_fallback, 0)

    def test_tool_discovery_prefers_separate_third_party_crowbar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            third_party = root / "third-party"
            third_party.mkdir()
            crowbar = third_party / "CrowbarCommandLineDecomp.exe"
            studiomdl = root / "studiomdl.exe"
            crowbar.write_bytes(b"crowbar")
            studiomdl.write_bytes(b"studiomdl")

            crowbar_command, studiomdl_command = discover_tool_commands(root)

        self.assertEqual(crowbar_command, (crowbar.resolve(),))
        self.assertEqual(studiomdl_command, (studiomdl.resolve(),))

    def test_noop_cli_writes_output_and_reports_deprecated_argument_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            maps = game / "maps"
            maps.mkdir(parents=True)
            (game / "GameInfo.txt").write_text(
                'GameInfo\n{\n FileSystem\n {\n  SearchPaths\n  {\n'
                '   game "|gameinfo_path|."\n  }\n }\n}\n',
                encoding="utf-8",
            )
            vmf_input = maps / "noop.vmf"
            vmf_output = maps / "psr_temp" / "noop.vmf"
            source = b'world\n{\n "id" "1"\n}\n'
            vmf_input.write_bytes(source)
            console = StringIO()

            with patch.dict("os.environ", {"LOCALAPPDATA": str(root / "local")}), redirect_stdout(console):
                exit_code = main([
                    "-game", str(game),
                    "-vmf_in", str(vmf_input),
                    "-vmf_out", str(vmf_output),
                    "-subfolders", "1",
                ])
            written = vmf_output.read_bytes()
            output = console.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertEqual(written, source)
        self.assertEqual(output.count("[deprecated_cli_argument]"), 1)
        self.assertIn("SUCCESS", output)

    def test_errors_do_not_change_exit_zero_when_valid_vmf_was_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = build_project_state_paths(
                ProjectIdentity("a" * 64, "c:/game/gameinfo.txt", "b" * 64),
                local_appdata=root,
            )
            state.ensure_directories()

            def partial(_request, report: DiagnosticReport, _progress):
                report.add("error", "model_failed", "fallback delivered")
                return CompileRunResult(
                    True, "maps/test.vmf", state, 1, 0, 0, 0, 0, 0
                )

            with patch("psr.cli._validate_platform"), patch(
                "psr.cli.execute_compile_run", side_effect=partial
            ), redirect_stdout(StringIO()):
                exit_code = main([
                    "-game", "game",
                    "-vmf_in", "input.vmf",
                    "-vmf_out", "output.vmf",
                ])

        self.assertEqual(exit_code, 0)

    def test_early_runtime_failure_still_returns_zero_after_valid_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vmf_input = root / "input.vmf"
            vmf_output = root / "out" / "output.vmf"
            source = b'world\n{\n "id" "1"\n}\n'
            vmf_input.write_bytes(source)

            with patch("psr.cli._validate_platform"), redirect_stdout(StringIO()):
                exit_code = main([
                    "-game", str(root / "missing-game"),
                    "-vmf_in", str(vmf_input),
                    "-vmf_out", str(vmf_output),
                ])
            written = vmf_output.read_bytes()

        self.assertEqual(exit_code, 0)
        self.assertEqual(written, source)


if __name__ == "__main__":
    unittest.main()
