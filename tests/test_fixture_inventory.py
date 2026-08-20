from __future__ import annotations

import unittest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureInventoryTests(unittest.TestCase):
    def test_initial_fixture_families_exist(self) -> None:
        expected = {
            FIXTURES / "gameinfo" / "ordered_searchpaths.txt",
            FIXTURES / "mdl" / "synthetic_mdl_cases.json",
            FIXTURES / "scale" / "hammerpp_scale_cases.json",
            FIXTURES / "vmf" / "active_and_hidden_psr.vmf",
            FIXTURES / "vmf" / "no_psr_entities.vmf",
        }
        missing = sorted(path for path in expected if not path.is_file())
        self.assertEqual(missing, [])

if __name__ == "__main__":
    unittest.main()
