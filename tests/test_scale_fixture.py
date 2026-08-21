from __future__ import annotations

import json
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path

from psr.domain import (
    canonical_scale_percent,
    format_scale_percent,
    resolve_compile_scale,
    resolve_geometry_scale,
    scaled_model_path,
)


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
        self.parsing_cases = self.cases[:self.fixture["source"]["parsing_entities"]]

    def test_fixture_has_expected_snapshot_metadata(self) -> None:
        source = self.fixture["source"]
        self.assertEqual(self.fixture["schema_version"], 2)
        self.assertEqual(self.fixture["oracle_field"], "debug_string")
        self.assertEqual(self.fixture["oracle_prefix"], "effective_scale=")
        self.assertEqual(source["active_entities"], 51)
        self.assertEqual(source["parsing_entities"], 35)
        self.assertEqual(source["bytes"], 63081)
        self.assertEqual(
            source["sha256"],
            "bb6766854efb6584a7a8bd37e64e24212490c702082eb2946b6b9790b20071ee",
        )

    def test_every_entity_has_one_numeric_test_oracle(self) -> None:
        self.assertEqual(len(self.cases), 51)
        entity_ids = [case["entity_id"] for case in self.cases]
        self.assertEqual(len(entity_ids), len(set(entity_ids)))

        for case in self.cases:
            expected = Decimal(case["effective_scale"])
            self.assertGreaterEqual(expected, Decimal("0.01"), case)
            model = case.get("logical_model_path", self.fixture["source"]["default_model"])
            self.assertIn(model, self.fixture["models"])

    def test_minimum_scale_clamp_is_explicit(self) -> None:
        clamp_cases = [
            case
            for case in self.parsing_cases
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
            for case in self.parsing_cases
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
        counts = Counter(case["raw_modelscale"] for case in self.parsing_cases)
        repeated = {raw: count for raw, count in counts.items() if count > 1}
        self.assertEqual(repeated, {"''": 2, "'''": 2})

    def test_production_resolver_matches_every_confirmed_oracle(self) -> None:
        for case in self.parsing_cases:
            result = resolve_compile_scale(case["raw_modelscale"])
            self.assertEqual(
                result.compile_scale,
                Decimal(case["effective_scale"]),
                case,
            )
            self.assertEqual(result.raw_modelscale, case["raw_modelscale"])
            self.assertFalse(hasattr(result, "effective_scale"))

    def test_model_dependent_geometry_matches_every_confirmed_oracle(self) -> None:
        for case in self.cases:
            model = case.get("logical_model_path", self.fixture["source"]["default_model"])
            metadata = self.fixture["models"][model]
            compile_scale = resolve_compile_scale(case["raw_modelscale"]).compile_scale
            geometry = resolve_geometry_scale(
                compile_scale,
                bone_count=metadata["bone_count"],
                is_static_prop=metadata["is_static_prop"],
            )
            self.assertEqual(
                geometry.geometry_scale,
                Decimal(case["effective_scale"]),
                case,
            )

    def test_quadratic_geometry_has_a_separate_product_clamp(self) -> None:
        result = resolve_geometry_scale(
            Decimal("0.05"),
            bone_count=1,
            is_static_prop=False,
        )

        self.assertTrue(result.is_quadratic)
        self.assertEqual(result.geometry_scale, Decimal("0.01"))
        self.assertEqual(
            [item.code for item in result.diagnostics],
            ["psr_minimum_geometry_scale_clamp"],
        )

    def test_resolver_reports_prefix_fallback_and_clamp_reasons(self) -> None:
        self.assertEqual(
            resolve_compile_scale("3,0").compile_scale,
            Decimal("3"),
        )
        self.assertEqual(
            [item.code for item in resolve_compile_scale("2,0").diagnostics],
            ["hammer_scale_numeric_prefix"],
        )
        self.assertEqual(
            [item.code for item in resolve_compile_scale("0,5").diagnostics],
            ["hammer_scale_numeric_prefix", "hammer_scale_fallback"],
        )
        self.assertEqual(
            [item.code for item in resolve_compile_scale("blablabla").diagnostics],
            ["hammer_scale_fallback"],
        )
        self.assertEqual(
            [item.code for item in resolve_compile_scale("0.001").diagnostics],
            ["psr_minimum_scale_clamp"],
        )

    def test_missing_and_empty_values_use_hammer_default(self) -> None:
        for raw in (None, ""):
            result = resolve_compile_scale(raw)
            self.assertEqual(result.compile_scale, Decimal("1"))
            self.assertEqual(
                [item.code for item in result.diagnostics],
                ["hammer_scale_fallback"],
            )

    def test_rounding_is_decimal_half_up_to_hundredths(self) -> None:
        expected = {
            "1.001": Decimal("1.00"),
            "1.009": Decimal("1.01"),
            "1.095": Decimal("1.10"),
            "1.1": Decimal("1.10"),
            "1.104": Decimal("1.10"),
            "1.105": Decimal("1.11"),
            "1.999": Decimal("2.00"),
        }
        for raw, compile_scale in expected.items():
            self.assertEqual(resolve_compile_scale(raw).compile_scale, compile_scale)

    def test_scale_percent_and_managed_model_path_are_canonical(self) -> None:
        self.assertEqual(canonical_scale_percent(Decimal("1.10")), 110)
        self.assertEqual(canonical_scale_percent(Decimal("0.01")), 1)
        self.assertEqual(format_scale_percent(Decimal("0.01")), "001")
        self.assertEqual(format_scale_percent(Decimal("0.02")), "002")
        self.assertEqual(format_scale_percent(Decimal("0.50")), "050")
        self.assertEqual(format_scale_percent(Decimal("1.10")), "110")
        self.assertEqual(format_scale_percent(Decimal("10.00")), "1000")
        self.assertEqual(format_scale_percent(Decimal("55.00")), "5500")
        self.assertEqual(
            scaled_model_path("models/example/foo_static.mdl", Decimal("1.10")),
            "models/psr_scaled/example/foo_static_scaled_110.mdl",
        )
        self.assertEqual(
            scaled_model_path("models/root_model.mdl", Decimal("0.01")),
            "models/psr_scaled/root_model_scaled_001.mdl",
        )
        with self.assertRaises(ValueError):
            canonical_scale_percent(Decimal("1.095"))
        with self.assertRaises(ValueError):
            scaled_model_path("models/example/../unsafe.mdl", Decimal("1.00"))
        with self.assertRaises(ValueError):
            scaled_model_path("models/psr_scaled/unsafe.mdl", Decimal("1.00"))

    def test_long_numeric_input_does_not_escape_decimal_validation(self) -> None:
        raw = "9" * 200
        result = resolve_compile_scale(raw)
        self.assertTrue(result.compile_scale.is_finite())
        self.assertEqual(result.diagnostics, ())


if __name__ == "__main__":
    unittest.main()
