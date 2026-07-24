# Python-only NVDA path

## Decision

A native C/C++ port is not required for the current engine. A persistent
`MonologEngine` loads the 22 kHz voice and dictionary in roughly 7 ms on the
development machine, and renders the four-word test phrase in roughly 5 ms.
That is comfortably faster than real-time and suitable for an NVDA worker
thread.

## Working now

- persistent 22,050 Hz/16-bit engine state;
- continuous synthesis across multiple dictionary words;
- recovered spelling-rule fallback for words absent from the dictionary;
- punctuation pauses, sentence-final contours, and spoken operators/symbols;
- pitch, speed, volume, delay, stress, and intonation events;
- raw PCM returned in memory without temporary WAV files;
- deterministic cancellation boundaries can be introduced between fed chunks.

## Implemented experimental add-on

`addon/` now contains a self-contained synth driver and engine snapshot. It
provides an asynchronous worker, queued playback, deterministic cancellation,
pause/resume, index and completion notifications, character mode, breaks, and
mid-utterance rate, pitch, and volume changes. `tools/build_nvda_addon.py`
produces an installable package in `dist/`.

The package has been exercised outside NVDA with the engine test suite. It
still needs an interactive test in a real NVDA process before being described
as generally useful.

## Remaining compatibility work

### 1. Complete specialised text forms

The supplied dictionary and original `RULE`/`HASH` fallback are ported, along
with punctuation, operators, abbreviations, acronym/spelling heuristics,
integers, years, ordinals, decimals, currency, numeric dates, character mode,
and scoped `<<...>>` controls. Further oracle comparison is still appropriate
for ambiguous date formats and specialised legacy numeric conventions.

### 2. Streaming speech worker (implemented)

NVDA calls `speak` without waiting for playback. The driver therefore needs a
worker thread which:

1. consumes speech sequences;
2. converts text to phonetics;
3. renders bounded PCM chunks;
4. feeds them to `nvwave.WavePlayer`;
5. abandons queued work immediately when `cancel` changes a generation token.

The renderer itself can remain ordinary Python. Chunking should occur at word
or index boundaries, not inside a synthesis unit.

### 3. NVDA commands and notifications (implemented)

At minimum the driver must support `IndexCommand` and provide both
`synthIndexReached` and `synthDoneSpeaking`. Rate, pitch, and volume map from
NVDA's 0–100 settings to Monolog's 0–9 range. `pause` maps directly to
`WavePlayer.pause`; `cancel` maps to `WavePlayer.stop` and clears pending work.

Useful later commands include `BreakCommand`, `RateCommand`, `PitchCommand`,
`VolumeCommand`, and character mode.

### 4. Add-on packaging (implemented for local testing)

The add-on should contain:

```text
addon/
    manifest.ini
    synthDrivers/modernmono.py
    synthDrivers/_modernmono/ # bundled engine modules
    data/FB_22K16.DLL
    data/FB_DEFLT.DIC
```

The original voice and dictionary redistribution rights must be established
before public distribution.

## Current NVDA contract

Current NVDA requires `IndexCommand` support and both index/done notifications
from synthesizer drivers. Its `nvwave.WavePlayer` accepts PCM bytes through
`feed(data, onDone=...)`, and exposes `stop`, `pause`, and `idle`. This matches
the Python engine without an extension module.
