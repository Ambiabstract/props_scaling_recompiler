from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("PSR_FROZEN_EXE"),
    "set PSR_FROZEN_EXE to run the frozen one-file smoke test",
)
class FrozenExecutableTests(unittest.TestCase):
    def test_noop_compile_run_needs_no_python_or_external_tools(self) -> None:
        executable = Path(os.environ["PSR_FROZEN_EXE"]).resolve(strict=True)
        self.assertLessEqual(
            executable.stat().st_size,
            64 * 1024 * 1024,
            "frozen executable exceeds the approved hard size limit",
        )
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
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(root / "localappdata")

            completed = subprocess.run(
                [
                    str(executable),
                    "-game", str(game),
                    "-vmf_in", str(vmf_input),
                    "-vmf_out", str(vmf_output),
                    "-subfolders", "1",
                ],
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout.decode("utf-8", "replace")
                + completed.stderr.decode("utf-8", "replace"),
            )
            self.assertEqual(vmf_output.read_bytes(), source)
            output = completed.stdout.decode("utf-8", "replace")
            self.assertIn("SUCCESS", output)
            self.assertIn("[deprecated_cli_argument]", output)
            manifests = list((root / "localappdata").rglob("manifest.json"))
            self.assertEqual(len(manifests), 1)


if __name__ == "__main__":
    unittest.main()
