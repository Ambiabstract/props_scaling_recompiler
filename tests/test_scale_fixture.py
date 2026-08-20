from __future__ import annotations

import json
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "scale"
    / "hammerpp_scale_cases.json"
)


def load_scale_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class ScaleFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_scale_fixture()
        self.cases = self.fixture["cases"]

    def test_fixture_has_expected_snapshot_metadata(self) -> None:
        source = self.fixture["source"]
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertEqual(self.fixture["oracle_field"], "debug_string")
        self.assertEqual(self.fixture["oracle_prefix"], "effective_scale=")
        self.assertEqual(source["active_entities"], 29)
        self.assertEqual(source["bytes"], 41255)
        self.assertEqual(
            source["sha256"],
            "b6a5a5827ec4bf8cbeeed8163ac1b85d9d17560a2759d2062b4342c928afd6ed",
        )

    def test_every_entity_has_one_numeric_test_oracle(self) -> None:
        self.assertEqual(len(self.cases), 29)
        entity_ids = [case["entity_id"] for case in self.cases]
        self.assertEqual(len(entity_ids), len(set(entity_ids)))

        for case in self.cases:
            expected = Decimal(case["effective_scale"])
            self.assertGreaterEqual(expected, Decimal("0.01"), case)

    def test_minimum_scale_clamp_is_explicit(self) -> None:
        clamp_cases = [
            case
            for case in self.cases
            if case.get("reason") == "psr_minimum_scale_clamp"
        ]
        self.assertEqual(
            clamp_cases,
            [
                {
                    "entity_id": "2460",
                    "raw_modelscale": "0.001",
                    "effective_scale": "0.01",
                    "reason": "psr_minimum_scale_clamp",
                }
            ],
        )

    def test_known_hammer_compatibility_cases_are_preserved(self) -> None:
        by_raw = {
            case["raw_modelscale"]: case["effective_scale"]
            for case in self.cases
        }
        expected = {
            "blablabla": "1.0",
            "1,0": "1.0",
            "2,0": "2.0",
            "1,5": "1.0",
            "0,5": "1.0",
            "1ю5": "1.0",
            "0.01": "0.01",
            "55": "55.0",
        }
        for raw_modelscale, effective_scale in expected.items():
            self.assertEqual(by_raw[raw_modelscale], effective_scale)

    def test_only_intentional_raw_values_are_repeated(self) -> None:
        counts = Counter(case["raw_modelscale"] for case in self.cases)
        repeated = {raw: count for raw, count in counts.items() if count > 1}
        self.assertEqual(repeated, {"''": 2, "'''": 2})


if __name__ == "__main__":
    unittest.main()
