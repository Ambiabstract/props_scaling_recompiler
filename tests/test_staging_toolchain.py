from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import textwrap
import unittest
from decimal import Decimal
from pathlib import Path

from psr.assets import (
    CompiledModelValidationError,
    MountedSearchPath,
    OrderedAssetFileSystem,
    ToolExecutionError,
    build_reference_qc,
    build_scaled_qc,
    inspect_qc,
    inspect_source_model,
    run_crowbar_decompile,
    run_studiomdl_compile,
    validate_compiled_model,
)
from psr.pipeline import (
    QCOperationPlan,
    ReferenceQCArtifactPlan,
    ScaledQCArtifactPlan,
    StagingError,
    StagingWorkspace,
    stage_qc_operation,
    stage_source_model,
)
from tests.mdl_fixture_builder import build_case_files, build_mdl


class StagingLifecycleTests(unittest.TestCase):
    def test_workspace_is_unique_confined_and_cleaned_on_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "staging"
            with StagingWorkspace.create(
                parent,
                operation_identity="maps/a.vmf:abc",
            ) as first:
                first_root = first.root
                item = first.write_bytes("qc/variants/a.qc", b"qc")
                self.assertTrue(item.physical_path.is_file())
                with self.assertRaises(StagingError) as raised:
                    first.path("../outside.txt")
                self.assertEqual(raised.exception.code, "staging_path_invalid")
                second = StagingWorkspace.create(
                    parent,
                    operation_identity="maps/a.vmf:abc",
                )
                self.assertNotEqual(first.root, second.root)
                second.cleanup()
            self.assertFalse(first_root.exists())
            self.assertTrue(parent.is_dir())

    def test_preserved_workspace_requires_explicit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = StagingWorkspace.create(
                Path(temp),
                operation_identity="debug-operation",
                preserve=True,
            )
            with workspace:
                workspace.write_bytes("decompiled/model/readme.txt", b"kept")
            self.assertTrue(workspace.root.is_dir())
            workspace.cleanup()
            self.assertFalse(workspace.root.exists())

    def test_conflicting_second_write_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with StagingWorkspace.create(
                Path(temp), operation_identity="conflict"
            ) as workspace:
                workspace.write_bytes("qc/a.qc", b"first")
                with self.assertRaises(StagingError) as raised:
                    workspace.write_bytes("qc/a.qc", b"second")
                self.assertEqual(raised.exception.code, "staging_content_conflict")

    def test_source_staging_rechecks_provenance_and_content_identity(self) -> None:
        fixture = Path(__file__).parent / "fixtures/mdl/synthetic_mdl_cases.json"
        case = json.loads(fixture.read_text(encoding="utf-8"))["cases"][0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "game"
            for logical_path, content in build_case_files(case).items():
                target = game.joinpath(*Path(logical_path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            mount = MountedSearchPath(0, 0, 0, "game", "fixture", "folder", game)
            filesystem = OrderedAssetFileSystem((mount,))
            source = inspect_source_model(filesystem, case["logical_model_path"])

            with StagingWorkspace.create(
                root / "staging", operation_identity="source-stage"
            ) as workspace:
                staged = stage_source_model(workspace, filesystem, source)
                self.assertEqual(len(staged.files), len(source.files))
                self.assertEqual(
                    staged.physical_model_path.read_bytes(),
                    game.joinpath(*Path(case["logical_model_path"]).parts).read_bytes(),
                )

            model = game.joinpath(*Path(case["logical_model_path"]).parts)
            model.write_bytes(model.read_bytes() + b"changed")
            with StagingWorkspace.create(
                root / "staging", operation_identity="changed-source"
            ) as workspace:
                with self.assertRaises(StagingError) as raised:
                    stage_source_model(workspace, filesystem, source)
                self.assertEqual(
                    raised.exception.code,
                    "staging_source_content_changed",
                )

    def test_qc_plan_is_materialised_only_when_hashes_match(self) -> None:
        reference_content = b'$modelname "reference.mdl"\n$staticprop\n'
        variant_content = (
            b'$modelname "psr_scaled/a_scaled_150.mdl"\n$scale 1.5\n$staticprop\n'
        )
        reference = ReferenceQCArtifactPlan(
            logical_source_model="models/a.mdl",
            staging_relative_path="reference/a.qc",
            source_qc_sha256="a" * 64,
            output_qc_sha256=hashlib.sha256(reference_content).hexdigest(),
            skin_layout_fingerprint="b" * 64,
            requires_static_conversion=False,
            content=reference_content,
            mutations=(),
        )
        variant = ScaledQCArtifactPlan(
            logical_source_model="models/a.mdl",
            logical_output_model="models/psr_scaled/a_scaled_150.mdl",
            compile_scale=Decimal("1.50"),
            geometry_scale=Decimal("1.50"),
            staging_relative_path="variants/a_scaled_150.qc",
            reference_qc_sha256=reference.output_qc_sha256,
            output_qc_sha256=hashlib.sha256(variant_content).hexdigest(),
            content=variant_content,
            mutations=("replace_modelname", "insert_scale"),
        )
        plan = QCOperationPlan("maps/a.vmf", (reference,), (variant,), ())
        with tempfile.TemporaryDirectory() as temp:
            with StagingWorkspace.create(
                Path(temp), operation_identity="qc-stage"
            ) as workspace:
                staged = stage_qc_operation(workspace, plan)
                self.assertEqual(len(staged), 2)
                self.assertEqual(staged[1].physical_path.read_bytes(), variant_content)


class ToolAdapterTests(unittest.TestCase):
    def test_crowbar_uses_argv_and_requires_one_qc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "fake crowbar.py"
            fake.write_text(textwrap.dedent("""
                import pathlib
                import sys

                args = sys.argv[1:]
                output = pathlib.Path(args[args.index("-o") + 1])
                output.mkdir(parents=True, exist_ok=True)
                (output / "nested").mkdir()
                (output / "nested" / "model.qc").write_bytes(b'$modelname "x.mdl"\\n')
                (output / "nested" / "mesh.smd").write_bytes(b"version 1\\n")
                print("decompiled")
            """), encoding="utf-8")
            model = root / "source model.mdl"
            model.write_bytes(b"mdl")

            result = run_crowbar_decompile(
                (sys.executable, fake),
                model_path=model,
                output_directory=root / "decompiled output",
            )

            self.assertEqual(result.qc_path.name, "model.qc")
            self.assertEqual(
                result.relative_files,
                ("nested/mesh.smd", "nested/model.qc"),
            )
            self.assertEqual(result.invocation.returncode, 0)
            self.assertIn(b"decompiled", result.invocation.stdout)
            self.assertIn(str(model.resolve()), result.invocation.argv)

    def test_crowbar_nonzero_exit_preserves_captured_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "fail.py"
            fake.write_text(
                "import sys; sys.stderr.buffer.write(b'bad model'); raise SystemExit(7)\n",
                encoding="utf-8",
            )
            model = root / "source.mdl"
            model.write_bytes(b"mdl")
            with self.assertRaises(ToolExecutionError) as raised:
                run_crowbar_decompile(
                    (sys.executable, fake),
                    model_path=model,
                    output_directory=root / "out",
                )
            self.assertEqual(raised.exception.code, "crowbar_failed")
            self.assertEqual(raised.exception.invocation.returncode, 7)
            self.assertEqual(raised.exception.invocation.stderr, b"bad model")

    def test_studiomdl_success_is_followed_by_artifact_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "game"
            game.mkdir()
            qc = root / "case.qc"
            logical = "models/psr_scaled/matrix/case_scaled_150.mdl"
            qc.write_text(
                '$modelname "psr_scaled/matrix/case_scaled_150.mdl"\n$staticprop\n',
                encoding="ascii",
            )
            fake = root / "fake studiomdl.py"
            fake.write_text(textwrap.dedent("""
                import pathlib
                import re
                import struct
                import sys

                args = sys.argv[1:]
                game = pathlib.Path(args[args.index("-game") + 1])
                qc = pathlib.Path(args[-1])
                text = qc.read_text(encoding="ascii")
                name = re.search(r'\\$modelname\\s+"([^"]+)"', text).group(1)
                target = game / "models" / pathlib.PurePosixPath(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = bytearray(156)
                encoded = name.encode("ascii")
                struct.pack_into("<4si4s64s", data, 0, b"IDST", 48, b"ABCD", encoded)
                struct.pack_into("<I", data, 152, 0x10)
                target.write_bytes(data)
                target.with_suffix(".vvd").write_bytes(b"vvd")
                target.with_suffix(".dx80.vtx").write_bytes(b"vtx80")
                target.with_suffix(".dx90.vtx").write_bytes(b"vtx")
                target.with_suffix(".sw.vtx").write_bytes(b"vtxsw")
                print("no Completed marker is needed")
            """), encoding="utf-8")

            invocation = run_studiomdl_compile(
                (sys.executable, fake),
                game_directory=game,
                qc_path=qc,
            )
            validation = validate_compiled_model(
                game,
                logical,
                requires_physics=False,
            )

            self.assertEqual(invocation.returncode, 0)
            self.assertTrue(validation.is_static_prop)
            self.assertEqual(validation.mdl_version, 48)
            self.assertEqual(len(validation.files), 5)

    def test_validation_rejects_missing_physics_companion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            logical = "models/psr_scaled/props/a_scaled_100.mdl"
            case = {
                "internal_model_name": "psr_scaled/props/a_scaled_100.mdl",
                "mdl_version": 48,
                "static_prop": True,
                "checksum_hex": "11223344",
                "bone_count": 1,
                "skin_families": [["body"]],
                "cdmaterials": ["models/props/"],
                "surface_property": "default",
            }
            target = game.joinpath(*Path(logical).parts)
            target.parent.mkdir(parents=True)
            target.write_bytes(build_mdl(case))
            target.with_suffix(".vvd").write_bytes(b"vvd")
            target.with_suffix(".dx80.vtx").write_bytes(b"vtx80")
            target.with_suffix(".dx90.vtx").write_bytes(b"vtx")
            target.with_suffix(".sw.vtx").write_bytes(b"vtxsw")

            with self.assertRaises(CompiledModelValidationError) as raised:
                validate_compiled_model(game, logical, requires_physics=True)
            self.assertEqual(raised.exception.code, "compiled_companion_missing")
            self.assertIn(".phy", raised.exception.detail)

    def test_validation_accepts_studiomdl_63_byte_header_name_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            logical = (
                "models/psr_scaled/props/de_nuke/hr_nuke/nuke_clothes/"
                "nuke_overall_gloves_scaled_200.mdl"
            )
            internal_name = logical.removeprefix("models/")
            case = {
                "internal_model_name": "fixture/short.mdl",
                "mdl_version": 48,
                "static_prop": True,
                "checksum_hex": "11223344",
                "bone_count": 1,
                "skin_families": [["body"]],
                "cdmaterials": ["models/props/"],
                "surface_property": "default",
            }
            mdl = bytearray(build_mdl(case))
            truncated = internal_name.encode("ascii")[:63]
            mdl[12:76] = truncated + b"\0" * (64 - len(truncated))
            target = game.joinpath(*Path(logical).parts)
            target.parent.mkdir(parents=True)
            target.write_bytes(mdl)
            for extension in (".vvd", ".dx80.vtx", ".dx90.vtx", ".sw.vtx"):
                target.with_suffix(extension).write_bytes(extension.encode("ascii"))

            validation = validate_compiled_model(game, logical, requires_physics=False)

            self.assertEqual(validation.internal_model_name, internal_name[:63])

    def test_validation_rejects_wrong_truncated_header_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            logical = (
                "models/psr_scaled/props/de_nuke/hr_nuke/nuke_clothes/"
                "nuke_overall_gloves_scaled_200.mdl"
            )
            case = {
                "internal_model_name": "fixture/short.mdl",
                "mdl_version": 48,
                "static_prop": True,
                "checksum_hex": "11223344",
                "bone_count": 1,
                "skin_families": [["body"]],
                "cdmaterials": ["models/props/"],
                "surface_property": "default",
            }
            mdl = bytearray(build_mdl(case))
            wrong = ("x" + logical.removeprefix("models/")[1:]).encode("ascii")[:63]
            mdl[12:76] = wrong + b"\0" * (64 - len(wrong))
            target = game.joinpath(*Path(logical).parts)
            target.parent.mkdir(parents=True)
            target.write_bytes(mdl)
            for extension in (".vvd", ".dx80.vtx", ".dx90.vtx", ".sw.vtx"):
                target.with_suffix(extension).write_bytes(extension.encode("ascii"))

            with self.assertRaises(CompiledModelValidationError) as raised:
                validate_compiled_model(game, logical, requires_physics=False)

            self.assertEqual(raised.exception.code, "compiled_modelname_mismatch")

    def test_validation_rejects_multibone_dynamic_to_static_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            logical = "models/psr_scaled/props/multibone_scaled_100.mdl"
            case = {
                "internal_model_name": logical.removeprefix("models/"),
                "mdl_version": 48,
                "static_prop": True,
                "checksum_hex": "11223344",
                "bone_count": 2,
                "skin_families": [["body"]],
                "cdmaterials": ["models/props/"],
                "surface_property": "default",
            }
            target = game.joinpath(*Path(logical).parts)
            target.parent.mkdir(parents=True)
            target.write_bytes(build_mdl(case))
            for extension in (".vvd", ".dx80.vtx", ".dx90.vtx", ".sw.vtx"):
                target.with_suffix(extension).write_bytes(extension.encode("ascii"))

            with self.assertRaises(CompiledModelValidationError) as raised:
                validate_compiled_model(
                    game,
                    logical,
                    requires_physics=False,
                    requires_static_conversion=True,
                )

            self.assertEqual(
                raised.exception.code,
                "compiled_static_conversion_bones",
            )


class StagedQCCompileMatrixTests(unittest.TestCase):
    def test_static_dynamic_collision_and_existing_scale_matrix(self) -> None:
        rows: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp:
            with StagingWorkspace.create(
                Path(temp), operation_identity="compile-validation-matrix"
            ) as workspace:
                for is_static in (False, True):
                    for collision in ("none", "model", "joints"):
                        for existing_scale in (False, True):
                            name = (
                                f"{'static' if is_static else 'dynamic'}-"
                                f"{collision}-"
                                f"{'scale' if existing_scale else 'no-scale'}"
                            )
                            source, collision_bytes = _matrix_qc(
                                is_static=is_static,
                                collision=collision,
                                existing_scale=existing_scale,
                            )
                            reference = build_reference_qc(
                                source,
                                expected_source_families=(("body",),),
                                target_families=(("body",),),
                                require_staticprop=True,
                            )
                            output_model = f"models/psr_scaled/matrix/{name}_scaled_150.mdl"
                            variant = build_scaled_qc(
                                reference.data,
                                logical_output_model=output_model,
                                compile_scale=Decimal("1.50"),
                                geometry_scale=Decimal("1.50"),
                            )
                            metadata = inspect_qc(variant.data)
                            staged = workspace.write_bytes(
                                f"qc/matrix/{name}.qc", variant.data
                            )

                            self.assertTrue(metadata.is_static_prop, name)
                            self.assertEqual(metadata.scale, "1.5", name)
                            self.assertEqual(metadata.lod_distances, ("60",), name)
                            self.assertNotIn(b"$bbox", variant.data, name)
                            if collision_bytes:
                                self.assertIn(collision_bytes, variant.data, name)
                            rows.append({
                                "name": name,
                                "source_static": is_static,
                                "collision": collision,
                                "source_scale": "2" if existing_scale else None,
                                "result_scale": metadata.scale,
                                "reference_mutations": reference.mutations,
                                "variant_mutations": variant.mutations,
                                "sha256": staged.sha256,
                            })

                self.assertEqual(len(rows), 12)
                self.assertEqual(
                    len({row["sha256"] for row in rows}),
                    12,
                )
                report = workspace.write_bytes(
                    "reports/qc-compile-matrix.json",
                    json.dumps(rows, sort_keys=True, indent=2).encode("utf-8"),
                )
                self.assertTrue(report.physical_path.is_file())


def _matrix_qc(
    *,
    is_static: bool,
    collision: str,
    existing_scale: bool,
) -> tuple[bytes, bytes]:
    lines = ['$modelname "matrix/source.mdl"']
    if existing_scale:
        lines.append("$scale 2")
    if is_static:
        lines.append("$staticprop")
    lines.extend([
        '$body "body" "body.smd"',
        '$cdmaterials "models/matrix/"',
    ])
    collision_text = ""
    if collision == "model":
        collision_text = '$collisionmodel "physics.smd"\n{\n    $mass 10\n}'
    elif collision == "joints":
        collision_text = '$collisionjoints "physics.smd"\n{\n    $mass 10\n}'
    if collision_text:
        lines.append(collision_text)
    lines.extend([
        "$bbox -1 -2 -3 1 2 3",
        '$sequence "idle" "body.smd"',
        '$lod 40 { replacemodel "body.smd" "lod.smd" }',
    ])
    source = ("\n".join(lines) + "\n").encode("ascii")
    return source, collision_text.encode("ascii")


if __name__ == "__main__":
    unittest.main()
