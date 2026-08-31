"""PSR-specific srctools hook: do not bundle its unrelated FGD database."""

# The upstream srctools hook unconditionally adds ~1 MiB fgd.lzma. PSR uses
# only KeyValues, filesys/VPK, MDL and VMT modules, so no package data is needed.
datas = []
excludedimports = [
    "asyncio",
    "concurrent",
    "multiprocessing",
    "PIL",
    "tkinter",
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
]
