#!/usr/bin/env python3
"""Decode the top-level ProVoice resource relationships and PCM unit data."""

from __future__ import annotations

import argparse
import json
import struct
import wave
from pathlib import Path

from .ne_extract import parse_ne


def inspect_voice(path: Path) -> tuple[dict, dict[int, bytes]]:
    metadata, resources = parse_ne(path)
    by_type_id = {(record["type"], record["id"]): blob for record, blob in resources}
    count_blob = next(blob for record, blob in resources if record["name"] == "NUMPCMRESOURCES")
    resource_count = struct.unpack_from("<H", count_blob)[0]
    inst = by_type_id[("INST", 256)]
    demi = by_type_id[("DEMI", 257)]
    flags, phoneme_count, sample_rate, bits_per_sample, nominal_pitch, pitch_bias = struct.unpack_from(
        "<HHHHHh", demi
    )
    matrix_items = phoneme_count * phoneme_count
    matrix_a = list(struct.unpack_from(f"<{matrix_items}h", demi, 12))
    matrix_b = list(struct.unpack_from(f"<{matrix_items}h", demi, 12 + matrix_items * 2))

    pcmd = {resource_id - 300: blob for (resource_type, resource_id), blob in by_type_id.items() if resource_type == "PCMD"}
    if len(pcmd) != resource_count:
        raise ValueError(f"NUMPCMRESOURCES says {resource_count}, found {len(pcmd)}")

    units = []
    for index, (unit_flags, byte_length, resource_index, byte_offset) in enumerate(
        struct.iter_unpack("<HHHH", inst)
    ):
        if resource_index not in pcmd:
            raise ValueError(f"unit {index} references missing PCMD index {resource_index}")
        if byte_offset + byte_length > len(pcmd[resource_index]):
            raise ValueError(f"unit {index} extends beyond PCMD index {resource_index}")
        units.append(
            {
                "index": index,
                "flags": unit_flags,
                "byte_length": byte_length,
                "sample_count": byte_length // (bits_per_sample // 8),
                "resource_index": resource_index,
                "resource_id": resource_index + 300,
                "byte_offset": byte_offset,
            }
        )
    manifest = {
        "file": path.name,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "channels": 1,
        "metadata_flags": flags,
        "phoneme_count": phoneme_count,
        "nominal_pitch": nominal_pitch,
        "pitch_bias": pitch_bias,
        "pcmd_resource_count": resource_count,
        "unit_count": len(units),
        "transition_matrix_a": matrix_a,
        "transition_matrix_b": matrix_b,
        "units": units,
    }
    return manifest, pcmd


def write_unit_wav(manifest: dict, pcmd: dict[int, bytes], unit_index: int, output: Path) -> None:
    unit = manifest["units"][unit_index]
    start = unit["byte_offset"]
    pcm = pcmd[unit["resource_index"]][start : start + unit["byte_length"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(manifest["channels"])
        stream.setsampwidth(manifest["bits_per_sample"] // 8)
        stream.setframerate(manifest["sample_rate"])
        stream.writeframes(pcm)


def transition_units(manifest: dict, left: int, right: int, matrix: str) -> list[int]:
    """Return the terminated INST chain selected for a phoneme pair."""
    count = manifest["phoneme_count"]
    if not 0 <= left < count or not 0 <= right < count:
        raise ValueError(f"phoneme pair is out of range: {left}, {right}")
    key = f"transition_matrix_{matrix}"
    start = manifest[key][left * count + right]
    if start == -1:
        return []
    result = []
    while True:
        if not 0 <= start < len(manifest["units"]):
            raise ValueError(f"transition chain escaped INST at unit {start}")
        result.append(start)
        if manifest["units"][start]["flags"] & 0x100:
            return result
        start += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("voice", type=Path)
    parser.add_argument("--output", type=Path, default=Path("analysis/voice"))
    parser.add_argument("--unit", type=int, help="also export this raw INST unit as WAV")
    args = parser.parse_args()
    manifest, pcmd = inspect_voice(args.voice)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_file = args.output / f"{args.voice.stem}_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if args.unit is not None:
        write_unit_wav(manifest, pcmd, args.unit, args.output / f"unit_{args.unit}.wav")
    print(
        f"{args.voice.name}: {manifest['sample_rate']} Hz/{manifest['bits_per_sample']} bit, "
        f"{manifest['phoneme_count']} phonemes, {manifest['unit_count']} units, "
        f"{manifest['pcmd_resource_count']} PCMD resources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
