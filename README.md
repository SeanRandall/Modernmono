# ModernMono

Reverse-engineering workspace for a native reimplementation of the Monolog
Win16 speech synthesizer.

The original binaries remain in `monologue16/`. Generated inventories and
decoded artifacts are written under `analysis/`; reproducible parsers live in
`tools/`.

## Current executable milestone

The tooling can now:

- parse NE headers and extract resources without running Win16 code;
- decode all 891 entries in `FB_DEFLT.DIC`;
- parse the phonetic command language accepted by `FB_NGN`;
- decode both voices' metadata, transition matrices, and `INST` unit records;
- resolve every synthesis unit to its precise `PCMD` byte range;
- pronounce words missing from the dictionary with the original `HASH`/`RULE`
  spelling rules;
- produce a diagnostic WAV from the raw units selected for a phonetic string.

Run the tests:

```powershell
python -m unittest discover -s tests -v
```

Render a diagnostic 22 kHz unit sequence:

```powershell
python tools\render_units.py monologue16\FB_22K16.DLL "hEHl'OW" analysis\voice\hello_raw_units.wav
```

Render with the recovered default scheduler and boundary interpolation:

```powershell
python tools\render_units.py monologue16\FB_22K16.DLL "hEHl'OW" analysis\voice\hello_scheduled.wav --mode scheduled
```

External pitch and speed settings use the original 0–9 range:

```powershell
python tools\render_units.py monologue16\FB_22K16.DLL "hEHl'OW" output.wav --mode scheduled --pitch 5 --speed 5
```

Render the standard dictionary comparison corpus:

```powershell
python tools\render_corpus.py monologue16\FB_22K16.DLL monologue16\FB_DEFLT.DIC analysis\corpus\22k
```

Render continuous dictionary-backed text:

```powershell
python tools\render_text.py "absence, actually another dictionary." analysis\voice\phrase.wav
```

Or type and hear lines interactively on Windows:

```powershell
python tools\console_speak.py
```

The console accepts inline phonetic commands in double brackets, for example
`Hello [[P8]]world`, `fast [[S3]]slow`, or `wait [[D2]]then continue`.
Ordinary punctuation is interpreted automatically: statements fall, questions
rise, exclamations receive an emphatic fall, and commas, semicolons, colons,
parentheses, quotes, and ellipses insert their recovered pauses. Symbols and
comparison operators including `#`, `%`, `&`, `*`, `@`, `^`, `=`, `<`, `<=`,
`<>`, `>`, `>=`, and `:=` are spoken rather than discarded.

The recovered text front end also expands integers, years, ordinals, decimals,
currency and numeric dates; recognizes supplied abbreviations; spells short
vowelless tokens such as `cm`; and accepts scoped original controls such as
`<<P8 higher text>>` and raw phonetics such as `<<~hEHl'OW>>`. Use `/spell` in
the console for character-by-character speech.

Words absent from `FB_DEFLT.DIC` now fall back to the original `RULE`/`HASH`
resources embedded in the selected voice. Tokens outside that rule system
(for example mixed alphanumeric identifiers) are still reported explicitly.
See [`analysis/PYTHON_NVDA_PLAN.md`](analysis/PYTHON_NVDA_PLAN.md).

The scheduled mode ports unit resizing, pitch-period/repeat scheduling, tail
crossfades, voiced-boundary interpolation, and per-unit stress and intonation
contours. Exact interactions between overlapping stress, sentence intonation,
speed, pitch, and the optional excitation scaling are still being refined.

See [`analysis/ENGINE_MAP.md`](analysis/ENGINE_MAP.md) for the recovered model.
