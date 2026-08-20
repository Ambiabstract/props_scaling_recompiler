"""Source asset discovery, resolution, inspection, and tool adapters."""

from .searchpaths import (
    AssetProvenance,
    MountedSearchPath,
    OrderedAssetFileSystem,
    ResolvedAsset,
    SearchPathDiagnostic,
    SearchPathParseError,
    SearchPathPlan,
    SearchPathSpec,
    normalize_logical_path,
    parse_gameinfo_search_paths,
    parse_search_paths_text,
    plan_search_paths,
)

__all__ = [
    "AssetProvenance",
    "MountedSearchPath",
    "OrderedAssetFileSystem",
    "ResolvedAsset",
    "SearchPathDiagnostic",
    "SearchPathParseError",
    "SearchPathPlan",
    "SearchPathSpec",
    "normalize_logical_path",
    "parse_gameinfo_search_paths",
    "parse_search_paths_text",
    "plan_search_paths",
]
