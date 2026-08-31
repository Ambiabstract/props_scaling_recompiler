# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).parent.resolve()
hook_root = project_root / "packaging" / "hooks"

a = Analysis(
    [str(project_root / "psr_entrypoint.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[str(hook_root)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "asyncio",
        "concurrent",
        "multiprocessing",
        "PIL",
        "tkinter",
        "unittest",
        "wx",
        "srctools.bsp",
        "srctools.choreo",
        "srctools.dmx",
        "srctools.fgd",
        "srctools.packlist",
        "srctools.run",
        "srctools.steam",
        "srctools.vmf",
        "srctools.vtf",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="props_scaling_recompiler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(project_root / "props_scaling_recompiler_icon_v3.ico"),
)
