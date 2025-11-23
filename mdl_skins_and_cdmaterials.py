#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# mdl_skins_and_cdmaterials.py
# Usage: python mdl_skins_and_cdmaterials.py path\to\model.mdl
from __future__ import annotations
import os, sys, struct
from typing import List, Tuple

MDL_IDST = b'IDST'  # Source MDL

# ---- studiohdr_t offsets (Source MDL v44/46/48) ----
MDL_OFF_TEXTURE_COUNT   = 0xCC  # int numtextures
MDL_OFF_TEXTURE_INDEX   = 0xD0  # int textureindex (file offset from start of studiohdr)
MDL_OFF_TXDIR_COUNT     = 0xD4  # int numcdtextures
MDL_OFF_TXDIR_INDEX     = 0xD8  # int cdtextureindex (file offset to array of int offsets-to-strings)
MDL_OFF_SKINREF_COUNT   = 0xDC  # int numskinref
MDL_OFF_SKINFAM_COUNT   = 0xE0  # int numskinfamilies
MDL_OFF_SKIN_INDEX      = 0xE4  # int skinindex (file offset to USHORT table)
MDL_STUDIOHDR_SIZE_MIN  = 0x1A0 # safe lower bound for header
MDL_MSTEXTURE_SIZE      = 64    # sizeof(mstudiotexture_t) in file
MDL_OFF_MSTEX_NAMEOFF   = 0x00  # int name_offset (relative to start of this struct)

def read_u32(f) -> int:
    d = f.read(4)
    if len(d) < 4: raise ValueError("unexpected EOF while reading <I>")
    return struct.unpack('<I', d)[0]

def read_i32_at(f, off: int) -> int:
    f.seek(off); return read_u32(f)

def read_cstring_at(f, off: int, limit: int = 1024) -> str:
    f.seek(off)
    out = bytearray()
    for _ in range(limit):
        b = f.read(1)
        if not b or b == b'\x00': break
        out += b
    return out.decode('ascii', errors='ignore')

def read_header_fields(f) -> Tuple[int,int,int,int,int,int,int,int]:
    f.seek(0)
    if f.read(4) != MDL_IDST:
        raise ValueError("Не похоже на Source MDL (ожидался IDST).")
    version = read_u32(f)

    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    if file_size < MDL_STUDIOHDR_SIZE_MIN:
        raise ValueError("Слишком короткий MDL (битый заголовок).")

    texture_count = read_i32_at(f, MDL_OFF_TEXTURE_COUNT)
    texture_index = read_i32_at(f, MDL_OFF_TEXTURE_INDEX)
    txdir_count   = read_i32_at(f, MDL_OFF_TXDIR_COUNT)
    txdir_index   = read_i32_at(f, MDL_OFF_TXDIR_INDEX)
    skinref_count = read_i32_at(f, MDL_OFF_SKINREF_COUNT)
    skinfam_count = read_i32_at(f, MDL_OFF_SKINFAM_COUNT)
    skin_index    = read_i32_at(f, MDL_OFF_SKIN_INDEX)

    # sanity
    for name, val in (("textureindex", texture_index), ("cdtextureindex", txdir_index), ("skinindex", skin_index)):
        if not (0 <= val < file_size):
            raise ValueError(f"Некорректный {name} ({val}).")

    if texture_count <= 0 or skinref_count <= 0 or skinfam_count <= 0:
        # у моделей без «вариантов» skinfam_count обычно 1
        raise ValueError("Некорректные размеры таблиц (ожидались значения > 0).")

    return (version, texture_count, texture_index, txdir_count, txdir_index,
            skinref_count, skinfam_count, skin_index)

def extract_texture_names(f, texture_count: int, texture_index: int) -> List[str]:
    names: List[str] = []
    for i in range(texture_count):
        entry_off = texture_index + i * MDL_MSTEXTURE_SIZE
        f.seek(entry_off + MDL_OFF_MSTEX_NAMEOFF)
        (name_rel,) = struct.unpack('<i', f.read(4))
        name_abs = entry_off + name_rel
        names.append(read_cstring_at(f, name_abs))
    return names

def extract_cdmaterials(f, txdir_count: int, txdir_index: int) -> List[str]:
    """Считывает массив путей из $cdmaterials. Формат: по адресу cdtextureindex лежит
    numcdtextures целых (int), каждый — абсолютный смещ. строки (от начала studiohdr)."""
    if txdir_count <= 0:
        return []

    f.seek(txdir_index)
    offs = struct.unpack('<' + 'i'*txdir_count, f.read(4*txdir_count))

    paths: List[str] = []
    for off in offs:
        s = read_cstring_at(f, off) if off != 0 else ""
        # нормализуем: без ведущего/замыкающего слэша, без префикса materials/
        s = s.replace('\\', '/').removeprefix('materials/').strip('/')
        paths.append(s)  # пустая строка допустима (корень materials)
    # уберём дубликаты с сохранением порядка
    seen = set(); uniq = []
    for p in paths:
        if p not in seen:
            seen.add(p); uniq.append(p)
    return uniq

def extract_skin_families(f, skinref_count: int, skinfam_count: int, skin_index: int, texture_names: List[str]) -> List[List[str]]:
    f.seek(skin_index)
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

def join_cdmat(cd: str, name: str) -> str:
    """Собирает относительный путь VMT: <cd>/<name> (без префикса materials/)."""
    cd = (cd or '').replace('\\', '/').strip('/')
    name = (name or '').replace('\\', '/').lstrip('/')
    return f"{cd}/{name}" if cd else name

def build_expanded_paths(families: List[List[str]], cdmaterials: List[str]) -> List[List[List[str]]]:
    """Для каждого материала возвращает все возможные пути с учётом cdmaterials.
    Результат: список скинов -> список материалов -> список candidate-путей."""
    expanded: List[List[List[str]]] = []
    for fam in families:
        fam_paths: List[List[str]] = []
        for name in fam:
            # если в name уже есть подкаталог (например props/wood01), он просто конкатенируется
            candidates = [join_cdmat(cd, name) for cd in (cdmaterials or [''])]
            # убираем дубли
            seen = set(); uniq = []
            for p in candidates:
                if p not in seen:
                    seen.add(p); uniq.append(p)
            fam_paths.append(uniq)
        expanded.append(fam_paths)
    return expanded

def get_skin_data(mdl_path: str):
    with open(mdl_path, 'rb') as f:
        (version, tcnt, toff, txdcnt, txdoff, refcnt, famcnt, skoff) = read_header_fields(f)
        textures = extract_texture_names(f, tcnt, toff)
        families = extract_skin_families(f, refcnt, famcnt, skoff, textures)
        cdmaterials = extract_cdmaterials(f, txdcnt, txdoff)
    return version, textures, families, cdmaterials

def find_varying_columns(families: List[List[str]]) -> List[int]:
    """Возвращает индексы колонок, в которых значения меняются между скинами."""
    if not families:
        return []
    cols = len(families[0])
    varying = []
    for c in range(cols):
        vals = {fam[c] for fam in families}
        if len(vals) > 1:
            varying.append(c)
    return varying

def print_qc_style(families: List[List[str]], varying_cols: List[int]) -> None:
    """Печатает QC-представление: по одному материалу на скин для изменяемых колонок.
    Если изменяемых несколько — печатаем список на строку, в порядке колонок."""
    print('\nQC-style $texturegroup (varying refs only):')
    print('$texturegroup "skinfamilies"')
    print('{')
    for fam in families:
        print('\t{')
        if not varying_cols:
            # Нет изменяемых колонок: всё фиксированно — покажем пустую строку-комментарий
            print('\t\t// (no varying materials)')
        else:
            for c in varying_cols:
                name = fam[c]
                print(f'\t\t"{name}.vmt"' if not name.lower().endswith('.vmt') else f'\t\t"{name}"')
        print('\t}')
    print('}')

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mdl", help="path\\to\\model.mdl")
    ap.add_argument("--only-varying", action="store_true",
                    help="Печатать только изменяемые материалы (QC-представление)")
    args = ap.parse_args()

    try:
        version, textures, families, cdmats = get_skin_data(args.mdl)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)

    print(f"MDL version: {version}")
    print(f"Textures in model: {len(textures)}")
    print(f"Skin families: {len(families)}")
    print(f"$cdmaterials ({len(cdmats)}):")
    for p in cdmats:
        print("  ", p if p else "(root)")

    varying_cols = find_varying_columns(families)

    '''
    if not args.only-varying:
        # Полный «сыро́й» вывод (как раньше)
        for i, fam in enumerate(families):
            print(f"\nSkin {i}:")
            for mat in fam:
                print("  ", mat)
    
        # Плюс QC-представление для наглядности
        print_qc_style(families, varying_cols)
    else:
        # Только QC-представление
        print_qc_style(families, varying_cols)
    '''
    print_qc_style(families, varying_cols)

if __name__ == "__main__":
    main()