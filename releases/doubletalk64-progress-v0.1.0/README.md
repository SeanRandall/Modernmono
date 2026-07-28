# DoubleTalk 64 progress build 0.1.0

DoubleTalk 64 is an experimental, CPU-emulator-free NVDA reconstruction of
the RC Systems DoubleTalk PC voice. This snapshot is a work in progress rather
than a finished reproduction: ordinary speech is usable, but pronunciation,
prosody, contextual allophones and voice presets are still being matched to
the original firmware.

## Installation

Open `doubletalk64.nvda-addon` with NVDA, approve the installation, restart
NVDA, and select **DoubleTalk 64 (native)** as the synthesizer.

The settings ring exposes voice, rate, pitch, articulation, expression,
formant frequency, tone, reverb and volume. The rate control includes a native
speed boost which is kept separate from pitch.

## Phoneme support

The native engine accepts phonetic pronunciations. In the current addon this
is exposed through its editable user dictionary; the driver does **not** yet
advertise NVDA's `PhonemeCommand` as a supported speech command.

The dictionary is created at:

```text
%APPDATA%\nvda\doubletalk64-user-dictionary.tsv
```

Each non-comment line is `spelling<TAB>pronunciation`. User entries override
the built-in and ROM dictionaries. For example:

```text
DOUBLE TALK	D AH B AX L T AO K
FREQUENCY	fr'IYkwAXns-IY
```

Two notations are accepted:

- Readable, space-separated phonemes such as `DH AX`, `B EY B IY`, and
  `F R IY K W AX N S IY`.
- Compact, case-sensitive notation used by the reconstructed dictionary, such
  as `DHAX`, `b'EYb-IY`, and `fr'IYkwAXns-IY`.

Readable phoneme names currently include vowels `AA AE AH AW AX AY EH ER EY
IH IX IY OW OY UH UW` and consonants/allophones `B CH D DH DX F G H J K KX L
M N NG P PX R RR S SH T TH TX V W WH Y Z ZH`.

Compact pronunciations can also contain stress and contour controls: `'` for
primary stress, `"` for secondary stress, `-` for a fall, `/` for a rise, `\`
for a descent, and `|` for a separator. Numeric duration and the reconstructed
`F`, `M`, `S`, `V`, `P`, and `D` controls are parsed internally, but they are
still experimental and are best left to dictionary development.

After editing the dictionary, reload the synthesizer or restart NVDA.

## Current implementation

- Runs natively in Python without emulating the DoubleTalk PC processor.
- Uses the original ROM voice data and recovered transition tables.
- Reads both ROM pronunciation lexicons and supports user overrides.
- Includes eight reconstructed voice presets and contextual stop/allophone
  selection.
- Contains an experimental decoder for the ROM's 912 spelling rules. It is
  intentionally not the live fallback yet because the firmware's surrounding
  preprocessing and rewrite stages are still being recovered.

