#!/usr/bin/env python3
"""Monolog's RULE/HASH spelling-to-phonetics fallback.

This is a direct, bounds-checked port of FB_SPCH's FUN_1000_0d00 and
FUN_1000_11c8.  The rules themselves remain in the selected voice DLL.
"""

from __future__ import annotations

import struct
from pathlib import Path

from tools.ne_extract import parse_ne


# The comma-terminated character classes at FB_SPCH data offsets 0x90..0x252.
# FUN_1000_10a2 selects one of these for the lower-case rule operators.
CLASSES = {
    "%": ",ABLY,AL,ANCE,ANT,AR,ED,EN,ER,EY,E,IBLE,IBLY,ISH,ING,IN',IVE,OR,UR,FUL,LESS,MENT,U,",
    "@": " ,BO,BUS,CUS,EB,ED,ERP,ER,ME,MI,MOC,NE,NI,NOC,NU,ORP,ORTNI,PO,PUS,REDNU,REP,REVO,RETNI,RI,RUS,SID,SIM,SNART,SUS,VID,XE,",
    "b": "ABL,AL,AN,AR,AS,AT,A',A ,CALLY,CLY,CS ,C',C ,EN,ET,O,U,",
    "n": "M,N,",
    "p": "B,F,P,PH,V,W,",
    "s": " ,',S ,S',ED ,ED',ES ,ES',E ,E',",
    "t": "BE,COM,CON,DE,DIS,DIV,EM,EN,EX,IM,INTRO,IN,IR,MIS,OB,OP,OVER,PER,PRE,PRO,RE,SUB,SUP,SUR,SUS,TRANS,UNDER,UN,",
    "w": "W,UQ,",
    "y": "B,C,F,G,H,K,M,N,P, ,",
    "z": " ,',S ,S',ELY,LY,ABLE,ABLY,IBLE,IBLY,ING ,ING',INGS ,IN',ER ,ER',ERS ,ED ,ED',EDS ,ES ,ES',E ,E',OR ,OR',ORS ,FUL,LESS,MENT,UE ,UE',UES ,",
}

# FB_SPCH's table at data offset 0x10. Bits are consumed by 0x0eb6..0x1056.
LETTER_FLAGS = {
    "A": 0x10, "B": 0x09, "C": 0x03, "D": 0x0D, "E": 0x30,
    "F": 0x01, "G": 0x0B, "H": 0x01, "I": 0x30, "J": 0x0F,
    "K": 0x01, "L": 0x0D, "M": 0x09, "N": 0x0D, "O": 0x10,
    "P": 0x01, "Q": 0x01, "R": 0x0D, "S": 0x07, "T": 0x05,
    "U": 0x30, "V": 0x09, "W": 0x0D, "X": 0x03, "Y": 0x30,
    "Z": 0x0F,
}


class RulePronouncer:
    def __init__(self, voice: Path):
        _, resources = parse_ne(Path(voice))
        by_type = {record["type"]: blob for record, blob in resources}
        try:
            self.rules = by_type["RULE"]
            hash_data = by_type["HASH"]
        except KeyError as error:
            raise ValueError(f"voice has no {error.args[0]} pronunciation resource") from None
        self.hash = struct.unpack_from("<26H", hash_data)

    @staticmethod
    def _flag(text: str, pos: int, mask: int) -> bool:
        return 0 <= pos < len(text) and bool(LETTER_FLAGS.get(text[pos], 0) & mask)

    @staticmethod
    def _class_match(text: str, pos: int, direction: int, choices: str) -> int | None:
        # FUN_1000_10a2 tries each comma-terminated alternative in order.
        for choice in choices.split(","):
            if not choice:
                continue
            end = pos + direction * len(choice)
            candidate = text[pos:end] if direction > 0 else text[end + 1:pos + 1][::-1]
            if candidate == choice:
                return end
        return None

    def _consume(self, text: str, pos: int, direction: int, operator: str) -> int | None:
        if operator in CLASSES:
            return self._class_match(text, pos, direction, CLASSES[operator])
        mask = {"&": 0x02, "$": 0x10, "+": 0x20, ".": 0x08}.get(operator)
        if mask is not None:
            return pos + direction if self._flag(text, pos, mask) else None
        if operator in ("^", "*", ":"):
            if not self._flag(text, pos, 0x01) or text[pos] == "X":
                return None
            # The original treats QU and CH/SH as a single consonant while
            # walking in either direction.
            if direction > 0 and text[pos:pos + 2] in ("QU", "CH", "SH"):
                return pos + 2
            if direction < 0 and text[max(0, pos - 1):pos + 1] in ("QU", "CH", "SH"):
                return pos - 2
            return pos + direction
        if operator == "!":
            if text[pos:pos + 1] == "S" and text[pos + 1:pos + 2] < "A":
                return pos
            if text[pos:pos + 2] == "LY" and text[pos + 2:pos + 3] < "A":
                return pos
            return None
        return None

    def _match(self, text: str, pos: int, pattern: str, direction: int) -> int | None:
        if not pattern:
            return pos
        operator = pattern[0]
        rest = pattern[1:]
        if operator in ("#", "*"):
            mask = 0x10 if operator == "#" else 0x01
            positions: list[int] = []
            current = pos
            while self._flag(text, current, mask):
                consumed = self._consume(
                    text, current, direction, "$" if operator == "#" else "*"
                )
                if consumed is None:
                    break
                positions.append(consumed)
                current = consumed
            for candidate in positions:
                matched = self._match(text, candidate, rest, direction)
                if matched is not None:
                    return matched
            return None
        if operator in (":", "v"):
            mask = 0x01 if operator == ":" else 0x10
            candidates = [pos]
            current = pos
            while self._flag(text, current, mask):
                consumed = self._consume(text, current, direction, "*" if operator == ":" else "$")
                if consumed is None:
                    break
                candidates.append(consumed)
                current = consumed
            for candidate in candidates:
                matched = self._match(text, candidate, rest, direction)
                if matched is not None:
                    return matched
            return None
        if operator == "!":
            return self._match(text, pos, rest, direction) if self._consume(text, pos, direction, operator) is not None else None
        if operator in CLASSES or operator in "&$+.^":
            consumed = self._consume(text, pos, direction, operator)
            return None if consumed is None else self._match(text, consumed, rest, direction)
        if not 0 <= pos < len(text) or text[pos] != operator:
            return None
        return self._match(text, pos + direction, rest, direction)

    def pronounce(self, word: str) -> str:
        return self.pronounce_with_category(word)[0]

    def pronounce_with_category(self, word: str) -> tuple[str, int]:
        """Return pronunciation and the rule's sentence-prosody category."""
        word = word.upper()
        if not word or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ'" for c in word):
            raise ValueError(f"RULE fallback only accepts alphabetic words: {word!r}")
        text = "   " + word + "   "
        pos = 3
        output: list[str] = []
        while text[pos] != " ":
            if text[pos] in "'.":
                pos += 1
                continue
            start = self.hash[ord(text[pos]) - ord("A")]
            cursor = start
            while cursor < len(self.rules):
                length = self.rules[cursor]
                if length == 0 or cursor + length > len(self.rules):
                    break
                rule = self.rules[cursor + 1:cursor + length].decode("ascii")
                parts = rule.split("{")
                if len(parts) >= 5:
                    key, left, right, replacement = parts[:4]
                    after = pos + 1 + len(key)
                    if (text[pos + 1:after] == key and
                            self._match(text, pos - 1, left, -1) is not None and
                            self._match(text, after, right, 1) is not None):
                        output.append(replacement)
                        pos = after
                        break
                cursor += length
            else:
                raise ValueError(f"no RULE pronunciation matched {word!r} at {word[pos - 3:]!r}")
            if cursor >= len(self.rules) or not length:
                raise ValueError(f"no RULE pronunciation matched {word!r}")
        result = "".join(output)
        category = 2 if result.endswith("*") else 1
        return result.removesuffix("*"), category
