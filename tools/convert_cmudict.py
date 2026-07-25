#!/usr/bin/env python3
"""Convert CMUdict ARPAbet pronunciations to Modern Mono phonetics."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data extracts" / "cmudict.dict"
OUTPUT = ROOT / "addon" / "data" / "CMUdict.tsv"

PHONEMES = {
    "AA": "AA", "AE": "AE", "AO": "AA", "AW": "AW", "AY": "AY",
    "EH": "EH", "ER": "ER", "EY": "EY", "IH": "IH", "IY": "IY",
    "OW": "OW", "OY": "OY", "UH": "UH", "UW": "UW",
    "B": "b", "CH": "tSH", "D": "d", "DH": "DH", "F": "f",
    "G": "g", "HH": "h", "JH": "dZH", "K": "k", "L": "l",
    "M": "m", "N": "n", "NG": "NG", "P": "p", "R": "r",
    "S": "s", "SH": "SH", "T": "t", "TH": "TH", "V": "v",
    "W": "w", "Y": "y", "Z": "z", "ZH": "ZH",
}


def convert_symbol(symbol: str) -> str:
    match = re.fullmatch(r"([A-Z]+)([012]?)", symbol)
    if not match:
        raise ValueError(symbol)
    name, stress = match.groups()
    if name == "AH":
        phoneme = "IX" if stress == "0" else "AH"
    else:
        phoneme = PHONEMES[name]
    prefix = "'" if stress == "1" else '"' if stress == "2" else ""
    return prefix + phoneme


def convert_pronunciation(symbols: list[str]) -> str:
    result = []
    for index, symbol in enumerate(symbols):
        name = re.sub(r"[012]$", "", symbol)
        following = re.sub(r"[012]$", "", symbols[index + 1]) if index + 1 < len(symbols) else ""
        if name == "AO" and following == "R":
            # Monolog has no dedicated caught/thought vowel. AA is the best
            # general match, but its rhotic sequence is much too open
            # ("short" becomes "shart"). Preserve Monolog's OW+r sequence.
            stress = symbol[-1] if symbol[-1:] in "012" else ""
            result.append(("'" if stress == "1" else '"' if stress == "2" else "") + "OW")
        elif name == "L" and (not following or following not in {
            "AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
            "IH", "IY", "OW", "OY", "UH", "UW",
        }):
            result.append("LX")
        else:
            result.append(convert_symbol(symbol))
    return "".join(result)


def main() -> None:
    entries: dict[str, str] = {}
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(";;;"):
            continue
        word, symbols = line.split(maxsplit=1)
        symbols = symbols.split("#", 1)[0].strip()
        word = re.sub(r"\(\d+\)$", "", word)
        if word in entries or not re.fullmatch(r"[a-z]+(?:'[a-z]+)?", word):
            continue
        entries[word] = convert_pronunciation(symbols.split())
    lines = ["# CMUdict fallback pronunciations converted from ARPAbet"]
    lines.extend(f"{word}\t{phonetics}" for word, phonetics in sorted(entries.items()))
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} entries to {OUTPUT}")


if __name__ == "__main__":
    main()
