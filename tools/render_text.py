#!/usr/bin/env python3
"""Render dictionary-backed multiword text with the persistent Python engine."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from tools.engine import MonologEngine, UnknownWordError
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.engine import MonologEngine, UnknownWordError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--voice", type=Path, default=Path("monologue16/FB_22K16.DLL"))
    parser.add_argument("--dictionary", type=Path, default=Path("monologue16/FB_DEFLT.DIC"))
    parser.add_argument("--pitch", type=int, choices=range(10), default=5)
    parser.add_argument("--speed", type=int, choices=range(10), default=5)
    args = parser.parse_args()
    engine = MonologEngine(args.voice, args.dictionary)
    try:
        phonetics = engine.text_to_phonetics(args.text)
        audio = engine.render_phonetics(phonetics, pitch=args.pitch, speed=args.speed)
    except UnknownWordError as error:
        parser.error(str(error))
    engine.write_wav(audio, args.output)
    print(f"phonetics: {phonetics}")
    print(f"wrote {args.output}: {audio.frame_count} frames ({audio.frame_count / audio.sample_rate:.3f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
