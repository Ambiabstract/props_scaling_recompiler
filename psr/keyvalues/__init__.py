"""Source-preserving Valve KeyValues parsing and targeted edits."""

from .vmf import (
    Block,
    Document,
    Property,
    Token,
    VmfParseError,
    iter_blocks,
    parse_vmf,
)

__all__ = [
    "Block",
    "Document",
    "Property",
    "Token",
    "VmfParseError",
    "iter_blocks",
    "parse_vmf",
]
