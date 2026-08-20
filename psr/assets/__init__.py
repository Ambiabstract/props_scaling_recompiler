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
from .mdl import (
    MaterialReferenceMetadata,
    SourceAssetInspectionError,
    SourceAssetMetadata,
    SourceFileMetadata,
    inspect_source_model,
)

__all__ = [
    "AssetProvenance",
    "MaterialReferenceMetadata",
    "MountedSearchPath",
    "OrderedAssetFileSystem",
    "ResolvedAsset",
    "SearchPathDiagnostic",
    "SearchPathParseError",
    "SearchPathPlan",
    "SearchPathSpec",
    "SourceAssetInspectionError",
    "SourceAssetMetadata",
    "SourceFileMetadata",
    "inspect_source_model",
    "normalize_logical_path",
    "parse_gameinfo_search_paths",
    "parse_search_paths_text",
    "plan_search_paths",
]
