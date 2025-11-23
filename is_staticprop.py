#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, os, struct, sys

IDST = 0x54534449  # 'IDST' little-endian
STATIC_PROP_FLAG = 1 << 4
FLAGS_OFFSET = 0x98  # смещение поля studiohdr_t::flags для Source MDL

def read_flags(mdl_path: str) -> int:
    with open(mdl_path, 'rb') as f:
        # Проверяем сигнатуру и версию
        hdr = f.read(8)  # int id; int version;
        if len(hdr) < 8:
            raise ValueError("Файл слишком короткий для MDL заголовка")
        mdl_id, version = struct.unpack('<II', hdr)
        if mdl_id != IDST:
            raise ValueError("Не похоже на Source MDL (ожидался IDST)")
        # По данным Valve SDK (studio.h) поле flags идёт после 6 векторов (смещение 0x98)
        f.seek(FLAGS_OFFSET)
        data = f.read(4)
        if len(data) < 4:
            raise ValueError("Не удалось прочитать поле flags")
        (flags,) = struct.unpack('<I', data)
        return flags, version

def main():
    ap = argparse.ArgumentParser(description="Проверить, является ли MDL статик-пропом ($staticprop).")
    ap.add_argument("mdl", help="Путь к .mdl")
    args = ap.parse_args()

    mdl = os.path.abspath(args.mdl)
    if not mdl.lower().endswith('.mdl'):
        print("error: ожидался файл .mdl", file=sys.stderr); sys.exit(2)
    if not os.path.isfile(mdl):
        print(f"error: файл не найден: {mdl}", file=sys.stderr); sys.exit(2)

    try:
        (flags, version) = read_flags(mdl)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)

    is_static = (flags & STATIC_PROP_FLAG) != 0
    print(f"version={version}, flags=0x{flags:08X}")
    print("static_prop:", "yes" if is_static else "no")
    sys.exit(0 if is_static else 1)

if __name__ == "__main__":
    main()
    input(f"Main")

try:
    main()
    input(f"Kruto")
except Exception as e:
    print(f"Exception:")
    print(e)
    input(f"Pizdec")