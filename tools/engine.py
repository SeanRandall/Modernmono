#!/usr/bin/env python3
"""Persistent, pure-Python 22 kHz Monolog engine."""

from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from pathlib import Path

from tools.dictionary_extract import parse_dictionary
from tools.phonetics import PHONEMES, parse as parse_phonetics
from tools.render_units import _scheduled_frames, scheduled_events
from tools.text_to_phonetics import RulePronouncer
from tools.voice_inspect import inspect_voice


TOKEN_RE = re.compile(
    r"<<~.*?>>|<<[BFMTPVSbfmtpvs][0-9]?|>>|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"[$£]?[+-]?\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?|"
    r"<=|>=|<>|:=|\.{2,}|"
    r"[A-Za-z]+(?:'[A-Za-z]+)?(?:\.[A-Za-z]+)*\.?|[^\w\s]",
    re.IGNORECASE | re.UNICODE,
)

CMU_FUNCTION_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "he", "her", "him", "his", "i", "in", "is", "it",
    "me", "of", "on", "or", "she", "that", "the", "them", "they", "to",
    "us", "was", "we", "were", "with", "you", "your",
})

# Spoken forms recovered from FB_SPCH!FUN_1000_2fca.  Using words here lets
# the dictionary/rule resources choose the same phonemes as ordinary text.
SPOKEN_PUNCTUATION = {
    "#": ("number",),
    "%": ("percent",),
    "&": ("and",),
    "*": ("times",),
    "@": ("at",),
    "^": ("caret",),
    "=": ("equals",),
    "<": ("is", "less", "than"),
    "<=": ("is", "less", "than", "or", "equal", "to"),
    "<>": ("is", "not", "equal", "to"),
    ">": ("is", "greater", "than"),
    ">=": ("is", "greater", "than", "or", "equal", "to"),
    ":=": ("is", "assigned"),
    "+": ("plus",),
    "-": ("minus",),
    "$": ("dollar",),
}

# Fixed phrases appended directly by FB_SPCH!FUN_1000_2fca. Categories are
# the original @ markers consumed by the sentence-contour post-pass.
SPOKEN_PHONETICS = {
    "#": (("n'AHmbER", 2),), "%": (("p-ERs'EHnt", 0),),
    "&": (("-AEn", 2),), "*": (("t'AYmz", 2),), "@": (("-AEt", 3),),
    "^": (("k'EHr-IXt", 0),), "=": (("'IYkw-UHLXz", 2),),
    "+": (("pl'AHs", 2),), "-": (("m'AYnIXs", 2),),
    "$": (("d'AAl-ERs'AYn", 0),),
    ":=": (("-IXz", 2), ("-AXs'AYnd", 0)),
    "<": (("-IXz", 2), ("l'EHs", 0), ("DHEHn", 2)),
    "<=": (("-IXz", 2), ("l'EHs", 0), ("DHEHn", 2), ("-OWr", 2), ("'IYkw-UHLX", 0), ("t-UW", 2)),
    "<>": (("-IXz", 2), ("n'AADX", 0), ("'IYkw-UHLX", 0), ("t-UW", 2)),
    ">": (("-IXz", 2), ("gr'EYDX-ER", 0), ("DHEHn", 2)),
    ">=": (("-IXz", 2), ("gr'EYDX-ER", 0), ("DHEHn", 2), ("-OWr", 2), ("'IYkw-UHLX", 0), ("t-UW", 2)),
}

LETTER_PHONETICS = {
    "A": "'EY", "B": "b'IY", "C": "s'IY", "D": "d'IY", "E": "'IY",
    "F": "'EHf", "G": "dZH'IY", "H": "'EYtSH", "I": "'AY", "J": "dZH'EY",
    "K": "k'EY", "L": "'EHLX", "M": "'EHm", "N": "'EHn", "O": "'OW",
    "P": "p'IY", "Q": "ky'UW", "R": "'AAr", "S": "'EHs", "T": "t'IY",
    "U": "y'UW", "V": "v'IY", "W": "d'AHbUHLXy-UW", "X": "'EHks",
    "Y": "w'AY", "Z": "z'IY",
}

SMALL_NUMBERS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
SCALES = ("", "thousand", "million", "billion", "trillion", "quadrillion", "quintillion", "sextillion", "septillion", "octillion", "nonillion", "decillion")
ORDINALS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
    "twenty": "twentieth", "thirty": "thirtieth", "forty": "fortieth",
    "fifty": "fiftieth", "sixty": "sixtieth", "seventy": "seventieth",
    "eighty": "eightieth", "ninety": "ninetieth", "hundred": "hundredth",
    "thousand": "thousandth", "million": "millionth", "billion": "billionth",
}
MONTHS = ("", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")

# Ready-made numeric pronunciations from FB_SPCH's automatic data segment.
# FUN_1000_249e uses these instead of sending number words through the
# dictionary or RULE/HASH spelling fallback. This is why numeric 0 and the
# ordinary word "zero" are intentionally different.
NUMERIC_PHONETICS = {
    "zero": "z'IHrOW", "one": "w'AHn", "two": "t'UW", "three": "THr'IY",
    "four": "f'OWr", "five": "f'AYv", "six": "s'IHks", "seven": "s'EHvIXn",
    "eight": "'EYt", "nine": "n'AYn", "ten": "t'EHn",
    "eleven": "-IXl'EHvIXn", "twelve": "tw'EHLXv", "thirteen": "TH'ERtIYn",
    "fourteen": "f'OWrtIYn", "fifteen": "f'IHftIYn", "sixteen": "s'IHksTXIYn",
    "seventeen": "s'EHvIXntIYn", "eighteen": "'EYtIYn", "nineteen": "n'AYntIYn",
    "twenty": "tw'EHnIY", "thirty": "TH'ERDXIY", "forty": "f'OWrDXIY",
    "fifty": "f'IHfdIY", "sixty": "s'IHksTXIY", "seventy": "s'EHvIXndIY",
    "eighty": "'EYDXIY", "ninety": "n'AYndIY", "hundred": "h'AHndZHrIXd",
    "thousand": "TH'AWzIXn", "million": "m'IHLXyIXn", "billion": "b'IHLXyIXn",
    "trillion": "tr'IHLXyIXn", "quadrillion": "kwAAdZHr'IHLXyIXn",
    "quintillion": "kwIHnt'IHLXyIXn", "sextillion": "sEHkst'IHLXyIXn",
    "septillion": "sEHpt'IHLXyIXn", "octillion": "AAkt'IHLXyIXn",
    "nonillion": "nOWn'IHLXyIXn", "decillion": "dIXs'IHLXyIXn",
    "zeroth": "z'IHrOWIXTH", "first": "f'ERst", "second": "s'EHKX-IXnd",
    "third": "TH'ERd", "fourth": "f'OWrTH", "fifth": "f'IHfTH",
    "sixth": "s'IHksTH", "seventh": "s'EHvIXnTH", "eighth": "'EYtTH",
    "ninth": "n'AYnTH", "tenth": "t'EHnTH", "eleventh": "-IXl'EHvIXnTH",
    "twelfth": "tw'EHLXvTH", "thirteenth": "TH'ERtIYnTH",
    "fourteenth": "f'OWrtIYnTH", "fifteenth": "f'IHftIYnTH",
    "sixteenth": "s'IHksTXIYnTH", "seventeenth": "s'EHvIXntIYnTH",
    "eighteenth": "'EYtIYnTH", "nineteenth": "n'AYntIYnTH",
    "twentieth": "tw'EHnIYTH", "thirtieth": "TH'ERDXIYTH",
    "fortieth": "f'OWrDXIYTH", "fiftieth": "f'IHfdIYTH",
    "sixtieth": "s'IHksTXIYTH", "seventieth": "s'EHvIXndIYTH",
    "eightieth": "'EYDXIYTH", "ninetieth": "n'AYndIYTH",
    "hundredth": "h'AHndZHrIXdTH", "thousandth": "TH'AWzIXnTH",
    "millionth": "m'IHLXyIXnTH", "billionth": "b'IHLXyIXnTH",
}

MONTH_PHONETICS = {
    "january": "dZH'AEny[UWEHrIY", "february": "f'EHby[UWEHrIY",
    "march": "m'AArtSH", "april": "'EYpr-UHLX", "may": "m'EY",
    "june": "dZH'UWn", "july": "dZH[UHl'AY", "august": "'AAgIXst",
    "september": "s-EHpt'EHmbER", "october": "-AAkt'OWbER",
    "november": "n-OWv'EHmbER", "december": "d-IXs'EHmbER",
}

CONTEXT_ABBREVIATIONS = {
    "DR": "dZHr'AYv", "ST": "sTXr'IYt", "MT": "m'AWntn",
}

CONSONANT_PHONETICS = (
    "l", "m", "n", "NG", "y", "r", "w", "b", "d", "g", "v", "DH", "z",
    "ZH", "f", "TH", "s", "SH", "h", "p", "PX", "t", "TX", "DX", "k", "KX",
)
VOWEL_PREFIXES = ("'", '"', "-", "A", "E", "I", "O", "U", "y", "r", "l", "w")
# FB_SPCH!FUN_1000_4834's complete boundary rewrite table. Each tuple is
# (left suffixes, matched suffix, required category, right prefixes, replacement).
BOUNDARY_REWRITES = (
    (("DH-",), "IY", 2, CONSONANT_PHONETICS, "AX"),
    (("t-",), "UW", 2, CONSONANT_PHONETICS, "UH"),
    (("A",), "A", None, ("r",), "A"),
    (("O",), "W", None, ("r",), "W"),
    (("s",), "p", None, VOWEL_PREFIXES, "PX"),
    (("s",), "t", None, VOWEL_PREFIXES, "TX"),
    (("s",), "k", None, VOWEL_PREFIXES, "KX"),
    (("AA", "AE", "AH", "AX", "AW", "AY", "EH", "ER", "EY", "IH", "IX", "IY", "OW", "OY", "UH", "UW", "r"), "d", None, ("'", '"', "-", "A", "E", "I", "O", "U"), "DX"),
    (("AA", "AE", "AH", "AX", "AW", "AY", "EH", "ER", "EY", "IH", "IX", "IY", "OW", "OY", "UH", "UW", "r"), "t", None, ("'", '"', "-", "A", "E", "I", "O", "U"), "DX"),
    (("AA", "AE", "AH", "AX", "AW", "AY", "EH", "ER", "EY", "IH", "IX", "IY", "OW", "OY", "UH", "UW", "r", "LX"), "k", None, VOWEL_PREFIXES, "KX"),
)

# The original post-pass (FUN_1000_3f6c) gives terminal punctuation different
# contours.  These recovered phonetic controls attach that distinction to the
# final word rather than reducing every mark to silence.
TERMINAL_PUNCTUATION = {".": 2, "?": 2, "!": 2, ";": 2, ":": 2}

PHONEME_RE = re.compile(
    "|".join(re.escape(name) for name in sorted((*PHONEMES, "IX"), key=len, reverse=True))
)


class UnknownWordError(ValueError):
    def __init__(self, words: list[str]):
        self.words = words
        super().__init__("no dictionary pronunciation for: " + ", ".join(words))


def _under_thousand(value: int) -> list[str]:
    words: list[str] = []
    if value >= 100:
        words.extend((SMALL_NUMBERS[value // 100], "hundred"))
        value %= 100
    if value >= 20:
        words.append(TENS[value // 10])
        value %= 10
    if value:
        words.append(SMALL_NUMBERS[value])
    return words


def integer_words(digits: str, *, year: bool = False) -> list[str]:
    digits = digits.replace(",", "")
    if len(digits) > 1 and digits[0] == "0":
        return [SMALL_NUMBERS[int(char)] for char in digits]
    value = int(digits or "0")
    if value == 0:
        return ["zero"]
    if year and len(digits) == 4 and 1000 <= value <= 2099:
        first, second = divmod(value, 100)
        if second == 0:
            return _under_thousand(first) + ["hundred"]
        if value < 2000:
            return _under_thousand(first) + _under_thousand(second)
        if value < 2010:
            return ["two", "thousand"] + ([] if second == 0 else ["and"] + _under_thousand(second))
        return ["twenty"] + _under_thousand(second)
    groups: list[int] = []
    while value:
        value, group = divmod(value, 1000)
        groups.append(group)
    if len(groups) > len(SCALES):
        return [SMALL_NUMBERS[int(char)] for char in digits]
    words: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        if groups[index]:
            words.extend(_under_thousand(groups[index]))
            if SCALES[index]:
                words.append(SCALES[index])
    return words


def ordinal_words(digits: str) -> list[str]:
    words = integer_words(digits)
    last = words[-1]
    words[-1] = ORDINALS.get(last, last + "th")
    return words


@dataclass(frozen=True)
class RenderedAudio:
    pcm: bytes
    sample_rate: int
    sample_width: int
    channels: int = 1

    @property
    def frame_count(self) -> int:
        return len(self.pcm) // (self.sample_width * self.channels)


class MonologEngine:
    """Loads the original data once and renders any number of utterances."""

    def __init__(self, voice: Path, dictionary: Path, community_dictionary: Path | None = None,
                 user_dictionary: Path | None = None, cmu_dictionary: Path | None = None):
        self.voice_path = Path(voice)
        self.dictionary_path = Path(dictionary)
        self.manifest, self.pcmd = inspect_voice(self.voice_path)
        if (self.manifest["sample_rate"], self.manifest["bits_per_sample"]) not in {
            (22050, 16), (11025, 8),
        }:
            raise ValueError("unsupported Monolog PCM voice format")
        parsed = parse_dictionary(self.dictionary_path)
        self.dictionary = {
            entry["spelling"].upper(): entry["phonetics"]
            for entry in parsed["entries"]
            if entry["active"]
        }
        self.rule_pronouncer = RulePronouncer(self.voice_path)
        self.community_overrides: dict[str, str] = {}
        self.community_fallbacks: dict[str, str] = {}
        self.community_expansions: dict[str, str] = {}
        if community_dictionary is not None and Path(community_dictionary).is_file():
            for line in Path(community_dictionary).read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                spelling, kind, tagged_value = line.split("\t", 2)
                priority, value = tagged_value.split(":", 1)
                if kind == "text":
                    self.community_expansions[spelling] = value
                elif priority == "override":
                    self.community_overrides[spelling.casefold()] = value
                else:
                    self.community_fallbacks[spelling.casefold()] = value
        if self.community_expansions:
            alternatives = sorted(self.community_expansions, key=len, reverse=True)
            self._community_expansion_re = re.compile(
                r"(?<![\w'])" + "(" + "|".join(re.escape(item) for item in alternatives) + r")(?![\w'])",
            )
        else:
            self._community_expansion_re = None
        if cmu_dictionary is not None and Path(cmu_dictionary).is_file():
            for line in Path(cmu_dictionary).read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                spelling, phonetics = line.split("\t", 1)
                key = spelling.casefold()
                if key not in CMU_FUNCTION_WORDS:
                    self.community_fallbacks.setdefault(key, phonetics)
        self.user_dictionary_path = Path(user_dictionary) if user_dictionary is not None else None
        self.user_phonetics: dict[str, str] = {}
        if user_dictionary is not None and Path(user_dictionary).is_file():
            for line_number, line in enumerate(
                    Path(user_dictionary).read_text(encoding="utf-8-sig").splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    spelling, phonetics = line.split("\t", 1)
                except ValueError as error:
                    raise ValueError(
                        f"invalid user dictionary line {line_number}: expected spelling<TAB>phonetics"
                    ) from error
                self.user_phonetics[spelling.casefold()] = phonetics.strip()
        if self.user_phonetics:
            alternatives = sorted(self.user_phonetics, key=len, reverse=True)
            self._user_phonetics_re = re.compile(
                r"(?<![\w'])" + "(" + "|".join(re.escape(item) for item in alternatives) + r")(?![\w'])",
                re.IGNORECASE,
            )
        else:
            self._user_phonetics_re = None

    def _compile_user_phonetics(self) -> None:
        if not self.user_phonetics:
            self._user_phonetics_re = None
            return
        alternatives = sorted(self.user_phonetics, key=len, reverse=True)
        self._user_phonetics_re = re.compile(
            r"(?<![\w'])" + "(" + "|".join(re.escape(item) for item in alternatives) + r")(?![\w'])",
            re.IGNORECASE,
        )

    def set_user_phonetics(self, spelling: str, phonetics: str | None) -> None:
        spelling = spelling.strip()
        if not spelling:
            raise ValueError("spelling must not be empty")
        key = spelling.casefold()
        if phonetics is None:
            self.user_phonetics.pop(key, None)
        else:
            phonetics = phonetics.strip()
            if not phonetics:
                raise ValueError("phonetics must not be empty")
            parse_phonetics(phonetics)
            self.user_phonetics[key] = phonetics
        self._compile_user_phonetics()
        if self.user_dictionary_path is not None:
            self.user_dictionary_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# spelling<TAB>phonetics"]
            lines.extend(
                f"{word}\t{value}" for word, value in sorted(self.user_phonetics.items())
            )
            self.user_dictionary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def text_to_phonetics(self, text: str, *, use_community_dictionary: bool = False) -> str:
        if self._user_phonetics_re is not None:
            text = self._user_phonetics_re.sub(
                lambda match: f"<<~{self.user_phonetics[match.group(0).casefold()]}>>", text
            )
        if use_community_dictionary and self._community_expansion_re is not None:
            text = self._community_expansion_re.sub(
                lambda match: self.community_expansions[match.group(0)], text
            )
        pieces: list[str] = []
        unknown: list[str] = []
        need_separator = False
        last_word_piece: int | None = None
        sentence_words: list[tuple[int, int]] = []
        inline_settings = {"P": 5, "S": 5, "V": 5, "M": 5, "F": 5}
        inline_stack: list[dict[str, int]] = []

        def pronunciation(word: str) -> tuple[str, int] | None:
            if use_community_dictionary:
                result = self.community_overrides.get(word.casefold())
                if result is not None:
                    return result, 1
            result = self.dictionary.get(word.upper())
            if result is not None:
                return result, 1
            if use_community_dictionary:
                result = self.community_fallbacks.get(word.casefold())
                if result is not None:
                    return result, 1
            if len(word) > 2 and word.lower().endswith("'s"):
                base = word[:-2]
                base_result = self.dictionary.get(base.upper())
                category = 1
                if base_result is None:
                    try:
                        base_result, category = self.rule_pronouncer.pronounce_with_category(base)
                    except ValueError:
                        base_result = None
                if base_result is not None:
                    suffix = "-IXz" if base_result.endswith(("s", "SH")) else "z"
                    return base_result + suffix, category
            try:
                return self.rule_pronouncer.pronounce_with_category(word)
            except ValueError:
                unknown.append(word)
                return None

        def append_word(word: str) -> None:
            nonlocal need_separator, last_word_piece
            pronounced = pronunciation(word)
            if pronounced is None:
                return
            result, category = pronounced
            if need_separator:
                pieces.append("|")
            pieces.append(result)
            last_word_piece = len(pieces) - 1
            sentence_words.append((last_word_piece, category))
            need_separator = True

        def should_spell(word: str) -> bool:
            upper = word.upper()
            if upper in self.dictionary or not upper.isalpha():
                return False
            scan = upper[1:] if upper.startswith("Y") else upper
            has_vowel = any(char in "AEIOUY" for char in scan)
            offset = 0
            while offset < len(upper) and upper[offset] in "AEIOU":
                offset += 1
            pattern_word = False
            if len(upper) - offset >= 3:
                offset += 1
                vowels = 0
                while offset < len(upper) and upper[offset] in "AEIOUY":
                    vowels += 1
                    offset += 1
                pattern_word = vowels > 0 and offset < len(upper)
            # This is FUN_1000_2286 plus FUN_1000_2184: mixed/lower-case
            # tokens containing a vowel are words; otherwise a C-V-C-like
            # shape may still be pronounced, and the remainder is spelled.
            single_spelled = len(upper) == 1 and not (
                word.islower() and upper in "AEIOU"
            )
            return single_spelled or (
                len(upper) > 1 and
                (not any(char.islower() for char in word) or not has_vowel)
                and not pattern_word
            )

        def append_spelling(word: str) -> None:
            nonlocal need_separator, last_word_piece
            for char in word.upper():
                if char in LETTER_PHONETICS:
                    if need_separator:
                        pieces.append("|")
                    pieces.append(LETTER_PHONETICS[char])
                    last_word_piece = len(pieces) - 1
                    sentence_words.append((last_word_piece, 1))
                    need_separator = True

        def append_words(words: list[str] | tuple[str, ...]) -> None:
            for word in words:
                append_word(word)

        def append_fixed(result: str, category: int = 0) -> None:
            nonlocal need_separator, last_word_piece
            if need_separator:
                pieces.append("|")
            pieces.append(result)
            last_word_piece = len(pieces) - 1
            sentence_words.append((last_word_piece, category))
            need_separator = True

        def append_numeric_words(words: list[str] | tuple[str, ...]) -> None:
            """Append FUN_1000_249e's numeric phonetics where available."""
            nonlocal need_separator, last_word_piece
            for word in words:
                result = NUMERIC_PHONETICS.get(word)
                if result is None:
                    append_word(word)
                    continue
                append_fixed(result)

        def append_number(token: str) -> None:
            currency = token[0] if token[:1] in "$£" else ""
            if currency:
                token = token[1:]
            sign = token[0] if token[:1] in "+-" else ""
            if sign:
                append_fixed("pl'AHs" if sign == "+" else "m'AYnIXs", 2)
                token = token[1:]
            ordinal = re.search(r"(?i)(st|nd|rd|th)$", token)
            if ordinal:
                token = token[:-2]
            integer, dot, fraction = token.replace(",", "").partition(".")
            if len(integer) > 1 and integer.startswith("0"):
                append_numeric_words([SMALL_NUMBERS[int(char)] for char in integer])
                if dot:
                    append_word("point")
                    append_numeric_words([SMALL_NUMBERS[int(char)] for char in fraction])
                return
            words = (
                ordinal_words(integer) if ordinal else
                integer_words(integer, year=len(integer) == 4 and integer.startswith("19"))
            )
            append_numeric_words(words)
            if currency:
                unit = "dollar" if currency == "$" else "pound"
                plural = int(integer or "0") != 1
                if currency == "$":
                    append_fixed("d'AAlER" + ("z" if plural else ""))
                else:
                    if plural:
                        unit += "s"
                    append_word(unit)
                if dot and fraction and int(fraction):
                    append_fixed("-AEn", 2)
                    cents = fraction[:2].ljust(2, "0")
                    append_numeric_words(integer_words(cents))
                    if currency == "$":
                        append_fixed("s'EHnt" + ("" if int(cents) == 1 else "s"))
                    else:
                        append_word("cent" if int(cents) == 1 else "cents")
            elif dot:
                append_fixed("pOYnt", 2)
                append_numeric_words([SMALL_NUMBERS[int(char)] for char in fraction])

        def append_date(token: str) -> None:
            month_text, day_text, year_text = re.split(r"[/-]", token)
            month, day = int(month_text), int(day_text)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                append_spelling(token)
                return
            append_fixed(MONTH_PHONETICS[MONTHS[month]])
            append_numeric_words(ordinal_words(day_text))
            append_numeric_words(integer_words(year_text, year=True))

        def append_raw(command: str) -> None:
            nonlocal need_separator
            if need_separator:
                pieces.append("|")
            pieces.append(command)
            need_separator = True

        def append_delay(units: int) -> None:
            nonlocal need_separator
            pieces.append(f"D{units}")
            need_separator = False

        def finish_sentence(mark: str | None) -> None:
            """Port FB_SPCH!FUN_1000_3f6c's word-target post-pass."""
            nonlocal sentence_words, last_word_piece
            count = len(sentence_words)

            # FUN_1000_4b52 runs at each @category word marker before the
            # marker is converted to a numeric contour target.
            for index in range(count - 1):
                piece_index, category = sentence_words[index]
                next_piece_index, _ = sentence_words[index + 1]
                between = pieces[piece_index + 1:next_piece_index]
                if any(item != "|" for item in between):
                    continue
                current_text = pieces[piece_index]
                following_text = pieces[next_piece_index]
                for lefts, matched, required_category, rights, replacement in BOUNDARY_REWRITES:
                    if required_category is not None and category != required_category:
                        continue
                    if not current_text.endswith(matched):
                        continue
                    before = current_text[:-len(matched)]
                    if not any(before.endswith(left) for left in lefts):
                        continue
                    if not any(following_text.startswith(right) for right in rights):
                        continue
                    pieces[piece_index] = before + replacement
                    break

            carry = 0
            targets: list[int] = []
            excursion = 20 if mark == "!" else 10
            for index, (_, category) in enumerate(sentence_words):
                previous_carry = carry
                carry = 0
                if index + 1 < count and category in (2, 3, 4):
                    following = sentence_words[index + 1][1]
                    carry = -excursion if following in (2, 3, 4) else excursion
                targets.append(50 - ((index + 1) * 40 // count) + previous_carry)
            if targets and mark != "!":
                if mark == ".":
                    targets[-1] = 0
                    if count > 2:
                        targets[-2] = 10
                elif mark == ";":
                    targets[-1] = 20
                elif mark == "?":
                    targets[-1] = 90
                    if count > 1:
                        targets[-2] = 70
            for (piece_index, _), target in zip(sentence_words, targets):
                pieces[piece_index] += str(target)
            sentence_words = []
            last_word_piece = None

        tokens = TOKEN_RE.findall(text)
        for token_index, token in enumerate(tokens):
            if token.startswith("<<~") and token.endswith(">>"):
                append_raw(token[3:-2])
                continue
            if token.startswith("<<"):
                inline_stack.append(inline_settings.copy())
                command = token[2:].upper()
                if len(command) == 2 and command[0] in inline_settings and command[1].isdigit():
                    inline_settings[command[0]] = int(command[1])
                    pieces.append(command)
                continue
            if token == ">>":
                if inline_stack:
                    previous_settings = inline_stack.pop()
                    for name in ("P", "S", "V", "M", "F"):
                        if previous_settings[name] != inline_settings[name]:
                            pieces.append(f"{name}{previous_settings[name]}")
                    inline_settings = previous_settings
                continue
            if token.endswith(".") and token not in (".", "..", "..."):
                base = token[:-1]
                if (base.upper() in CONTEXT_ABBREVIATIONS and token_index > 0
                        and tokens[token_index - 1][0].isalnum()):
                    append_fixed(CONTEXT_ABBREVIATIONS[base.upper()])
                elif token.upper() in self.dictionary:
                    append_word(token)
                else:
                    if should_spell(base):
                        append_spelling(base)
                    else:
                        append_word(base)
                finish_sentence(".")
                append_delay(2)
                continue
            if token in TERMINAL_PUNCTUATION:
                finish_sentence(token)
                append_delay(TERMINAL_PUNCTUATION[token])
                continue
            if token.startswith(".."):
                finish_sentence(".")
                append_delay(2)
                continue
            if token == ",":
                append_delay(1)
                continue
            if (token == "-" and token_index and token_index + 1 < len(tokens)
                    and tokens[token_index - 1][0].isalnum()
                    and tokens[token_index + 1][0].isalnum()):
                # A hyphen inside a compound is a word boundary, not "minus".
                continue
            if token in "()\"":
                append_delay(1)
                continue
            fixed_spoken = SPOKEN_PHONETICS.get(token)
            if fixed_spoken is not None:
                for result, category in fixed_spoken:
                    append_fixed(result, category)
                continue
            spoken = SPOKEN_PUNCTUATION.get(token)
            if spoken is not None:
                for word in spoken:
                    append_word(word)
                continue
            if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", token):
                append_date(token)
                continue
            if re.fullmatch(r"(?i)[$£]?[+-]?\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?", token):
                append_number(token)
                continue
            if not token[0].isalnum():
                continue
            if (token.upper().rstrip(".") in CONTEXT_ABBREVIATIONS and token_index > 0
                    and tokens[token_index - 1][0].isalnum()):
                append_fixed(CONTEXT_ABBREVIATIONS[token.upper().rstrip(".")])
                continue
            if should_spell(token):
                append_spelling(token)
            else:
                append_word(token)
        finish_sentence(None)
        if unknown:
            raise UnknownWordError(unknown)
        return "".join(pieces)

    def render_phonetics(
        self,
        phonetics: str,
        *,
        pitch: int = 5,
        speed: int = 5,
        excitation: int = 50,
        unit_compression: int = 0,
        compression_method: str = "centre",
        pause_compression: int = 0,
    ) -> RenderedAudio:
        events = scheduled_events(self.manifest, phonetics)
        pcm = _scheduled_frames(
            self.manifest,
            self.pcmd,
            events,
            pitch=max(0, min(9, pitch)),
            speed=max(0, min(24, speed)),
            excitation=max(0, min(100, excitation)),
            unit_compression=max(0, min(50, unit_compression)),
            compression_method=compression_method,
            pause_compression=max(0, min(90, pause_compression)),
        )
        return RenderedAudio(pcm, self.manifest["sample_rate"], self.manifest["bits_per_sample"] // 8)

    def render_text(self, text: str, *, pitch: int = 5, speed: int = 5) -> RenderedAudio:
        return self.render_phonetics(self.text_to_phonetics(text), pitch=pitch, speed=speed)

    def spell_to_phonetics(self, text: str, *, use_community_dictionary: bool = False) -> str:
        """Speak each non-space character through the original front end."""
        return self.text_to_phonetics(
            " ".join(char for char in text if not char.isspace()),
            use_community_dictionary=use_community_dictionary,
        )

    @staticmethod
    def write_wav(audio: RenderedAudio, output: Path) -> None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(audio.channels)
            stream.setsampwidth(audio.sample_width)
            stream.setframerate(audio.sample_rate)
            stream.writeframes(audio.pcm)
