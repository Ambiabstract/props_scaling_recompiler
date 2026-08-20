"""Byte-oriented, source-preserving structural parser for Valve VMF text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence


class VmfParseError(ValueError):
    """Raised when VMF/Valve KeyValues syntax is structurally invalid."""


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    start: int
    end: int
    value: bytes = b""


@dataclass(frozen=True, slots=True)
class Property:
    key: bytes
    value: bytes
    key_token: Token
    value_token: Token


@dataclass(frozen=True, slots=True)
class Block:
    name: bytes
    name_token: Token
    open_token: Token
    close_token: Token
    properties: tuple[Property, ...]
    children: tuple["Block", ...]
    members: tuple[Property | "Block", ...]

    @property
    def start(self) -> int:
        return self.name_token.start

    @property
    def end(self) -> int:
        return self.close_token.end

    def direct_values(
        self,
        key: bytes,
        *,
        case_sensitive: bool = False,
    ) -> tuple[bytes, ...]:
        """Return repeated direct values without searching nested blocks."""
        expected = key if case_sensitive else key.lower()
        return tuple(
            prop.value
            for prop in self.properties
            if (prop.key if case_sensitive else prop.key.lower()) == expected
        )


@dataclass(frozen=True, slots=True)
class Document:
    source: bytes
    blocks: tuple[Block, ...]

    def line_number(self, offset: int) -> int:
        return self.source.count(b"\n", 0, offset) + 1


def parse_vmf(source: bytes) -> Document:
    """Parse VMF bytes while retaining source spans and repeated members."""
    return _Parser(source, _tokenize(source)).parse()


def iter_blocks(
    blocks: Sequence[Block],
    *,
    recursive: bool = False,
) -> Iterator[Block]:
    """Yield blocks in source order, optionally including descendants."""
    pending = list(reversed(blocks))
    while pending:
        block = pending.pop()
        yield block
        if recursive:
            pending.extend(reversed(block.children))


def _tokenize(source: bytes) -> tuple[Token, ...]:
    tokens: list[Token] = []
    index = 0
    while index < len(source):
        byte = source[index]
        if byte in b" \t\r\n":
            index += 1
            continue
        if byte == ord("/") and source[index:index + 2] == b"//":
            index += 2
            while index < len(source) and source[index] not in b"\r\n":
                index += 1
            continue
        if byte == ord("{"):
            tokens.append(Token("lbrace", index, index + 1))
            index += 1
            continue
        if byte == ord("}"):
            tokens.append(Token("rbrace", index, index + 1))
            index += 1
            continue
        if byte == ord('"'):
            start = index
            index += 1
            value = bytearray()
            while index < len(source):
                byte = source[index]
                if byte == ord("\\") and index + 1 < len(source):
                    value.append(source[index + 1])
                    index += 2
                    continue
                if byte == ord('"'):
                    index += 1
                    tokens.append(Token("scalar", start, index, bytes(value)))
                    break
                value.append(byte)
                index += 1
            else:
                _fail(source, start, "unterminated quoted token")
            continue

        start = index
        while index < len(source):
            byte = source[index]
            if byte in b" \t\r\n{}":
                break
            if byte == ord("/") and source[index:index + 2] == b"//":
                break
            index += 1
        if index == start:
            _fail(source, start, "unexpected byte")
        tokens.append(Token("scalar", start, index, source[start:index]))
    return tuple(tokens)


class _Parser:
    def __init__(self, source: bytes, tokens: Sequence[Token]) -> None:
        self.source = source
        self.tokens = tokens
        self.index = 0

    def parse(self) -> Document:
        blocks: list[Block] = []
        while self.index < len(self.tokens):
            if self.tokens[self.index].kind != "scalar":
                self.fail(self.tokens[self.index], "expected a top-level block name")
            blocks.append(self.parse_block())
        return Document(self.source, tuple(blocks))

    def parse_block(self) -> Block:
        name = self.take("scalar", "expected block name")
        opening = self.take("lbrace", "expected '{' after block name")
        properties: list[Property] = []
        children: list[Block] = []
        members: list[Property | Block] = []
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            if token.kind == "rbrace":
                self.index += 1
                return Block(
                    name.value,
                    name,
                    opening,
                    token,
                    tuple(properties),
                    tuple(children),
                    tuple(members),
                )
            if token.kind != "scalar":
                self.fail(token, "expected property key or nested block name")
            key = token
            self.index += 1
            if self.index >= len(self.tokens):
                self.fail(key, "missing value or block after key")
            following = self.tokens[self.index]
            if following.kind == "lbrace":
                self.index -= 1
                child = self.parse_block()
                children.append(child)
                members.append(child)
            elif following.kind == "scalar":
                self.index += 1
                prop = Property(key.value, following.value, key, following)
                properties.append(prop)
                members.append(prop)
            else:
                self.fail(following, "expected property value or '{'")
        self.fail(opening, "unclosed block")
        raise AssertionError("unreachable")

    def take(self, kind: str, message: str) -> Token:
        if self.index >= len(self.tokens):
            raise VmfParseError(f"{message} at end of file")
        token = self.tokens[self.index]
        if token.kind != kind:
            self.fail(token, message)
        self.index += 1
        return token

    def fail(self, token: Token, message: str) -> None:
        _fail(self.source, token.start, message)


def _fail(source: bytes, offset: int, message: str) -> None:
    line = source.count(b"\n", 0, offset) + 1
    raise VmfParseError(f"{message} at line {line}, byte {offset}")


__all__ = [
    "Block",
    "Document",
    "Property",
    "Token",
    "VmfParseError",
    "iter_blocks",
    "parse_vmf",
]
