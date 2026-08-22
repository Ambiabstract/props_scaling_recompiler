from __future__ import annotations

import unittest
from pathlib import Path

from psr.assets import SMDTransformError, build_empty_bodygroup_smd


FIXTURE = Path(__file__).parent / "fixtures" / "smd" / "reference_triangle.smd"


class EmptyBodygroupSMDTests(unittest.TestCase):
    def test_reference_skeleton_is_preserved_and_geometry_is_empty(self) -> None:
        source = FIXTURE.read_bytes()

        result = build_empty_bodygroup_smd(source)

        self.assertEqual(
            result,
            (
                b"version 1\n"
                b"nodes\n"
                b'  0 "root" -1\n'
                b'  1 "handle" 0\n'
                b"end\n"
                b"skeleton\n"
                b"  time 0\n"
                b"    0 0 0 0 0 0 0\n"
                b"    1 1 2 3 0 0 0\n"
                b"end\n"
                b"triangles\n"
                b"end\n"
            ),
        )

    def test_missing_required_section_is_categorised(self) -> None:
        with self.assertRaises(SMDTransformError) as raised:
            build_empty_bodygroup_smd(b"version 1\nnodes\nend\n")
        self.assertEqual(raised.exception.code, "smd_skeleton_missing")


if __name__ == "__main__":
    unittest.main()
