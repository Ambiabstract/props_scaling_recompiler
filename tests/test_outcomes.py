from __future__ import annotations

import unittest
from psr.pipeline import (
    OutcomeLedger,
    WorkFailure,
    filter_operation_plan,
)

from tests.test_vmf_discovery_planning import (
    DiscoveryPlanningTests,
    entity,
    load_mdl_case,
)


class OutcomeClosureTests(unittest.TestCase):
    setUp = DiscoveryPlanningTests.setUp
    tearDown = DiscoveryPlanningTests.tearDown
    filesystem = DiscoveryPlanningTests.filesystem
    install_case = DiscoveryPlanningTests.install_case

    def operation_and_materials(self):
        first = load_mdl_case("static_multi_material")
        second = load_mdl_case("static_multi_material")
        second["logical_model_path"] = "models/fixture/second.mdl"
        second["internal_model_name"] = "fixture/second.mdl"
        self.install_case(first)
        self.install_case(second)
        source = (
            entity("10", first["logical_model_path"], "1.5", color="1 2 3")
            + entity("11", first["logical_model_path"], "2.0", color="4 5 6")
            + entity("12", first["logical_model_path"], "2.0")
            + entity("20", second["logical_model_path"], "1.5", color="1 2 3")
        ).encode("ascii")
        from psr.pipeline import (
            build_colored_material_plan,
            build_operation_plan,
            discover_vmf_requests,
            inspect_colored_material_sources,
            inspect_map_sources,
        )
        discovery = discover_vmf_requests(source, map_identity="maps/outcomes.vmf")
        operation = build_operation_plan(inspect_map_sources(discovery, self.filesystem()))
        materials = build_colored_material_plan(
            operation,
            inspect_colored_material_sources(operation, self.filesystem()),
        )
        return operation, materials

    def test_entity_color_variant_model_and_shared_material_closures(self) -> None:
        operation, materials = self.operation_and_materials()
        shared_output = next(
            item.logical_output_material
            for item in materials.colored_materials
            if item.render_color == (1, 2, 3)
        )
        cases = (
            (WorkFailure("entity", "x", "x", entity_id="10"), {"10"}),
            (WorkFailure(
                "colored_skin", "x", "x",
                logical_source_model=operation.usages[1].request.logical_model_path,
                source_skin=0,
                render_color=(4, 5, 6),
            ), {"11"}),
            (WorkFailure(
                "model_variant", "x", "x",
                logical_output_model=operation.usages[1].logical_output_model,
            ), {"11", "12"}),
            (WorkFailure(
                "source_model", "x", "x",
                logical_source_model=operation.usages[0].request.logical_model_path,
            ), {"10", "11", "12"}),
            (WorkFailure(
                "material", "x", "x", logical_material=shared_output,
            ), {"10", "20"}),
        )
        for failure, expected in cases:
            with self.subTest(scope=failure.scope):
                self.assertEqual(
                    OutcomeLedger((failure,)).affected_entity_ids(operation, materials),
                    expected,
                )

    def test_filtered_operation_keeps_independent_work_and_reduces_groups(self) -> None:
        operation, _materials = self.operation_and_materials()
        filtered = filter_operation_plan(operation, {"10", "11"})

        self.assertEqual(
            {item.request.entity_id for item in filtered.usages},
            {"12", "20"},
        )
        self.assertEqual(
            {entity_id for item in filtered.generated_models for entity_id in item.entity_ids},
            {"12", "20"},
        )
        self.assertTrue(filtered.is_valid)


if __name__ == "__main__":
    unittest.main()
