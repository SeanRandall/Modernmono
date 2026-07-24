#!/usr/bin/env python3
"""Inventory and extract resources from 16-bit Windows NE executables.

This intentionally has no third-party dependencies.  It preserves resource
bytes verbatim and writes enough NE metadata to make reverse-engineering work
reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


class NEError(ValueError):
    pass


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def counted_string(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        return f"<bad-offset-{offset:#x}>"
    length = data[offset]
    return data[offset + 1 : offset + 1 + length].decode("latin-1", "replace")


def safe_name(value: str) -> str:
    result = "".join(c if c.isalnum() or c in "._-" else "_" for c in value)
    return result or "unnamed"


def parse_name_table(data: bytes, start: int, end: int) -> list[dict]:
    names: list[dict] = []
    pos = start
    while pos < end:
        length = data[pos]
        pos += 1
        if length == 0:
            break
        if pos + length + 2 > len(data):
            raise NEError("truncated NE name table")
        name = data[pos : pos + length].decode("latin-1", "replace")
        pos += length
        ordinal = u16(data, pos)
        pos += 2
        names.append({"name": name, "ordinal": ordinal})
    return names


def parse_ne(path: Path) -> tuple[dict, list[tuple[dict, bytes]]]:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise NEError("missing MZ header")
    ne = u32(data, 0x3C)
    if data[ne : ne + 2] != b"NE":
        raise NEError("missing NE header")

    segment_count = u16(data, ne + 0x1C)
    module_count = u16(data, ne + 0x1E)
    segment_table = ne + u16(data, ne + 0x22)
    resource_table = ne + u16(data, ne + 0x24)
    resident_table = ne + u16(data, ne + 0x26)
    module_table = ne + u16(data, ne + 0x28)
    imported_names = ne + u16(data, ne + 0x2A)
    nonresident_table = u32(data, ne + 0x2C)
    nonresident_size = u16(data, ne + 0x20)
    segment_shift = u16(data, ne + 0x32)

    segments = []
    for index in range(segment_count):
        pos = segment_table + index * 8
        sector, length, flags, minimum = struct.unpack_from("<HHHH", data, pos)
        segments.append(
            {
                "number": index + 1,
                "offset": sector << segment_shift,
                "length": length or 0x10000,
                "minimum_allocation": minimum or 0x10000,
                "flags": flags,
                "is_data": bool(flags & 1),
                "is_movable": bool(flags & 0x10),
                "is_preload": bool(flags & 0x40),
            }
        )

    modules = []
    for index in range(module_count):
        name_offset = u16(data, module_table + index * 2)
        modules.append(counted_string(data, imported_names + name_offset))

    resident_names = parse_name_table(data, resident_table, module_table)
    nonresident_names = (
        parse_name_table(data, nonresident_table, nonresident_table + nonresident_size)
        if nonresident_table and nonresident_size
        else []
    )

    # When the offsets are equal there is no resource table; the bytes at that
    # location are the resident-name table and must not be interpreted as one.
    has_resources = resource_table < resident_table
    shift = u16(data, resource_table) if has_resources else 0
    pos = resource_table + 2

    def resource_name(raw: int) -> tuple[str, int | None]:
        if raw & 0x8000:
            numeric = raw & 0x7FFF
            return str(numeric), numeric
        return counted_string(data, resource_table + raw), None

    resources: list[tuple[dict, bytes]] = []
    while has_resources:
        type_raw = u16(data, pos)
        pos += 2
        if type_raw == 0:
            break
        count = u16(data, pos)
        pos += 2
        reserved = u32(data, pos)
        pos += 4
        type_name, type_id = resource_name(type_raw)
        for _ in range(count):
            offset_units, length_units, flags, id_raw, handle, usage = struct.unpack_from(
                "<HHHHHH", data, pos
            )
            pos += 12
            name, numeric_id = resource_name(id_raw)
            offset = offset_units << shift
            length = length_units << shift
            blob = data[offset : offset + length]
            if len(blob) != length:
                raise NEError(f"truncated resource {type_name}/{name}")
            record = {
                "type": type_name,
                "type_id": type_id,
                "name": name,
                "id": numeric_id,
                "offset": offset,
                "length": length,
                "flags": flags,
                "handle": handle,
                "usage": usage,
                "type_reserved": reserved,
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
            resources.append((record, blob))

    metadata = {
        "file": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ne_header_offset": ne,
        "target_os": data[ne + 0x36],
        "expected_windows_version": {
            "major": data[ne + 0x3F],
            "minor": data[ne + 0x3E],
        },
        "automatic_data_segment": u16(data, ne + 0x0E),
        "initial_heap": u16(data, ne + 0x10),
        "initial_stack": u16(data, ne + 0x12),
        "entry_cs_ip": [u16(data, ne + 0x16), u16(data, ne + 0x14)],
        "entry_ss_sp": [u16(data, ne + 0x1A), u16(data, ne + 0x18)],
        "segment_alignment_shift": segment_shift,
        "segments": segments,
        "imported_modules": modules,
        "resident_names": resident_names,
        "nonresident_names": nonresident_names,
        "resources": [record for record, _ in resources],
    }
    return metadata, resources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("analysis/ne"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    all_metadata = []
    for path in args.inputs:
        try:
            metadata, resources = parse_ne(path)
        except NEError as error:
            print(f"{path}: {error!s}".encode("ascii", "backslashreplace").decode("ascii"))
            continue
        module_dir = args.output / safe_name(path.stem)
        module_dir.mkdir(parents=True, exist_ok=True)
        for record, blob in resources:
            filename = (
                f"type_{safe_name(record['type'])}__id_{safe_name(record['name'])}"
                f"__offset_{record['offset']:08x}.bin"
            )
            output_file = module_dir / filename
            # Avoid churning hundreds of Dropbox-synchronised files on every
            # inventory run.
            if not output_file.exists() or output_file.read_bytes() != blob:
                output_file.write_bytes(blob)
            record["extracted_file"] = str(output_file.as_posix())
        (module_dir / "inventory.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        all_metadata.append(metadata)
        print(f"{path.name}: {len(resources)} resources, {len(metadata['segments'])} segments")
    (args.output / "inventory.json").write_text(
        json.dumps(all_metadata, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
