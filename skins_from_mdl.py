#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# skins_from_mdl_fixed.py
# Usage: python skins_from_mdl_fixed.py path\to\model.mdl
from __future__ import annotations
import os, sys, struct
from typing import List, Tuple

IDST = b'IDST'  # Source MDL
# Оффсеты поля «текстуры» и «скин-таблица» в studiohdr_t для v48 (подходят и для v44/46):
OFF_TEXTURE_COUNT   = 0xCC  # int numtextures
OFF_TEXTURE_INDEX   = 0xD0  # int textureindex (офсет в ФАЙЛЕ от начала studiohdr)
OFF_TXDIR_COUNT     = 0xD4  # int numcdtextures (не используем)
OFF_TXDIR_INDEX     = 0xD8  # int cdtextureindex (не используем)
OFF_SKINREF_COUNT   = 0xDC  # int numskinref (кол-во столбцов)
OFF_SKINFAM_COUNT   = 0xE0  # int numskinfamilies (кол-во строк)
OFF_SKIN_INDEX      = 0xE4  # int skinindex (офсет USHORT-таблицы индексов)
STUDIOHDR_SIZE_MIN  = 0x1A0 # безопасный размер заголовка для v48 (с запасом)
MSTEXTURE_SIZE      = 64    # размер mstudiotexture_t в файле
# Внутри mstudiotexture_t в самом начале лежит int name_offset (смещение относ. начала самой структуры)
OFF_MSTEX_NAMEOFF   = 0x00

def read_u32(f) -> int:
    data = f.read(4)
    if len(data) < 4: raise ValueError("unexpected EOF while reading <I>")
    return struct.unpack('<I', data)[0]

def read_i32_at(f, off: int) -> int:
    f.seek(off)
    return read_u32(f)

def read_cstring_at(f, off: int, limit: int = 512) -> str:
    """Читает ASCIIZ-строку по абсолютному смещению с верхним лимитом длины."""
    f.seek(off)
    out = bytearray()
    for _ in range(limit):
        b = f.read(1)
        if not b or b == b'\x00':
            break
        out += b
    return out.decode('ascii', errors='ignore')

def read_header_fields(f) -> Tuple[int,int,int,int,int,int]:
    """Возвращает ключевые поля из studiohdr_t."""
    f.seek(0)
    magic = f.read(4)
    if magic != IDST:
        raise ValueError("Не похоже на Source MDL (ожидался IDST).")
    version = read_u32(f)  # можно вывести при отладке

    # Убедимся, что заголовок вообще присутствует
    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    if file_size < STUDIOHDR_SIZE_MIN:
        raise ValueError("Слишком короткий MDL (битый заголовок).")

    # Читаем нужные оффсеты/счётчики
    texture_count = read_i32_at(f, OFF_TEXTURE_COUNT)
    texture_index = read_i32_at(f, OFF_TEXTURE_INDEX)
    skinref_count = read_i32_at(f, OFF_SKINREF_COUNT)
    skinfam_count = read_i32_at(f, OFF_SKINFAM_COUNT)
    skin_index    = read_i32_at(f, OFF_SKIN_INDEX)

    # Базовые проверки на адекватность
    if not (0 <= texture_index < file_size):
        raise ValueError("Некорректный textureindex.")
    if not (0 <= skin_index < file_size):
        raise ValueError("Некорректный skinindex.")
    if texture_count <= 0 or skinref_count <= 0 or skinfam_count <= 0:
        # У моделей без скинов skinfam_count обычно 1; 0 — странно
        raise ValueError("Некорректные размеры таблиц (возможно, не Source MDL 44/46/48).")

    return (version, texture_count, texture_index, skinref_count, skinfam_count, skin_index)

def extract_texture_names(f, texture_count: int, texture_index: int) -> List[str]:
    """Возвращает список имён материалов из массива mstudiotexture_t."""
    names: List[str] = []
    for i in range(texture_count):
        entry_off = texture_index + i * MSTEXTURE_SIZE
        # name_offset хранится в первых 4 байтах структуры и задаёт смещение от entry_off
        f.seek(entry_off + OFF_MSTEX_NAMEOFF)
        data = f.read(4)
        if len(data) < 4:
            raise ValueError("EOF при чтении mstudiotexture_t.name_offset")
        (name_rel,) = struct.unpack('<i', data)
        name_abs = entry_off + name_rel
        names.append(read_cstring_at(f, name_abs))
    return names

def extract_skin_families(f, skinref_count: int, skinfam_count: int, skin_index: int, texture_names: List[str]) -> List[List[str]]:
    """Собирает таблицу skin families (индексы -> имена материалов)."""
    f.seek(skin_index)
    # Таблица хранится как массив uint16 длиной skinfam_count * skinref_count
    total = skinfam_count * skinref_count
    raw = f.read(total * 2)
    if len(raw) < total * 2:
        raise ValueError("EOF при чтении skin-таблицы.")
    idxs = struct.unpack('<' + 'H'*total, raw)

    fams: List[List[str]] = []
    for r in range(skinfam_count):
        row: List[str] = []
        base = r * skinref_count
        for c in range(skinref_count):
            t = idxs[base + c]
            row.append(texture_names[t] if 0 <= t < len(texture_names) else '')
        fams.append(row)
    return fams

def get_skin_families(mdl_path: str) -> List[List[str]]:
    with open(mdl_path, 'rb') as f:
        version, tcnt, toff, refcnt, famcnt, skoff = read_header_fields(f)
        textures = extract_texture_names(f, tcnt, toff)
        return extract_skin_families(f, refcnt, famcnt, skoff, textures)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python skins_from_mdl_fixed.py path\\to\\model.mdl")
        sys.exit(2)
    mdl = sys.argv[1]
    try:
        fams = get_skin_families(mdl)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"Found {len(fams)} skin families")
    for i, fam in enumerate(fams):
        print(f"\nSkin {i}:")
        for mat in fam:
            print("  ", mat)
