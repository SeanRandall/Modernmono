#!/usr/bin/env python3
"""Render a repeatable pronunciation corpus from FB_DEFLT.DIC."""

from __future__ import annotations

import argparse
import csv
import wave
from pathlib import Path

try:
    from tools.dictionary_extract import parse_dictionary
    from tools.render_units import render
except ModuleNotFoundError:
    from dictionary_extract import parse_dictionary
    from render_units import render


DEFAULT_WORDS = ("ABSENCE", "ACTUALLY", "ALGORITHM", "ANOTHER", "DICTIONARY", "YOUTH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("voice", type=Path)
    parser.add_argument("dictionary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("words", nargs="*", default=DEFAULT_WORDS)
    parser.add_argument("--pitch", type=int, choices=range(10), default=5)
    parser.add_argument("--speed", type=int, choices=range(10), default=5)
    args = parser.parse_args()
    dictionary = parse_dictionary(args.dictionary)
    entries = {entry["spelling"].upper(): entry for entry in dictionary["entries"]}
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for requested in args.words:
        word = requested.upper()
        if word not in entries:
            raise SystemExit(f"dictionary has no entry for {requested!r}")
        entry = entries[word]
        output = args.output / f"{word.lower()}.wav"
        units = render(
            args.voice,
            entry["phonetics"],
            output,
            mode="scheduled",
            pitch=args.pitch,
            speed=args.speed,
        )
        with wave.open(str(output), "rb") as stream:
            frames = stream.getnframes()
            sample_rate = stream.getframerate()
        rows.append(
            {
                "word": word,
                "phonetics": entry["phonetics"],
                "units": len(units),
                "frames": frames,
                "sample_rate": sample_rate,
                "duration_seconds": f"{frames / sample_rate:.6f}",
                "file": output.name,
            }
        )
        print(f"{word}: {entry['phonetics']} -> {output}")
    with (args.output / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
