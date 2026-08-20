from __future__ import annotations

import re
import unittest

import psr
import psr.assets
import psr.cache
import psr.domain
import psr.keyvalues
import psr.pipeline


class ProjectFoundationTests(unittest.TestCase):
    def test_package_exposes_a_pep_440_development_version(self) -> None:
        self.assertRegex(psr.__version__, re.compile(r"^\d+\.\d+\.\d+\.dev\d+$"))
        self.assertTrue(psr.__version__.startswith("2.0.0.dev"))

    def test_architectural_subpackages_are_importable(self) -> None:
        expected = {
            "psr.assets",
            "psr.cache",
            "psr.domain",
            "psr.keyvalues",
            "psr.pipeline",
        }
        imported = {
            psr.assets.__name__,
            psr.cache.__name__,
            psr.domain.__name__,
            psr.keyvalues.__name__,
            psr.pipeline.__name__,
        }
        self.assertEqual(imported, expected)


if __name__ == "__main__":
    unittest.main()
