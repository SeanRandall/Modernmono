#!/usr/bin/env python3
"""Extract Monolog FB_DEFLT.DIC entries without loading original code."""

from __future__ import annotations

import argparse
import csv
import json
import struct
from pathlib import Path


def c_string(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"unterminated string at {offset:#x}")
    return data[offset:end].decode("ascii")


def parse_dictionary(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError("dictionary is too short")
    count, string_bytes = struct.unpack_from("<HH", data)
    index_size = count * 10
    strings_offset = 4 + index_size
    if strings_offset + string_bytes != len(data):
        raise ValueError(
            f"size mismatch: header describes {strings_offset + string_bytes} bytes, "
            f"file has {len(data)}"
        )
    string_data = data[strings_offset:]
    entries = []
    for index in range(count):
        active, spelling_offset, phonetics_offset = struct.unpack_from(
            "<HII", data, 4 + index * 10
        )
        if spelling_offset >= string_bytes or phonetics_offset >= string_bytes:
            raise ValueError(f"entry {index} has an out-of-range string offset")
        entries.append(
            {
                "index": index,
                "active": bool(active),
                "raw_active": active,
                "spelling_offset": spelling_offset,
                "phonetics_offset": phonetics_offset,
                "spelling": c_string(string_data, spelling_offset),
                "phonetics": c_string(string_data, phonetics_offset),
            }
        )
    return {
        "file": path.name,
        "entry_count": count,
        "string_bytes": string_bytes,
        "strings_offset": strings_offset,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dictionary", type=Path)
    parser.add_argument("--output", type=Path, default=Path("analysis/dictionary"))
    args = parser.parse_args()
    parsed = parse_dictionary(args.dictionary)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dictionary.json").write_text(
        json.dumps(parsed, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "dictionary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["index", "active", "spelling", "phonetics"]
        )
        writer.writeheader()
        for entry in parsed["entries"]:
            writer.writerow({key: entry[key] for key in writer.fieldnames})
    active = sum(entry["active"] for entry in parsed["entries"])
    print(f"{parsed['entry_count']} entries ({active} active), strings at {parsed['strings_offset']:#x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
