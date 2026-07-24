#!/usr/bin/env python3
"""Interactive text-to-speech console for the pure-Python Monolog engine."""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import winsound
except ImportError:  # pragma: no cover - this workspace targets Windows
    winsound = None

try:
    from tools.engine import MonologEngine, UnknownWordError
    from tools.phonetics import parse
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.engine import MonologEngine, UnknownWordError
    from tools.phonetics import parse


ROOT = Path(__file__).resolve().parents[1]
INLINE = re.compile(r"\[\[(.*?)\]\]")
HELP = r"""
Type ordinary text and press Enter. Inline phonetic commands go in [[...]]:
  Hello [[P8]]world.       pitch 8 from "world" onward
  Fast [[S8]]then [[S3]]slow.
  Wait [[D2]]and continue. delay 2 timing units (about half a second)
  Quiet [[V2]]voice.       volume 2 (5 is normal)

Punctuation is interpreted automatically: questions rise, statements and
exclamations fall, commas/semicolons pause, and symbols such as #, %, &, *,
@, ^, =, <, <=, <>, >, >= and := are spoken using the original vocabulary.
Original controls are also accepted: <<P8 high pitch>> restores the previous
setting at >>; S, V, M and F work the same way. <<~hEHl'OW>> inserts raw
phonetics. The [[P8]] form remains available for a non-restoring inline change.

Console commands:
  /pitch 0..9    set the starting pitch (default 5)
  /speed 0..9    set the starting speed (default 5)
  /phonetics     treat whole lines as raw phonetics
  /spell         speak lines character by character
  /text          return to ordinary text mode
  /help          show this help
  /quit          exit
"""


def text_with_inline_commands(engine: MonologEngine, text: str) -> str:
    """Translate text while copying [[phonetic commands]] into the result."""
    pieces: list[str] = []
    spoken = False
    offset = 0
    for match in INLINE.finditer(text):
        chunk = text[offset:match.start()]
        if chunk.strip():
            if spoken:
                pieces.append("|")
            pieces.append(engine.text_to_phonetics(chunk))
            spoken = True
        command = match.group(1).strip()
        if not command:
            raise ValueError("empty inline command")
        parse(command)
        pieces.append(command)
        offset = match.end()
    chunk = text[offset:]
    if chunk.strip():
        if spoken:
            pieces.append("|")
        pieces.append(engine.text_to_phonetics(chunk))
    result = "".join(pieces)
    if not result:
        raise ValueError("nothing to speak")
    parse(result)
    return result


def main() -> int:
    voice = ROOT / "monologue16" / "FB_22K16.DLL"
    dictionary = ROOT / "monologue16" / "FB_DEFLT.DIC"
    preview = ROOT / "analysis" / "voice" / "console_preview.wav"
    engine = MonologEngine(voice, dictionary)
    pitch = speed = 5
    mode = "text"
    print("Monolog console is ready. Type /help for commands; /quit to exit.")
    while True:
        try:
            line = input(f"{mode} P{pitch} S{speed}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/phonetics":
            mode = "phonetics"
            continue
        if line == "/spell":
            mode = "spell"
            continue
        if line == "/text":
            mode = "text"
            continue
        setting = re.fullmatch(r"/(pitch|speed)\s+([0-9])", line)
        if setting:
            if setting.group(1) == "pitch":
                pitch = int(setting.group(2))
            else:
                speed = int(setting.group(2))
            continue
        if line.startswith("/"):
            print("Unknown console command. Type /help.")
            continue
        try:
            if mode == "phonetics":
                phonetics = line
            elif mode == "spell":
                phonetics = engine.spell_to_phonetics(line)
            else:
                phonetics = text_with_inline_commands(engine, line)
            parse(phonetics)
            audio = engine.render_phonetics(phonetics, pitch=pitch, speed=speed)
            engine.write_wav(audio, preview)
            print(f"  {phonetics}")
            if winsound is None:
                print(f"  wrote {preview}")
            else:
                winsound.PlaySound(str(preview), winsound.SND_FILENAME)
        except (UnknownWordError, ValueError) as error:
            print(f"  error: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
