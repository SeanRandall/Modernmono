#!/usr/bin/env python3
"""Parser for the phonetic command language consumed by FB_NGN.

The token values and command kinds are recovered from FB_NGN's
FUN_1000_1e50.  AX and IX expansion mirrors FUN_1000_244a.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


PHONEMES = {
    "IY": 0x01, "IH": 0x02, "EH": 0x04, "AE": 0x05, "AH": 0x06,
    "AX": 0x07, "AA": 0x08, "UH": 0x09, "UW": 0x0A, "OW": 0x0B,
    "ER": 0x0C, "AY": 0x0D, "EY": 0x0E, "OY": 0x0F, "AW": 0x10,
    "LX": 0x11, "EM": 0x12, "EN": 0x13, "l": 0x14, "m": 0x15,
    "n": 0x16, "NG": 0x17, "y": 0x18, "r": 0x19, "w": 0x1A,
    "b": 0x1B, "d": 0x1C, "g": 0x1D, "v": 0x1E, "DH": 0x1F,
    "z": 0x20, "ZH": 0x21, "f": 0x22, "TH": 0x23, "s": 0x24,
    "SH": 0x25, "h": 0x26, "p": 0x27, "PX": 0x28, "t": 0x29,
    "TX": 0x2A, "DX": 0x2B, "k": 0x2C, "KX": 0x2D,
}

CONTROLS = {
    '"': (0x0D, "secondary_stress"),
    "'": (0x0C, "primary_stress"),
    "/": (0x0A, "pitch_up"),
    "[": (0x09, "pitch_small_up"),
    "\\": (0x0B, "pitch_down"),
    "]": (0x08, "pitch_small_down"),
    "^": (0x10, "boundary"),
    "|": (0x0F, "separator"),
}


@dataclass
class Token:
    offset: int
    text: str
    kind: int
    name: str
    value: int | None = None


def expand_reduced_vowels(text: str) -> str:
    # This odd-looking prefix is exactly what the original worker inserts.
    return text.replace("AX", "[AH").replace("IX", "[IH")


def _number(text: str, offset: int) -> tuple[int, int] | None:
    start = offset
    if offset < len(text) and text[offset] in "+-":
        offset += 1
    digits = offset
    while offset < len(text) and text[offset].isdigit():
        offset += 1
    if offset == digits:
        return None
    return int(text[start:offset]), offset


def parse(text: str, *, expand: bool = True) -> list[Token]:
    if expand:
        text = expand_reduced_vowels(text)
    tokens: list[Token] = []
    offset = 0
    phoneme_names = sorted(PHONEMES, key=len, reverse=True)
    while offset < len(text):
        start = offset
        char = text[offset]
        if char in CONTROLS:
            kind, name = CONTROLS[char]
            tokens.append(Token(start, char, kind, name))
            offset += 1
            continue
        # A hyphen is an intonation command only when not followed by digits;
        # otherwise it belongs to a signed numeric parameter.
        if char == "-" and (offset + 1 == len(text) or not text[offset + 1].isdigit()):
            tokens.append(Token(start, char, 0x0E, "pitch_fall"))
            offset += 1
            continue
        number = _number(text, offset)
        if number is not None:
            value, offset = number
            tokens.append(Token(start, text[start:offset], 5, "duration", value))
            continue
        if char in "FMSVPD":
            number = _number(text, offset + 1)
            if number is not None:
                value, end = number
                kinds = {"M": 1, "F": 2, "V": 3, "S": 4, "P": 6, "D": 7}
                names = {"M": "mouth", "F": "frequency", "V": "volume", "S": "speed", "P": "pitch", "D": "delay"}
                tokens.append(Token(start, text[start:end], kinds[char], names[char], value))
                offset = end
                continue
        match = next((name for name in phoneme_names if text.startswith(name, offset)), None)
        if match is not None:
            tokens.append(Token(start, match, 0, "phoneme", PHONEMES[match]))
            offset += len(match)
            continue
        raise ValueError(f"unrecognised phonetic input at {start}: {text[start:start + 12]!r}")
    return tokens


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phonetics")
    parser.add_argument("--no-expand", action="store_true")
    args = parser.parse_args()
    print(json.dumps([asdict(token) for token in parse(args.phonetics, expand=not args.no_expand)], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
