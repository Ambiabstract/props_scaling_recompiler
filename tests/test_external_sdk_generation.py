from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from psr.assets import (
    OrderedAssetFileSystem,
    ToolExecutionError,
    parse_gameinfo_search_paths,
    parse_search_paths_text,
    plan_search_paths,
    run_studiomdl_compile,
)
from psr.cache import build_project_identity, empty_manifest
from psr.pipeline import (
    GenerationError,
    StagingWorkspace,
    build_colored_material_plan,
    build_operation_plan,
    build_skin_layout_plan,
    discover_vmf_requests,
    generate_and_validate,
    inspect_colored_material_sources,
    inspect_map_sources,
)

try:
    import pytest
except ImportError:  # pragma: no cover - unittest-only environment
    pytestmark = ()
else:
    pytestmark = pytest.mark.external_sdk


RUN_EXTERNAL = os.environ.get("PSR_RUN_EXTERNAL_SDK") == "1"
SDK_ROOT = Path(os.environ.get(
    "PSR_SDK_ROOT",
    r"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer",
))
ANTENNA_ROOT = Path(os.environ.get(
    "PSR_ANTENNA_ROOT",
    r"C:\Program Files (x86)\Steam\steamapps\sourcemods\antenna_sdk2013",
))
STUDIOMDL = SDK_ROOT / "bin/studiomdl.exe"
CROWBAR = SDK_ROOT / "bin/CrowbarCommandLineDecomp.exe"
EXPECTED_STUDIOMDL_SHA256 = (
    "e6c4ea7477b8ce31de878ff53ca640cb222c4978f3ba33c4715de3de1c7a6416"
)
EXPECTED_CROWBAR_SHA256 = (
    "4b5fc8f5092448c1f8fe12f6849bf8ee3996406f02109ec90ab800c6cf145b2a"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _entity(
    entity_id: int = 1,
    color: tuple[int, int, int] = (190, 48, 148),
) -> bytes:
    return f'''entity
{{
    "id" "{entity_id}"
    "classname" "prop_static_scalable"
    "model" "models/props_se/storage/book_2.mdl"
    "modelscale" "1.5"
    "skin" "0"
    "rendercolor" "{color[0]} {color[1]} {color[2]}"
}}
'''.encode("ascii")


def _white_entity() -> bytes:
    return _entity(color=(255, 255, 255))


def _staging_gameinfo(*extra_roots: Path) -> bytes:
    staging_token = "|gameinfo_path|."
    antenna = ANTENNA_ROOT.resolve().as_posix()
    sdk = SDK_ROOT.resolve().as_posix()
    extras = "\n".join(
        f'            game+mod "{root.resolve().as_posix()}"'
        for root in extra_roots
    )
    return f'''"GameInfo"
{{
    game "PSR isolated SDK validation"
    type singleplayer_only
    FileSystem
    {{
        SteamAppId 243730
        SearchPaths
        {{
            game+mod+mod_write+default_write_path "{staging_token}"
{extras}
            game+mod "{antenna}"
            game "{sdk}/ep2"
            game "{sdk}/episodic"
            game "{sdk}/hl2"
            platform "{sdk}/platform"
        }}
    }}
}}
'''.encode("utf-8")


def _overlay_filesystem(overlay: Path) -> OrderedAssetFileSystem:
    text = f'''GameInfo
{{
    FileSystem
    {{
        SearchPaths
        {{
            game "{overlay.resolve().as_posix()}"
            game "{ANTENNA_ROOT.resolve().as_posix()}"
        }}
    }}
}}
'''
    plan = plan_search_paths(
        parse_search_paths_text(text),
        gameinfo_dir=ANTENNA_ROOT,
        engine_root=SDK_ROOT,
    )
    return OrderedAssetFileSystem(plan.mounts)


def _write_logical(root: Path, logical_path: str, content: bytes) -> None:
    destination = root.joinpath(*Path(logical_path).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


@unittest.skipUnless(
    RUN_EXTERNAL,
    "set PSR_RUN_EXTERNAL_SDK=1 for the read-only Antenna/isolated SDK matrix",
)
class ExternalSDKGenerationTests(unittest.TestCase):
    def plans(self, filesystem: OrderedAssetFileSystem, map_identity: str):
        gameinfo = ANTENNA_ROOT / "GameInfo.txt"
        discovery = discover_vmf_requests(_entity(), map_identity=map_identity)
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        material_inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, material_inspection)
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(gameinfo)),
        )
        self.assertTrue(operation.is_valid, operation.diagnostics)
        self.assertTrue(materials.is_valid, materials.diagnostics)
        self.assertTrue(skin_layout.is_valid, skin_layout.diagnostics)
        return operation, materials, skin_layout

    def test_book2_generated_patch_and_model_compile_in_isolated_game_root(self) -> None:
        gameinfo = ANTENNA_ROOT / "GameInfo.txt"
        for path in (gameinfo, CROWBAR, STUDIOMDL):
            self.assertTrue(path.is_file(), path)
        self.assertEqual(_sha256(CROWBAR), EXPECTED_CROWBAR_SHA256)
        self.assertEqual(_sha256(STUDIOMDL), EXPECTED_STUDIOMDL_SHA256)

        specs = parse_gameinfo_search_paths(gameinfo)
        search_plan = plan_search_paths(
            specs,
            gameinfo_dir=ANTENNA_ROOT,
            engine_root=SDK_ROOT,
        )
        filesystem = OrderedAssetFileSystem(search_plan.mounts)
        operation, materials, skin_layout = self.plans(
            filesystem,
            "maps/psr_external_sdk_book2.vmf",
        )

        with tempfile.TemporaryDirectory(prefix="psr-external-sdk-") as temp:
            with StagingWorkspace.create(
                Path(temp),
                operation_identity=operation.map_identity,
            ) as workspace:
                workspace.write_bytes("game/GameInfo.txt", _staging_gameinfo())
                result = generate_and_validate(
                    workspace,
                    filesystem,
                    operation,
                    materials,
                    skin_layout,
                    crowbar_command=(CROWBAR,),
                    studiomdl_command=(STUDIOMDL,),
                )

                self.assertEqual(len(result.decompilations), 1)
                self.assertEqual(len(result.models), 1)
                self.assertEqual(len(result.materials), 1)
                self.assertTrue(result.models[0].validation.is_static_prop)
                self.assertEqual(len(result.models[0].validation.files), 6)
                self.assertTrue(all(
                    item.generated.generation_mode == "patch"
                    for item in result.materials
                ))
                self.assertTrue(all(
                    b"Patch" in item.generated.content
                    and b'"insert"' in item.generated.content
                    for item in result.materials
                ))
                logs = (
                    result.models[0].compile_invocation.stdout
                    + result.models[0].compile_invocation.stderr
                )
                self.assertNotIn(b"KeyValues Error", logs)

    def test_studiomdl_skin_family_boundary_is_1024_rows(self) -> None:
        gameinfo = ANTENNA_ROOT / "GameInfo.txt"
        specs = parse_gameinfo_search_paths(gameinfo)
        search_plan = plan_search_paths(
            specs,
            gameinfo_dir=ANTENNA_ROOT,
            engine_root=SDK_ROOT,
        )
        filesystem = OrderedAssetFileSystem(search_plan.mounts)
        discovery = discover_vmf_requests(
            _white_entity(),
            map_identity="maps/psr_external_sdk_skin_limit.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, inspection)
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(gameinfo)),
        )
        source_families = skin_layout.layouts[0].families
        source_row = source_families[0]

        with tempfile.TemporaryDirectory(prefix="psr-external-skin-limit-") as temp:
            root = Path(temp)
            accepted_layout = replace(
                skin_layout.layouts[0],
                families=source_families + (source_row,) * (1024 - len(source_families)),
                layout_fingerprint="a" * 64,
            )
            with StagingWorkspace.create(
                root / "accepted",
                operation_identity=operation.map_identity,
            ) as workspace:
                workspace.write_bytes("game/GameInfo.txt", _staging_gameinfo())
                result = generate_and_validate(
                    workspace,
                    filesystem,
                    operation,
                    materials,
                    replace(skin_layout, layouts=(accepted_layout,)),
                    crowbar_command=(CROWBAR,),
                    studiomdl_command=(STUDIOMDL,),
                )
                self.assertEqual(len(result.models), 1)
                content = result.models[0].qc_artifact.content
                collision = content.index(b"$collisionmodel")
                group_close = content.rfind(b"}", 0, collision)
                self.assertNotEqual(group_close, -1)
                overflow_row = (
                    b"    { "
                    + b" ".join(
                        b'"' + material.encode("ascii") + b'"'
                        for material in source_row
                    )
                    + b" }\n"
                )
                overflow_qc = result.models[0].compile_qc.physical_path.with_name(
                    "overflow_skinfamilies.qc"
                )
                overflow_qc.write_bytes(
                    content[:group_close] + overflow_row + content[group_close:]
                )
                with self.assertRaises(ToolExecutionError) as caught:
                    run_studiomdl_compile(
                        (STUDIOMDL,),
                        game_directory=workspace.path("game"),
                        qc_path=overflow_qc,
                    )
                self.assertEqual(caught.exception.code, "studiomdl_failed")
                invocation = caught.exception.invocation
                self.assertIsNotNone(invocation)
                logs = invocation.stdout + invocation.stderr
                self.assertIn(b"Aborted Processing", logs)

    def test_studiomdl_material_boundary_is_32_unique_names(self) -> None:
        gameinfo = ANTENNA_ROOT / "GameInfo.txt"
        specs = parse_gameinfo_search_paths(gameinfo)
        search_plan = plan_search_paths(
            specs,
            gameinfo_dir=ANTENNA_ROOT,
            engine_root=SDK_ROOT,
        )
        filesystem = OrderedAssetFileSystem(search_plan.mounts)
        discovery = discover_vmf_requests(
            b"".join(_entity(index, (0, 0, index)) for index in range(1, 25)),
            map_identity="maps/psr_external_sdk_material_limit.vmf",
        )
        operation = build_operation_plan(inspect_map_sources(discovery, filesystem))
        inspection = inspect_colored_material_sources(operation, filesystem)
        materials = build_colored_material_plan(operation, inspection)
        skin_layout = build_skin_layout_plan(
            operation,
            materials,
            empty_manifest(build_project_identity(gameinfo)),
        )
        layout = skin_layout.layouts[0]
        self.assertEqual(len(layout.mappings), 23)
        self.assertEqual(
            len({material for family in layout.families for material in family}),
            31,
        )
        warning = next(
            item
            for item in skin_layout.diagnostics
            if item.code == "model_material_limit_reached"
        )
        self.assertEqual(warning.entity_id, "24")

        with tempfile.TemporaryDirectory(prefix="psr-external-material-limit-") as temp:
            with StagingWorkspace.create(
                Path(temp),
                operation_identity=operation.map_identity,
            ) as workspace:
                workspace.write_bytes("game/GameInfo.txt", _staging_gameinfo())
                result = generate_and_validate(
                    workspace,
                    filesystem,
                    operation,
                    materials,
                    skin_layout,
                    crowbar_command=(CROWBAR,),
                    studiomdl_command=(STUDIOMDL,),
                )
                self.assertEqual(len(result.materials), 23)
                rejected_skin = next(
                    item
                    for item in materials.colored_skins
                    if item.render_color == (0, 0, 24)
                )
                overflow_family = list(layout.families[0])
                for slot, logical_path in zip(
                    rejected_skin.material_slots,
                    rejected_skin.logical_colored_materials,
                ):
                    overflow_family[slot] = (
                        logical_path.casefold()
                        .removeprefix("materials/")
                        .removesuffix(".vmt")
                    )
                overflow_mapping = replace(
                    layout.mappings[-1],
                    source_skin=rejected_skin.source_skin,
                    render_color=rejected_skin.render_color,
                    target_skin=len(layout.families),
                )
                overflow_layout = replace(
                    layout,
                    families=layout.families + (tuple(overflow_family),),
                    mappings=layout.mappings + (overflow_mapping,),
                )
                overflow_plan = replace(
                    skin_layout,
                    layouts=(overflow_layout,),
                    assignments=tuple(
                        replace(
                            assignment,
                            target_skin=len(layout.families),
                            used_color_fallback=False,
                        )
                        if assignment.entity_id == "24"
                        else assignment
                        for assignment in skin_layout.assignments
                    ),
                )

        with tempfile.TemporaryDirectory(prefix="psr-external-material-overflow-") as temp:
            with StagingWorkspace.create(
                Path(temp),
                operation_identity=operation.map_identity,
            ) as workspace:
                workspace.write_bytes("game/GameInfo.txt", _staging_gameinfo())
                with patch("psr.assets.qc.MAX_STUDIO_MATERIALS", 32):
                    with self.assertRaises(GenerationError) as caught:
                        generate_and_validate(
                            workspace,
                            filesystem,
                            operation,
                            materials,
                            overflow_plan,
                            crowbar_command=(CROWBAR,),
                            studiomdl_command=(STUDIOMDL,),
                        )
                self.assertEqual(caught.exception.code, "studiomdl_failed")
                invocation = caught.exception.invocation
                self.assertIsNotNone(invocation)
                logs = invocation.stdout + invocation.stderr
                self.assertIn(b"Too many materials used, max 32", logs)

    def test_replace_patch_and_source_patch_full_copy_compile_from_overlay(self) -> None:
        source_path = "materials/models/props_se/book/book_small_face_01.vmt"
        base_path = "materials/models/props_se/book/psr_validation_base.vmt"
        scenarios = (
            (
                "replace",
                b'''VertexLitGeneric
{
    "$basetexture" "models/props_se/book/book_small_face_01"
    "$color2" "{255 255 255}"
}
''',
                None,
                "patch",
                "replace",
            ),
            (
                "source_patch",
                f'''Patch
{{
    "include" "{base_path}"
    "insert"
    {{
        "$color2" "{{255 255 255}}"
    }}
}}
'''.encode("ascii"),
                b'''VertexLitGeneric
{
    "$basetexture" "models/props_se/book/book_small_face_01"
}
''',
                "full_copy",
                "replace",
            ),
        )

        with tempfile.TemporaryDirectory(prefix="psr-external-vmt-") as temp:
            root = Path(temp)
            for name, source, base, expected_mode, expected_assignment in scenarios:
                with self.subTest(name=name):
                    overlay = root / name / "overlay"
                    _write_logical(overlay, source_path, source)
                    if base is not None:
                        _write_logical(overlay, base_path, base)
                    filesystem = _overlay_filesystem(overlay)
                    operation, materials, skin_layout = self.plans(
                        filesystem,
                        f"maps/psr_external_sdk_{name}.vmf",
                    )
                    self.assertEqual(len(materials.colored_materials), 1)
                    material = materials.colored_materials[0]
                    self.assertEqual(material.generation_mode, expected_mode)
                    self.assertEqual(material.color_assignment, expected_assignment)

                    with StagingWorkspace.create(
                        root / name / "staging",
                        operation_identity=operation.map_identity,
                    ) as workspace:
                        workspace.write_bytes(
                            "game/GameInfo.txt",
                            _staging_gameinfo(overlay),
                        )
                        result = generate_and_validate(
                            workspace,
                            filesystem,
                            operation,
                            materials,
                            skin_layout,
                            crowbar_command=(CROWBAR,),
                            studiomdl_command=(STUDIOMDL,),
                        )

                        self.assertEqual(len(result.models), 1)
                        self.assertEqual(len(result.materials), 1)
                        self.assertEqual(
                            result.materials[0].generated.generation_mode,
                            expected_mode,
                        )
                        logs = (
                            result.models[0].compile_invocation.stdout
                            + result.models[0].compile_invocation.stderr
                        )
                        self.assertNotIn(b"KeyValues Error", logs)


if __name__ == "__main__":
    unittest.main()
