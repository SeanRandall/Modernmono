#!/usr/bin/env python3
"""Convert the US-English IBMTTS/Eloquence dictionaries for Modern Mono."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from zipfile import ZipFile


SPR = {
    "a": "AA", "A": "AE", "e": "EY", "E": "EH", "i": "IY", "I": "IH",
    # FB_NGN has no separate /aw/ vowel from "law"; AA is its closest unit.
    "o": "OW", "c": "AA", "u": "UW", "U": "UH", "H": "AH", "R": "ER",
    "O": "OY", "W": "AW", "Y": "AY", "x": "AX", "X": "IX",
    "b": "b", "C": "tSH", "d": "d", "D": "DH", "f": "f", "F": "DX",
    "g": "g", "G": "NG", "h": "h", "J": "dZH", "k": "k", "l": "l",
    "m": "m", "M": "EM", "n": "n", "N": "EN", "p": "p", "r": "r",
    "s": "s", "S": "SH", "t": "t", "T": "TH", "v": "v", "w": "w",
    "y": "y", "z": "z", "Z": "ZH",
}
VOWELS = frozenset("aAeEiIocuUHR OWYxX".replace(" ", ""))
SYLLABLE_RE = re.compile(r"\.([012])([^.]*)")


def convert_spr(value: str) -> str | None:
    """Translate one plain IBM SPR expression to the FB_NGN alphabet."""
    if not (value.startswith("`[") and value.endswith("]")):
        return None
    result: list[str] = []
    position = 2
    body = value[2:-1]
    matches = list(SYLLABLE_RE.finditer(body))
    if not matches or matches[0].start() != 0:
        return None
    for match in matches:
        stress, syllable = match.groups()
        if any(char not in SPR for char in syllable):
            # Glottal stop has no recovered Modern Mono equivalent. Reject it
            # instead of silently changing the pronunciation.
            return None
        marker_at = next((i for i, char in enumerate(syllable) if char in VOWELS), None)
        if stress in "12" and marker_at is None:
            return None
        for index, char in enumerate(syllable):
            if index == marker_at and stress in "12":
                result.append("'" if stress == "1" else '"')
            result.append(SPR[char])
        position = match.end()
    if position != len(body):
        return None
    return "".join(result)


def read_dictionary(archive: ZipFile, name: str):
    for raw in archive.read(name).decode("cp1252").splitlines():
        if not raw or raw.startswith(";") or "\t" not in raw:
            continue
        spelling, value = raw.split("\t", 1)
        yield spelling, value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    entries: dict[str, tuple[str, str, str]] = {}
    rejected = 0
    with ZipFile(args.archive) as archive:
        # Later sources override earlier ones: curated main, then abbreviations.
        for filename, priority in (("ENURoot.dic", "fallback"), ("ENUmain.dic", "override"), ("ENUabbr.dic", "override")):
            for spelling, value in read_dictionary(archive, filename):
                converted = convert_spr(value)
                if converted is not None:
                    entries[spelling.casefold()] = (spelling, "phonetic", priority + ":" + converted)
                elif not value.startswith("`") and "`" not in value:
                    entries[spelling.casefold()] = (spelling, "text", priority + ":" + value)
                else:
                    rejected += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Converted from eigencrow/IBMTTSDictionaries US-English files.",
        "# Source data is CC0-1.0. Columns: spelling, kind, priority:value.",
    ]
    lines.extend("\t".join(entry) for entry in sorted(entries.values(), key=lambda item: item[0].casefold()))
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} entries; rejected {rejected} unsupported/embedded-command entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
